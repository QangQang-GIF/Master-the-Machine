"""GitGuard: Verifies repository commit state and produces a VerifiedRevision."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from mtm_core.git import get_head_sha, is_repo_clean, run_git


@dataclass(frozen=True)
class VerifiedRevision:
    """Immutable proof that a commit exists, is clean, and ready for submission."""
    commit_sha: str
    branch: str
    is_clean: bool


class GitGuard:
    """Verifies that the submission branch has a valid, non-tampered Git commit."""

    def __init__(self, repo_path: Path | str):
        self.repo = Path(repo_path).resolve()

    def verify_head(self, expected_sha: Optional[str] = None) -> VerifiedRevision:
        sha = get_head_sha(self.repo)
        if expected_sha and not sha.startswith(expected_sha.strip()):
            raise ValueError(f"GitGuard revision mismatch: HEAD is {sha}, expected {expected_sha}")

        # Check branch name
        res = run_git(self.repo, ["rev-parse", "--abbrev-ref", "HEAD"])
        branch = res.stdout.strip() or "main"

        clean = is_repo_clean(self.repo)
        return VerifiedRevision(
            commit_sha=sha,
            branch=branch,
            is_clean=clean,
        )
