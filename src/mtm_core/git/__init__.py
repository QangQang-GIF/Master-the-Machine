"""mtm_core.git — Git repository, revision, and worktree isolation utilities."""
from .repository import run_git, git_init, git_status, git_diff, git_commit
from .revision import get_head_sha, is_repo_clean, verify_revision
from .worktree import create_worktree, remove_worktree

__all__ = [
    "run_git",
    "git_init",
    "git_status",
    "git_diff",
    "git_commit",
    "get_head_sha",
    "is_repo_clean",
    "verify_revision",
    "create_worktree",
    "remove_worktree",
]
