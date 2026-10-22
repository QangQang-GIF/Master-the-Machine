"""Git worktree management for worker isolation."""
from __future__ import annotations

import shutil
from pathlib import Path

from .repository import run_git


def create_worktree(repo_path: Path | str, branch: str, worktree_path: Path | str, base_commit: str = "HEAD") -> Path:
    """Create an isolated worktree on a designated branch."""
    repo = Path(repo_path).resolve()
    target = Path(worktree_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)

    # Detach worktree or checkout new branch
    args = ["worktree", "add", "-B", branch, str(target), base_commit]
    res = run_git(repo, args)
    if res.returncode != 0:
        raise RuntimeError(f"Failed to create worktree at {target}: {res.stderr}")
    return target


def remove_worktree(repo_path: Path | str, worktree_path: Path | str, force: bool = True) -> None:
    """Prune and remove an isolated worktree."""
    repo = Path(repo_path).resolve()
    target = Path(worktree_path).resolve()

    args = ["worktree", "remove", str(target)]
    if force:
        args.append("--force")
    res = run_git(repo, args)
    if res.returncode != 0:
        # Fallback if already removed or orphaned
        shutil.rmtree(target, ignore_errors=True)
        run_git(repo, ["worktree", "prune"])
    else:
        run_git(repo, ["worktree", "prune"])
