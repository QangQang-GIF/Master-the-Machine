"""Git repository management: init, diff, status, commit."""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import List, Optional


def run_git(path: Path | str, args: List[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Execute a git command within a repository path."""
    cmd = ["git", "-C", str(path)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def git_init(path: Path | str, user_name: str = "mtm-agent", user_email: str = "agent@local") -> None:
    """Initialize a fresh Git repository with an initial base commit."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    steps = [
        ["init", "-q"],
        ["config", "core.autocrlf", "false"],
        ["config", "user.name", user_name],
        ["config", "user.email", user_email],
        ["add", "-A"],
        ["commit", "-q", "-m", "base", "--allow-empty"],
    ]
    for args in steps:
        res = run_git(p, args)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed in {p}: {res.stderr}")


def git_status(path: Path | str) -> List[str]:
    """Return list of modified/untracked files relative to the repository root."""
    res = run_git(path, ["status", "--porcelain"])
    if res.returncode != 0:
        return []
    changed = []
    for line in res.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            changed.append(parts[1])
    return changed


def git_diff(path: Path | str, base: str = "HEAD", cached: bool = False, stage_all: bool = True) -> str:
    """Return unified diff relative to base revision or index.

    If stage_all is True (default), newly created and modified files are staged first
    so the resulting diff captures all workspace changes relative to base.
    """
    if stage_all:
        run_git(path, ["add", "-A"])
        cached = True
    args = ["diff", "--binary"]
    if cached:
        args.append("--cached")
    if base:
        args.append(base)
    res = run_git(path, args)
    return res.stdout


def git_commit(path: Path | str, message: str) -> str:
    """Stage all changes and commit, returning the commit SHA."""
    run_git(path, ["add", "-A"])
    res = run_git(path, ["commit", "-m", message])
    if res.returncode != 0:
        raise RuntimeError(f"git commit failed: {res.stderr}")
    return git_diff(path, base="HEAD~1")
