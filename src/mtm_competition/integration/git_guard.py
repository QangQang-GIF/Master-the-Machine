"""GitGuard: Verifies repository commit state, remote pushed refs, answer parity, and evidence containment."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.git import get_head_sha, is_repo_clean, run_git
from mtm_competition.integration.integration_guard import is_forbidden_path


class SubmissionGateError(Exception):
    """Raised when pre-submission gate or git tree validation fails."""
    pass


@dataclass(frozen=True)
class VerifiedRevision:
    """Immutable proof that a commit exists, is clean, and ready for submission."""
    commit_sha: str
    branch: str
    is_clean: bool
    is_pushed: bool = False


@dataclass
class ValidationResult:
    """Pre-submission Git validation outcome."""
    valid: bool
    error_message: Optional[str] = None


class GitGuard:
    """Verifies that the submission branch has a valid, non-tampered Git commit."""

    def __init__(self, repo_path: Path | str):
        self.repo = Path(repo_path).resolve()

    def has_remote(self, remote: str = "origin") -> bool:
        """Check if remote is configured in this repository."""
        res = run_git(self.repo, ["remote"])
        return remote in res.stdout.split()

    def verify_commit_pushed(self, commit_sha: str, remote_name: str = "origin", branch_or_ref: str = "main") -> bool:
        """Verify on remote using git ls-remote that branch ref matches commit."""
        candidates = [branch_or_ref]
        if not branch_or_ref.startswith("refs/"):
            candidates.insert(0, f"refs/heads/{branch_or_ref}")
        for ref_query in candidates:
            ls_res = run_git(self.repo, ["ls-remote", remote_name, ref_query])
            if ls_res.returncode == 0 and ls_res.stdout.strip():
                for line in ls_res.stdout.strip().splitlines():
                    parts = line.split()
                    if parts:
                        r_sha = parts[0].strip()
                        if r_sha.startswith(commit_sha) or commit_sha.startswith(r_sha):
                            return True
        return False

    def verify_file_in_tree(self, commit_sha: str, rel_path: str) -> bool:
        """Check if file exists in commit tree using git cat-file -e."""
        posix_path = Path(rel_path).as_posix().lstrip("/")
        res = run_git(self.repo, ["cat-file", "-e", f"{commit_sha}:{posix_path}"])
        return res.returncode == 0

    def get_file_content_from_tree(self, commit_sha: str, rel_path: str) -> Optional[str]:
        """Read text file content from commit tree using git show."""
        posix_path = Path(rel_path).as_posix().lstrip("/")
        res = run_git(self.repo, ["show", f"{commit_sha}:{posix_path}"])
        if res.returncode == 0:
            return res.stdout
        return None

    def verify_head(
        self,
        expected_sha: Optional[str] = None,
        verify_pushed: bool = False,
        remote: str = "origin",
    ) -> VerifiedRevision:
        sha = get_head_sha(self.repo)
        if expected_sha and not sha.startswith(expected_sha.strip()):
            raise ValueError(f"GitGuard revision mismatch: HEAD is {sha}, expected {expected_sha}")

        # Check branch name
        res = run_git(self.repo, ["rev-parse", "--abbrev-ref", "HEAD"])
        branch = res.stdout.strip() or "main"

        clean = is_repo_clean(self.repo)
        if not clean:
            raise ValueError(f"GitGuard repository is not clean: uncommitted changes exist in {self.repo}")

        is_pushed = False
        if self.has_remote(remote):
            # Direct verification against remote using git ls-remote
            ls_res = run_git(self.repo, ["ls-remote", remote, f"refs/heads/{branch}"])
            if ls_res.returncode == 0 and ls_res.stdout.strip():
                remote_sha = ls_res.stdout.split()[0].strip()
                if remote_sha.startswith(sha) or sha.startswith(remote_sha):
                    is_pushed = True
            if not is_pushed:
                # Fallback check on remote branch
                rem_res = run_git(self.repo, ["branch", "-r", "--contains", sha])
                if f"{remote}/{branch}" in rem_res.stdout:
                    is_pushed = True
        elif verify_pushed:
            raise ValueError(f"GitGuard verification failed: remote '{remote}' is not configured")

        if verify_pushed and not is_pushed:
            raise ValueError(
                f"GitGuard verification failed: commit {sha} has not been pushed to {remote}/{branch}"
            )

        return VerifiedRevision(
            commit_sha=sha,
            branch=branch,
            is_clean=clean,
            is_pushed=is_pushed,
        )


class PreSubmissionGitGuard:
    """Pre-submission Git Hard Gate enforcing:
    1. Remote push: git ls-remote remote_name assigned_branch_ref matches target_commit_sha (GIT-03).
    2. submit.json parity: git show <sha>:answers/<ID>/submit.json equals answer_payload (GIT-01).
    3. Evidence containment: git cat-file -e <sha>:answers/<ID>/evidence/<name> for each relative evidence file (GIT-02).
    4. Staging block: no .env*, exam-info.json, or cache files modified (GIT-04).
    5. Workspace containment: no directory traversal or path escape outside matchRoot / checkoutRoot (GIT-05).
    """

    def __init__(self, repo_path: Optional[Path | str] = None):
        self.repo = Path(repo_path).resolve() if repo_path else None

    def validate_pre_submission(
        self,
        repo_path: Optional[Path | str] = None,
        target_commit_sha: Optional[str] = None,
        remote_name: str = "origin",
        assigned_branch_ref: str = "main",
        backlog_item_key: Optional[str] = None,
        answer_payload: Optional[Dict[str, Any]] = None,
        evidence_files: Optional[List[str]] = None,
        match_root: Optional[Path | str] = None,
        checkout_root: Optional[Path | str] = None,
        require_remote: bool = True,
        raise_on_error: bool = False,
    ) -> ValidationResult:
        r_path = Path(repo_path or self.repo).resolve() if (repo_path or self.repo) else None
        if r_path is None or not (r_path / ".git").exists():
            msg = f"Invalid git repository path: {r_path}"
            if raise_on_error:
                raise SubmissionGateError(msg)
            return ValidationResult(valid=False, error_message=msg)

        # 1. Resolve target commit SHA
        sha = target_commit_sha or get_head_sha(r_path)
        verify_res = run_git(r_path, ["rev-parse", "--verify", sha])
        if verify_res.returncode != 0:
            msg = f"Target commit SHA {sha} is invalid or does not exist in repository {r_path}"
            if raise_on_error:
                raise SubmissionGateError(msg)
            return ValidationResult(valid=False, error_message=msg)
        full_sha = verify_res.stdout.strip()

        # 2. Remote branch ref push verification (GIT-03)
        if require_remote:
            if not remote_name:
                msg = "Remote push verification required but no remote name provided"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

            rem_res = run_git(r_path, ["remote"])
            if remote_name not in rem_res.stdout.split():
                msg = f"Remote '{remote_name}' is not configured in repository"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

            candidates = [assigned_branch_ref]
            if not assigned_branch_ref.startswith("refs/"):
                candidates.insert(0, f"refs/heads/{assigned_branch_ref}")

            matched_remote_sha = None
            for cand in candidates:
                ls_res = run_git(r_path, ["ls-remote", remote_name, cand])
                if ls_res.returncode == 0 and ls_res.stdout.strip():
                    for line in ls_res.stdout.strip().splitlines():
                        parts = line.split()
                        if parts:
                            r_sha = parts[0].strip()
                            if r_sha.startswith(full_sha) or full_sha.startswith(r_sha):
                                matched_remote_sha = r_sha
                                break
                    if matched_remote_sha:
                        break

            if not matched_remote_sha:
                msg = f"Remote push verification failed: commit {full_sha} is not pushed to {remote_name} ref '{assigned_branch_ref}'"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

        # Prefix if match_root is relative to r_path
        prefix = ""
        if match_root:
            try:
                rel_m = Path(match_root).resolve().relative_to(r_path).as_posix()
                if rel_m != ".":
                    prefix = f"{rel_m}/"
            except Exception:
                pass

        # 3. answers/<ID>/submit.json Commit Parity (GIT-01)
        if backlog_item_key:
            tree_path = f"{prefix}answers/{backlog_item_key}/submit.json"
            cat_res = run_git(r_path, ["cat-file", "-e", f"{full_sha}:{tree_path}"])
            if cat_res.returncode != 0 and prefix:
                fallback_path = f"answers/{backlog_item_key}/submit.json"
                cat_res = run_git(r_path, ["cat-file", "-e", f"{full_sha}:{fallback_path}"])
                if cat_res.returncode == 0:
                    tree_path = fallback_path

            if cat_res.returncode != 0:
                msg = f"Target commit {full_sha} is missing required answer file '{tree_path}'"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

            show_res = run_git(r_path, ["show", f"{full_sha}:{tree_path}"])
            if show_res.returncode != 0:
                msg = f"Failed to read '{tree_path}' from commit {full_sha}: {show_res.stderr}"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

            try:
                committed_answer = json.loads(show_res.stdout)
            except Exception as exc:
                msg = f"Target commit {full_sha} contains invalid JSON in '{tree_path}': {exc}"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

            if answer_payload is not None:
                if committed_answer != answer_payload:
                    msg = (
                        f"Commit answer parity mismatch for task {backlog_item_key}: "
                        f"'{tree_path}' in commit {full_sha} does not match answer payload"
                    )
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)

        # 4. Relative Evidence Containment (GIT-02)
        if backlog_item_key:
            all_evidence = list(evidence_files or [])
            if answer_payload and isinstance(answer_payload, dict):
                ev_in_ans = answer_payload.get("evidence")
                if isinstance(ev_in_ans, list):
                    for ev_item in ev_in_ans:
                        if ev_item not in all_evidence:
                            all_evidence.append(ev_item)

            for ev in all_evidence:
                ev_str = str(ev)
                # Check absolute path
                if os.path.isabs(ev_str) or Path(ev_str).is_absolute() or ev_str.startswith(("/", "\\")):
                    msg = f"Illegal absolute evidence path rejected: '{ev_str}'"
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)

                # Check directory traversal
                if ".." in Path(ev_str).parts or ".." in ev_str or "%2e" in ev_str.lower() or "%2f" in ev_str.lower():
                    msg = f"Path traversal detected in evidence path: '{ev_str}'"
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)

                # Normalize tree path: must reside in answers/<ID>/evidence/<name>
                ev_posix = Path(ev_str).as_posix().lstrip("./")
                expected_prefix = f"answers/{backlog_item_key}/evidence/"

                if ev_posix.startswith(expected_prefix):
                    tree_ev_path = f"{prefix}{ev_posix}"
                elif ev_posix.startswith("evidence/"):
                    tree_ev_path = f"{prefix}answers/{backlog_item_key}/{ev_posix}"
                elif ev_posix.startswith("answers/"):
                    msg = f"Evidence path traverses outside task {backlog_item_key} evidence directory: '{ev_str}'"
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)
                else:
                    tree_ev_path = f"{prefix}answers/{backlog_item_key}/evidence/{ev_posix}"

                cat_ev = run_git(r_path, ["cat-file", "-e", f"{full_sha}:{tree_ev_path}"])
                if cat_ev.returncode != 0 and prefix:
                    fallback_ev_path = tree_ev_path[len(prefix):]
                    cat_ev = run_git(r_path, ["cat-file", "-e", f"{full_sha}:{fallback_ev_path}"])
                    if cat_ev.returncode == 0:
                        tree_ev_path = fallback_ev_path

                if cat_ev.returncode != 0:
                    msg = f"Evidence file '{ev_str}' missing from commit {full_sha} (checked '{tree_ev_path}')"
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)

        # 5. Staging Block & Immutable / Secret File check in target commit (GIT-04)
        diff_tree = run_git(r_path, ["diff-tree", "--no-commit-id", "--name-only", "-r", "--root", full_sha])
        if diff_tree.returncode == 0:
            for committed_file in diff_tree.stdout.splitlines():
                committed_file = committed_file.strip()
                if not committed_file:
                    continue
                forbidden, reason = is_forbidden_path(committed_file)
                if forbidden:
                    msg = f"Target commit {full_sha} touches forbidden file '{committed_file}' ({reason})"
                    if raise_on_error:
                        raise SubmissionGateError(msg)
                    return ValidationResult(valid=False, error_message=msg)

        # 6. Workspace Containment Check (GIT-05)
        if match_root and checkout_root:
            try:
                Path(match_root).resolve().relative_to(Path(checkout_root).resolve())
            except Exception:
                msg = f"Match root {match_root} is not contained within checkout root {checkout_root}"
                if raise_on_error:
                    raise SubmissionGateError(msg)
                return ValidationResult(valid=False, error_message=msg)

        return ValidationResult(valid=True, error_message=None)
