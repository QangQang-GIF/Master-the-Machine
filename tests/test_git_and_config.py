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
    from mtm.constants import MODEL, MODEL_EFFORT, CONCURRENCY, DEFAULT_GAME_MASTER_ORIGIN
    assert profiles["profiles"]["standard"]["codex_model"] == MODEL


def test_mtm_runtime_constants():
    from mtm.constants import (
        MODEL,
        MODEL_EFFORT,
        CONCURRENCY,
        DEFAULT_GAME_MASTER_ORIGIN,
        DEFAULT_TASK_TIMEOUT_SECONDS,
        START_WORK_PATH,
        SUBMISSION_PATH,
    )
    assert MODEL == "gpt-6.1-sol"
    assert MODEL_EFFORT == "medium"
    assert CONCURRENCY == 1
    assert DEFAULT_GAME_MASTER_ORIGIN == "https://masterthemachine.tech"
    assert DEFAULT_TASK_TIMEOUT_SECONDS == 420.0
    assert START_WORK_PATH == "/api/v1/matches/{match_id}/backlogs/start"
    assert SUBMISSION_PATH == "/api/v1/matches/{match_id}/submissions"


def test_clone_empty_repo_and_head_nonexistent(tmp_path):
    """Phase 0: Test behavior with empty bare repo, clone, and unresolvable HEAD."""
    from mtm_core.git import run_git, git_clone, has_head_commit, get_head_sha

    bare_remote = tmp_path / "bare_empty.git"
    res = run_git(tmp_path, ["init", "--bare", str(bare_remote)])
    assert res.returncode == 0

    cloned_repo = tmp_path / "cloned_empty"
    git_clone(bare_remote, cloned_repo, user_name="CI Bot", user_email="ci@local")

    # In empty repo, HEAD does not exist yet
    assert not has_head_commit(cloned_repo)
    with pytest.raises(RuntimeError, match="Failed to get HEAD SHA"):
        get_head_sha(cloned_repo)


def test_git_identity_configuration(tmp_path):
    """Phase 0: Ensure git identity can be configured and commit succeeds independently of global config."""
    from mtm_core.git import run_git, configure_git_identity, git_commit, get_head_sha

    repo_dir = tmp_path / "isolated_identity_repo"
    run_git(tmp_path, ["init", str(repo_dir)])
    
    # Configure explicit identity
    configure_git_identity(repo_dir, user_name="Custom Runner", user_email="custom@runner.org")
    
    (repo_dir / "file.txt").write_text("content\n", encoding="utf-8")
    commit_sha = git_commit(repo_dir, "Custom commit message")
    assert len(commit_sha) == 40
    assert commit_sha == get_head_sha(repo_dir)

    # Verify committer info in log
    log_res = run_git(repo_dir, ["log", "-1", "--format=%an <%ae>"])
    assert log_res.returncode == 0
    assert "Custom Runner <custom@runner.org>" in log_res.stdout
