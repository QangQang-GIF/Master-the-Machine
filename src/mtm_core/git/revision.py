"""Git revision and integrity validation."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from .repository import run_git


def get_head_sha(path: Path | str) -> str:
    """Get the current commit SHA of HEAD."""
    res = run_git(path, ["rev-parse", "HEAD"])
    if res.returncode != 0:
        raise RuntimeError(f"Failed to get HEAD SHA for {path}: {res.stderr}")
    return res.stdout.strip()


def is_repo_clean(path: Path | str) -> bool:
    """Check if the working tree has no uncommitted changes."""
    res = run_git(path, ["status", "--porcelain"])
    return res.returncode == 0 and len(res.stdout.strip()) == 0


def verify_revision(path: Path | str, expected_sha: str) -> bool:
    """Verify that HEAD matches the expected SHA prefix or exact hash."""
    head_sha = get_head_sha(path)
    return head_sha.startswith(expected_sha.strip()) or expected_sha.strip() == "HEAD"
