"""mtm_core.git — Git repository, revision, and worktree isolation utilities."""
from .repository import run_git, git_init, git_status, git_diff, git_commit, git_clone, configure_git_identity
from .revision import get_head_sha, is_repo_clean, verify_revision, has_head_commit
from .worktree import create_worktree, remove_worktree

__all__ = [
    "run_git",
    "git_init",
    "git_status",
    "git_diff",
    "git_commit",
    "git_clone",
    "configure_git_identity",
    "get_head_sha",
    "is_repo_clean",
    "verify_revision",
    "has_head_commit",
    "create_worktree",
    "remove_worktree",
]
