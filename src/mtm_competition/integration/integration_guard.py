"""IntegrationGuard: Integrates validated worker patches into the canonical submission repository."""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import WorkerResult
from mtm_core.git import git_diff, git_status, run_git, get_head_sha


@dataclass
class IntegrationResult:
    success: bool
    commit_sha: Optional[str] = None
    applied_files: List[str] = None
    error_message: Optional[str] = None
    pushed: bool = False
    verification_passed: bool = True


class IntegrationGuard:
    """Safely stages, applies, verifies, commits, and pushes worker patches onto the submission branch."""

    def __init__(
        self,
        target_repo_path: Path | str,
        push_to_remote: bool = True,
        remote_name: str = "origin",
        verify_post_integration: bool = True,
        require_remote: bool = False,
    ):
        self.target_repo = Path(target_repo_path).resolve()
        self.push_to_remote = push_to_remote
        self.remote_name = remote_name
        self.verify_post_integration = verify_post_integration
        self.require_remote = require_remote

    def has_remote(self, remote: Optional[str] = None) -> bool:
        """Check if remote is configured."""
        r_name = remote or self.remote_name
        res = run_git(self.target_repo, ["remote"])
        return r_name in res.stdout.split()

    def verify_remote_sha(self, branch: str, expected_sha: str) -> bool:
        """Directly verify on the remote via git ls-remote that the ref matches expected commit."""
        ls_res = run_git(self.target_repo, ["ls-remote", self.remote_name, f"refs/heads/{branch}"])
        if ls_res.returncode != 0 or not ls_res.stdout.strip():
            # Fallback to general branch query
            ls_res = run_git(self.target_repo, ["ls-remote", self.remote_name, branch])
            if ls_res.returncode != 0 or not ls_res.stdout.strip():
                return False
        remote_sha = ls_res.stdout.split()[0].strip()
        return remote_sha.startswith(expected_sha) or expected_sha.startswith(remote_sha)

    def integrate(
        self,
        worker_result: WorkerResult,
        worker_workspace: Path | str,
        commit_message: Optional[str] = None,
        verification_cmd: Optional[str] = None,
    ) -> IntegrationResult:
        ws = Path(worker_workspace).resolve()

        # Production check: if require_remote is True, push_to_remote must be True and remote must exist
        if self.require_remote:
            if not self.push_to_remote:
                return IntegrationResult(
                    success=False,
                    error_message="Production mode mandates push_to_remote=True; local-only submission is disallowed",
                )
            if not self.has_remote(self.remote_name):
                return IntegrationResult(
                    success=False,
                    error_message=f"Git push required in production but remote '{self.remote_name}' is not configured",
                )

        # Extract patch from isolated worker workspace
        patch_text = git_diff(ws, base="HEAD", stage_all=True)
        if not patch_text and not worker_result.changed_files:
            return IntegrationResult(
                success=False,
                error_message="Worker reported changes but diff is empty",
            )

        # Validate and apply patch files safely
        applied_files: List[str] = []
        newly_created_files: List[Path] = []
        pre_existing_files: Dict[str, bytes] = {}

        for rel_file in worker_result.changed_files:
            rel_path = Path(rel_file)
            if rel_path.is_absolute() or ".." in rel_path.parts:
                return IntegrationResult(
                    success=False,
                    error_message=f"Invalid path traversal in worker result: {rel_file}",
                )

            src_file = ws / rel_file
            if src_file.is_symlink():
                return IntegrationResult(
                    success=False,
                    error_message=f"Symlinks are disallowed in worker patches: {rel_file}",
                )

            dest_file = (self.target_repo / rel_file).resolve()
            try:
                dest_file.relative_to(self.target_repo)
            except (ValueError, RuntimeError):
                return IntegrationResult(
                    success=False,
                    error_message=f"Path escape detected: {rel_file} is outside canonical repository",
                )

            if dest_file.exists():
                pre_existing_files[rel_file] = dest_file.read_bytes()

            if src_file.exists():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(src_file.read_bytes())
                applied_files.append(rel_file)
                if rel_file not in pre_existing_files:
                    newly_created_files.append(dest_file)
            elif not src_file.exists() and dest_file.exists():
                # Handled deletion
                dest_file.unlink()
                applied_files.append(rel_file)

        if not applied_files:
            return IntegrationResult(
                success=False,
                error_message="No valid files from worker patch could be applied",
            )

        # Explicitly stage only applied files; never blindly run git add -A
        stage_res = run_git(self.target_repo, ["add", "--"] + applied_files)
        if stage_res.returncode != 0:
            # Unstage and rollback
            self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files)
            return IntegrationResult(
                success=False,
                error_message=f"Failed to stage worker patch files: {stage_res.stderr}",
            )

        # Post-integration verification before commit/push
        if self.verify_post_integration and verification_cmd:
            verify_proc = subprocess.run(
                verification_cmd,
                shell=True,
                cwd=str(self.target_repo),
                capture_output=True,
                text=True,
            )
            if verify_proc.returncode != 0:
                # Safe rollback: Revert only touched files, DO NOT clean -fd or reset --hard canonical repo
                self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files)
                return IntegrationResult(
                    success=False,
                    verification_passed=False,
                    error_message=f"Post-integration verification failed: {verify_proc.stderr or verify_proc.stdout}",
                )

        msg = commit_message or f"feat({worker_result.task_id}): apply verified fix"
        commit_res = run_git(self.target_repo, ["commit", "-m", msg])

        if commit_res.returncode != 0:
            if "nothing to commit" in commit_res.stdout or "nothing to commit" in commit_res.stderr:
                sha = get_head_sha(self.target_repo)
                return IntegrationResult(
                    success=True,
                    commit_sha=sha,
                    applied_files=applied_files,
                    pushed=False,
                )
            self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files)
            return IntegrationResult(
                success=False,
                error_message=f"Git commit failed: {commit_res.stderr}",
            )

        sha = get_head_sha(self.target_repo)

        # Push commit to remote if configured
        pushed = False
        if self.push_to_remote:
            if not self.has_remote(self.remote_name):
                if self.require_remote:
                    return IntegrationResult(
                        success=False,
                        commit_sha=sha,
                        applied_files=applied_files,
                        pushed=False,
                        error_message=f"Git push required but remote '{self.remote_name}' is not configured",
                    )
            else:
                branch_res = run_git(self.target_repo, ["rev-parse", "--abbrev-ref", "HEAD"])
                branch = branch_res.stdout.strip() or "main"
                push_res = run_git(self.target_repo, ["push", self.remote_name, branch])
                if push_res.returncode != 0:
                    return IntegrationResult(
                        success=False,
                        commit_sha=sha,
                        applied_files=applied_files,
                        pushed=False,
                        error_message=f"Git push to {self.remote_name}/{branch} failed: {push_res.stderr}",
                    )

                # Direct remote verification via git ls-remote
                if not self.verify_remote_sha(branch, sha):
                    return IntegrationResult(
                        success=False,
                        commit_sha=sha,
                        applied_files=applied_files,
                        pushed=False,
                        error_message=f"Direct remote verification failed: commit {sha} not confirmed on {self.remote_name}/{branch}",
                    )
                pushed = True

        return IntegrationResult(
            success=True,
            commit_sha=sha,
            applied_files=applied_files,
            pushed=pushed,
            verification_passed=True,
        )

    def _rollback_applied_files(
        self,
        applied_files: List[str],
        pre_existing_files: Dict[str, bytes],
        newly_created_files: List[Path],
    ) -> None:
        """Safely revert only touched files without reset --hard or clean -fd."""
        run_git(self.target_repo, ["reset", "HEAD", "--"] + applied_files)
        for rel_file, original_content in pre_existing_files.items():
            dest = self.target_repo / rel_file
            dest.write_bytes(original_content)
        for new_file in newly_created_files:
            if new_file.exists():
                new_file.unlink(missing_ok=True)
