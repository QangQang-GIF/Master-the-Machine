"""IntegrationGuard: Integrates validated worker patches into the canonical submission repository with 3-way conflict detection."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Dict, List, Optional

from mtm_core.contracts import WorkerResult
from mtm_core.git import git_diff, git_status, run_git, get_head_sha
from mtm_competition.verification.command_safety import run_verification_command


FORBIDDEN_FILE_PATTERNS = [
    # Immutable practice challenge file
    "exam-info.json",
    # Environment secrets
    ".env",
    ".env.*",
    "*.env",
    "*secret*",
    "*token*",
    # Runtime knowledge cache files
    "project_map.md",
    "index-meta.json",
]

FORBIDDEN_DIR_PREFIXES = [
    ".codex",
    ".agents",
    "team-sdlc-pack/state",
]


def is_cross_platform_absolute_path(path_str: str) -> bool:
    """Check if a path is absolute on either POSIX or Windows (drive letter, UNC, or leading slash)."""
    if not path_str:
        return False
    s = str(path_str).strip()
    if s.startswith(("/", "\\")):
        return True
    if len(s) >= 2 and s[0].isalpha() and s[1] == ":":
        return True
    try:
        if PureWindowsPath(s).is_absolute() or PurePosixPath(s).is_absolute():
            return True
    except Exception:
        pass
    return False


def is_forbidden_path(rel_path: Path | str) -> tuple[bool, str]:
    """Check if a path matches forbidden file exclusions (GIT-04).
    
    Strictly blocks:
    - exam-info.json (immutable practice challenge file)
    - Environment secrets: .env, .env.*, *.env, *secret*, *token*
    - Runtime knowledge cache files: PROJECT_MAP.md, index-meta.json, team-sdlc-pack/state/, .codex/, .agents/
    """
    p = Path(rel_path)
    posix_path = p.as_posix()
    posix_lower = posix_path.lower()
    name_lower = p.name.lower()

    # 1. Exact or glob match on filename
    for pat in FORBIDDEN_FILE_PATTERNS:
        if fnmatch.fnmatch(name_lower, pat.lower()):
            return True, f"Forbidden file pattern '{pat}' matched by '{rel_path}'"

    # 2. Check forbidden directory prefixes / components
    for prefix in FORBIDDEN_DIR_PREFIXES:
        pref_lower = prefix.lower()
        if (
            posix_lower == pref_lower
            or posix_lower.startswith(pref_lower + "/")
            or f"/{pref_lower}/" in posix_lower
            or posix_lower.endswith("/" + pref_lower)
        ):
            return True, f"Forbidden directory '{prefix}' matched by '{rel_path}'"

    # 3. Check any directory part for secret/token or agent caches
    for part in p.parts:
        part_lower = part.lower()
        if part_lower in (".codex", ".agents"):
            return True, f"Forbidden internal agent cache directory '{part}' in '{rel_path}'"
        if fnmatch.fnmatch(part_lower, "*secret*") or fnmatch.fnmatch(part_lower, "*token*"):
            return True, f"Forbidden secret/token directory '{part}' in '{rel_path}'"

    return False, ""


@dataclass
class IntegrationResult:
    success: bool
    commit_sha: Optional[str] = None
    applied_files: List[str] = None
    error_message: Optional[str] = None
    pushed: bool = False
    verification_passed: bool = True


def _has_git_head(repo: Path) -> bool:
    if not (repo / ".git").exists():
        return False
    res = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", "HEAD"],
        capture_output=True,
        check=False,
    )
    return res.returncode == 0


def _read_git_head_blob(repo: Path, rel_posix: str) -> Optional[bytes]:
    res = subprocess.run(
        ["git", "-C", str(repo), "show", f"HEAD:{rel_posix}"],
        capture_output=True,
        check=False,
    )
    if res.returncode == 0:
        return res.stdout
    return None


def _three_way_merge_bytes(current_bytes: bytes, base_bytes: bytes, worker_bytes: bytes) -> Optional[bytes]:
    """Run `git merge-file -p` on three byte buffers; return merged bytes if clean, or None on conflict."""
    with tempfile.TemporaryDirectory(prefix="mtm_3way_") as tmp_dir:
        td = Path(tmp_dir)
        cur_f = td / "current"
        base_f = td / "base"
        wrk_f = td / "worker"
        cur_f.write_bytes(current_bytes)
        base_f.write_bytes(base_bytes)
        wrk_f.write_bytes(worker_bytes)
        res = subprocess.run(
            ["git", "merge-file", "-p", str(cur_f), str(base_f), str(wrk_f)],
            capture_output=True,
            check=False,
        )
        if res.returncode == 0:
            return res.stdout
        return None


class IntegrationGuard:
    """Safely stages, 3-way merges, verifies, commits, and pushes worker patches onto the submission branch."""

    def __init__(
        self,
        target_repo_path: Path | str,
        push_to_remote: bool = True,
        remote_name: str = "origin",
        verify_post_integration: bool = True,
        require_remote: bool = False,
        match_root: Optional[Path | str] = None,
        checkout_root: Optional[Path | str] = None,
    ):
        self.target_repo = Path(target_repo_path).resolve()
        self.push_to_remote = push_to_remote
        self.remote_name = remote_name
        self.verify_post_integration = verify_post_integration
        self.require_remote = require_remote
        self.match_root = Path(match_root).resolve() if match_root else self.target_repo
        self.checkout_root = Path(checkout_root).resolve() if checkout_root else (
            self.target_repo.parent if (self.target_repo.parent / ".git").exists() else self.target_repo
        )

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

    def stage_answer_and_evidence(
        self,
        task_id: str,
        answer_data: Dict[str, Any],
        evidence_files: Optional[Dict[str, bytes | str]] = None,
    ) -> List[str]:
        """Write and stage answers/<task_id>/submit.json and relative evidence files."""
        ans_dir = self.target_repo / "answers" / task_id
        ev_dir = ans_dir / "evidence"
        ans_dir.mkdir(parents=True, exist_ok=True)

        submit_file = ans_dir / "submit.json"
        submit_file.write_text(json.dumps(answer_data, indent=2), encoding="utf-8")
        staged_files = [f"answers/{task_id}/submit.json"]

        if evidence_files:
            ev_dir.mkdir(parents=True, exist_ok=True)
            for ev_name, ev_content in evidence_files.items():
                ev_str = str(ev_name)
                ev_path = Path(ev_name)
                if is_cross_platform_absolute_path(ev_str) or ".." in ev_path.parts:
                    raise ValueError(f"Evidence path must be relative: {ev_name}")
                dest_ev = ev_dir / ev_path.name
                if isinstance(ev_content, bytes):
                    dest_ev.write_bytes(ev_content)
                else:
                    dest_ev.write_text(str(ev_content), encoding="utf-8")
                staged_files.append(f"answers/{task_id}/evidence/{ev_path.name}")

        run_git(self.target_repo, ["add", "--"] + staged_files)
        return staged_files

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

        ws_has_head = _has_git_head(ws)

        # Pre-scan ALL changed files: check path traversal, workspace containment, and forbidden paths (GIT-04, GIT-05)
        for rel_file in worker_result.changed_files:
            rel_path = Path(rel_file)
            rel_str = str(rel_file)

            # 1. Path traversal check (GIT-05)
            if (
                is_cross_platform_absolute_path(rel_str)
                or rel_path.is_absolute()
                or ".." in rel_path.parts
                or ".." in rel_str
                or "%2e" in rel_str.lower()
                or "%2f" in rel_str.lower()
                or rel_str.startswith(("/", "\\"))
            ):
                return IntegrationResult(
                    success=False,
                    error_message=f"Invalid path traversal in worker result: {rel_file}",
                )

            # 2. Strict Staging Guard & Forbidden File check (GIT-04)
            forbidden, reason = is_forbidden_path(rel_file)
            if forbidden:
                # Rollback workspace changes to preserve original files
                if ws_has_head:
                    run_git(ws, ["checkout", "HEAD", "--", rel_file])
                    run_git(ws, ["clean", "-f", rel_file])
                return IntegrationResult(
                    success=False,
                    error_message=f"Forbidden path blocked ({reason}): {rel_file}",
                )

            # 3. Workspace containment check (GIT-05)
            dest_file = (self.target_repo / rel_file).resolve()
            try:
                dest_file.relative_to(self.target_repo)
            except (ValueError, RuntimeError):
                return IntegrationResult(
                    success=False,
                    error_message=f"Path escape detected: {rel_file} is outside canonical repository",
                )

            if self.match_root:
                try:
                    dest_file.relative_to(self.match_root)
                except (ValueError, RuntimeError):
                    return IntegrationResult(
                        success=False,
                        error_message=f"Path escape detected: {rel_file} is outside match root {self.match_root}",
                    )

            if self.checkout_root:
                try:
                    dest_file.relative_to(self.checkout_root)
                except (ValueError, RuntimeError):
                    return IntegrationResult(
                        success=False,
                        error_message=f"Path escape detected: {rel_file} is outside checkout root {self.checkout_root}",
                    )

        # Validate and apply patch files safely (with 3-way conflict resolution when ws has a base HEAD)
        applied_files: List[str] = []
        newly_created_files: List[Path] = []
        pre_existing_files: Dict[str, bytes] = {}

        for rel_file in worker_result.changed_files:
            rel_path = Path(rel_file)
            rel_posix = rel_path.as_posix()

            src_file = ws / rel_file
            if src_file.is_symlink():
                self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
                return IntegrationResult(
                    success=False,
                    error_message=f"Symlinks are disallowed in worker patches: {rel_file}",
                )

            dest_file = (self.target_repo / rel_file).resolve()
            if dest_file.exists():
                pre_existing_files[rel_file] = dest_file.read_bytes()

            ws_base_bytes = _read_git_head_blob(ws, rel_posix) if ws_has_head else None

            if src_file.exists():
                worker_bytes = src_file.read_bytes()
                target_bytes_to_write = worker_bytes

                if ws_has_head and rel_file in pre_existing_files and ws_base_bytes is not None:
                    current_target_bytes = pre_existing_files[rel_file]
                    if current_target_bytes != ws_base_bytes:
                        merged = _three_way_merge_bytes(current_target_bytes, ws_base_bytes, worker_bytes)
                        if merged is None:
                            self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
                            return IntegrationResult(
                                success=False,
                                error_message=f"Three-way merge conflict on {rel_file}: canonical repo has conflicting concurrent edits",
                            )
                        target_bytes_to_write = merged

                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(target_bytes_to_write)
                applied_files.append(rel_file)
                if rel_file not in pre_existing_files:
                    newly_created_files.append(dest_file)
            elif not src_file.exists() and dest_file.exists():
                if ws_has_head and ws_base_bytes is not None and pre_existing_files[rel_file] != ws_base_bytes:
                    self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
                    return IntegrationResult(
                        success=False,
                        error_message=f"Three-way merge conflict (modify/delete) on {rel_file}",
                    )
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
            self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
            return IntegrationResult(
                success=False,
                error_message=f"Failed to stage worker patch files: {stage_res.stderr}",
            )

        # Post-staging index verification: ensure no forbidden file is staged in index
        staged_diff = run_git(self.target_repo, ["diff", "--cached", "--name-only"])
        if staged_diff.returncode == 0:
            for staged_name in staged_diff.stdout.splitlines():
                staged_name = staged_name.strip()
                if not staged_name:
                    continue
                forbidden, reason = is_forbidden_path(staged_name)
                if forbidden:
                    run_git(self.target_repo, ["reset", "HEAD"])
                    self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
                    return IntegrationResult(
                        success=False,
                        error_message=f"Staged index contains forbidden file ({reason}): {staged_name}",
                    )

        # Post-integration verification before commit/push
        if self.verify_post_integration and verification_cmd:
            try:
                verify_proc = run_verification_command(verification_cmd, cwd=self.target_repo, timeout=120)
                verify_error = verify_proc.stderr or verify_proc.stdout
                passed = verify_proc.returncode == 0
            except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
                verify_error = str(exc)
                passed = False
            if not passed:
                # Safe rollback: Revert only touched files, DO NOT clean -fd or reset --hard canonical repo
                self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
                return IntegrationResult(
                    success=False,
                    verification_passed=False,
                    error_message=f"Post-integration verification failed: {verify_error}",
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
            self._rollback_applied_files(applied_files, pre_existing_files, newly_created_files, ws, worker_result.changed_files)
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
        ws: Optional[Path] = None,
        ws_touched_files: Optional[List[str]] = None,
    ) -> None:
        """Safely revert only touched files without reset --hard or clean -fd on entire repo."""
        if applied_files:
            run_git(self.target_repo, ["reset", "HEAD", "--"] + applied_files)
        for rel_file, original_content in pre_existing_files.items():
            dest = self.target_repo / rel_file
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(original_content)
        for new_file in newly_created_files:
            if new_file.exists():
                new_file.unlink(missing_ok=True)
        if ws and ws_touched_files and _has_git_head(ws):
            for wf in ws_touched_files:
                run_git(ws, ["checkout", "HEAD", "--", wf])
                run_git(ws, ["clean", "-f", wf])
