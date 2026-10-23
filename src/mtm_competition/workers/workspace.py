"""WorkspaceManager: Manages isolated execution directories and Git worktrees for workers."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from mtm_core.git import create_worktree, remove_worktree, git_status, git_diff


class WorkspaceManager:
    """Provides isolated, clean workspace environments for worker execution."""

    def __init__(self, base_scratch_dir: Path | str):
        self.scratch_dir = Path(base_scratch_dir).resolve()
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

    def prepare_workspace(
        self,
        repo_path: Path | str,
        task_id: str,
        attempt: int = 1,
        use_worktree: bool = True,
    ) -> Path:
        """Create an isolated workspace or worktree for a task attempt."""
        repo = Path(repo_path).resolve()
        ws_name = f"{task_id}_att{attempt}"
        ws_path = self.scratch_dir / ws_name

        if ws_path.exists():
            self.cleanup_workspace(repo, ws_path)

        def _ignore(directory, files):
            ignored = set()
            try:
                # If scratch_dir is inside repo, do not copy scratch_dir recursively
                if Path(directory).resolve() == repo.resolve():
                    rel = self.scratch_dir.resolve().relative_to(repo.resolve())
                    if rel.parts:
                        ignored.add(rel.parts[0])
            except ValueError:
                pass
            return list(ignored)

        if use_worktree and (repo / ".git").exists():
            branch_name = f"worker/{task_id}_att{attempt}"
            try:
                return create_worktree(repo, branch=branch_name, worktree_path=ws_path)
            except Exception:
                # Fallback to copy if worktree add fails (e.g. shallow clone)
                shutil.copytree(repo, ws_path, ignore=_ignore)
                return ws_path
        else:
            shutil.copytree(repo, ws_path, ignore=_ignore)
            return ws_path

    def cleanup_workspace(self, repo_path: Path | str, ws_path: Path | str) -> None:
        """Safely prune worktree and remove workspace directory."""
        repo = Path(repo_path).resolve()
        ws = Path(ws_path).resolve()
        if (repo / ".git").exists() and (ws / ".git").is_file():
            # It's a git worktree
            remove_worktree(repo, ws)
        else:
            shutil.rmtree(ws, ignore_errors=True)
