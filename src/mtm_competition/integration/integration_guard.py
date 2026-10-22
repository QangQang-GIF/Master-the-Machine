"""IntegrationGuard: Integrates validated worker patches into the canonical submission repository."""
from __future__ import annotations

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
    ):
        self.target_repo = Path(target_repo_path).resolve()
        self.push_to_remote = push_to_remote
        self.remote_name = remote_name
        self.verify_post_integration = verify_post_integration

    def has_remote(self, remote: Optional[str] = None) -> bool:
        """Check if remote is configured."""
        r_name = remote or self.remote_name
        res = run_git(self.target_repo, ["remote"])
        return r_name in res.stdout.split()

    def integrate(
        self,
        worker_result: WorkerResult,
        worker_workspace: Path | str,
        commit_message: Optional[str] = None,
        verification_cmd: Optional[str] = None,
    ) -> IntegrationResult:
        ws = Path(worker_workspace).resolve()

        # Extract patch from isolated worker workspace
        patch_text = git_diff(ws, base="HEAD", stage_all=True)
        if not patch_text and not worker_result.changed_files:
            return IntegrationResult(
                success=False,
                error_message="Worker reported changes but diff is empty",
            )

        # Apply patch or copy files into target repository
        applied_files = []
        for rel_file in worker_result.changed_files:
            src_file = ws / rel_file
            dest_file = self.target_repo / rel_file
            if src_file.exists():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(src_file.read_bytes())
                applied_files.append(rel_file)
            elif not src_file.exists() and dest_file.exists():
                # Handled deletion
                dest_file.unlink()
                applied_files.append(rel_file)

        # Stage applied files in target repository
        if applied_files:
            run_git(self.target_repo, ["add"] + applied_files)
        else:
            run_git(self.target_repo, ["add", "-A"])

        # Optional post-integration verification on target repo
        if self.verify_post_integration and verification_cmd:
            verify_proc = subprocess.run(
                verification_cmd,
                shell=True,
                cwd=str(self.target_repo),
                capture_output=True,
                text=True,
            )
            if verify_proc.returncode != 0:
                # Rollback changes on target repo
                run_git(self.target_repo, ["reset", "--hard", "HEAD"])
                run_git(self.target_repo, ["clean", "-fd"])
                return IntegrationResult(
                    success=False,
                    verification_passed=False,
                    error_message=f"Post-integration verification failed: {verify_proc.stderr or verify_proc.stdout}",
                )

        msg = commit_message or f"feat({worker_result.task_id}): apply verified fix"
        commit_res = run_git(self.target_repo, ["commit", "-m", msg])

        if commit_res.returncode != 0:
            # Check if there were no changes
            if "nothing to commit" in commit_res.stdout or "nothing to commit" in commit_res.stderr:
                sha = get_head_sha(self.target_repo)
                return IntegrationResult(
                    success=True,
                    commit_sha=sha,
                    applied_files=applied_files,
                    pushed=False,
                )
            return IntegrationResult(
                success=False,
                error_message=f"Git commit failed: {commit_res.stderr}",
            )

        sha = get_head_sha(self.target_repo)

        # Push commit to remote if configured
        pushed = False
        if self.push_to_remote and self.has_remote(self.remote_name):
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
            pushed = True

        return IntegrationResult(
            success=True,
            commit_sha=sha,
            applied_files=applied_files,
            pushed=pushed,
            verification_passed=True,
        )
