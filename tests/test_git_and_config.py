"""Unit tests for mtm_core.git and mtm_core.config."""
from pathlib import Path
import pytest

from mtm_core.git import (
    git_init,
    git_status,
    git_diff,
    get_head_sha,
    is_repo_clean,
    verify_revision,
    create_worktree,
    remove_worktree,
)
from mtm_core.config import (
    load_environment_config,
    load_model_profiles,
)


def test_git_operations(tmp_path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)

    assert is_repo_clean(repo_dir)
    sha = get_head_sha(repo_dir)
    assert len(sha) == 40
    assert verify_revision(repo_dir, sha[:7])
    assert verify_revision(repo_dir, "HEAD")

    # Add a file
    test_file = repo_dir / "code.py"
    test_file.write_text("a = 1\n", encoding="utf-8")
    assert not is_repo_clean(repo_dir)

    changed = git_status(repo_dir)
    assert "code.py" in changed

    diff = git_diff(repo_dir)
    assert "+a = 1" in diff or "code.py" in diff


def test_worktree_lifecycle(tmp_path):
    repo_dir = tmp_path / "main_repo"
    git_init(repo_dir)

    wt_dir = tmp_path / "isolated_worker"
    create_worktree(repo_dir, branch="worker/task-01", worktree_path=wt_dir)

    assert wt_dir.exists()
    assert (wt_dir / ".git").exists()

    remove_worktree(repo_dir, wt_dir)
    assert not wt_dir.exists()


def test_config_loader():
    dev_config = load_environment_config("development")
    assert dev_config["mode"] == "development"
    assert dev_config["skills"]["mutable"] is True

    prod_config = load_environment_config("production")
    assert prod_config["mode"] == "production"
    assert prod_config["skills"]["mutable"] is False

    profiles = load_model_profiles()
    assert "fast" in profiles["profiles"]
    assert "standard" in profiles["profiles"]
    assert "deep_reasoning" in profiles["profiles"]
