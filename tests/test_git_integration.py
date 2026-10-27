"""Unit and integration tests for Git Hard Gate, answer parity, evidence containment, and staging guards.

Covers:
- GIT-01: answers/<ID>/submit.json Commit Parity
- GIT-02: Relative Evidence Containment & Path Traversal Prevention
- GIT-03: Remote Branch Ref Push Verification via git ls-remote
- GIT-04: Strict Staging Guard & Forbidden File Exclusions (.env*, exam-info.json, caches)
- GIT-05: Workspace Containment (matchRoot / checkoutRoot boundary enforcement)
- SubmissionGuard & PreSubmissionGitGuard Integration
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mtm_competition.integration.git_guard import (
    GitGuard,
    PreSubmissionGitGuard,
    SubmissionGateError,
    ValidationResult,
    VerifiedRevision,
)
from mtm_competition.integration.integration_guard import (
    IntegrationGuard,
    IntegrationResult,
    is_forbidden_path,
)
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.tasks.store import TaskStore
from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_core.git import get_head_sha, git_init, run_git


@pytest.fixture
def offline_git_env(tmp_path: Path):
    """Creates a local bare remote and a cloned repository with explicit user identity."""
    base_dir = tmp_path / "git_env"
    base_dir.mkdir(parents=True)

    remote_dir = base_dir / "remote.git"
    res1 = run_git(base_dir, ["init", "--bare", str(remote_dir)])
    assert res1.returncode == 0, f"git init --bare failed: {res1.stderr}"

    checkout_dir = base_dir / "checkout_root"
    res2 = run_git(base_dir, ["clone", str(remote_dir), str(checkout_dir)])
    assert res2.returncode == 0, f"git clone failed: {res2.stderr}"

    run_git(checkout_dir, ["config", "user.name", "MTM Bot"])
    run_git(checkout_dir, ["config", "user.email", "bot@mtm.local"])
    run_git(checkout_dir, ["config", "core.autocrlf", "false"])

    # Create initial commit on main
    (checkout_dir / "app.py").write_text("print('hello world')\n", encoding="utf-8")
    run_git(checkout_dir, ["add", "app.py"])
    run_git(checkout_dir, ["commit", "-m", "initial commit"])
    run_git(checkout_dir, ["branch", "-M", "main"])
    run_git(checkout_dir, ["push", "-u", "origin", "main"])

    return {
        "remote_dir": remote_dir,
        "checkout_dir": checkout_dir,
        "branch": "main",
    }


# =====================================================================
# 1. GIT-04: Strict Staging Guard & Forbidden File Exclusions
# =====================================================================

def test_is_forbidden_path_rule_matching():
    # 1. Immutable challenge file
    assert is_forbidden_path("exam-info.json")[0] is True
    assert is_forbidden_path("subdir/exam-info.json")[0] is True

    # 2. Environment secrets
    assert is_forbidden_path(".env")[0] is True
    assert is_forbidden_path(".env.local")[0] is True
    assert is_forbidden_path(".env.production")[0] is True
    assert is_forbidden_path("prod.env")[0] is True
    assert is_forbidden_path("secrets.json")[0] is True
    assert is_forbidden_path("api_token.txt")[0] is True
    assert is_forbidden_path("auth/secret_key.pem")[0] is True

    # 3. Runtime knowledge cache files
    assert is_forbidden_path("PROJECT_MAP.md")[0] is True
    assert is_forbidden_path("docs/PROJECT_MAP.md")[0] is True
    assert is_forbidden_path("index-meta.json")[0] is True
    assert is_forbidden_path("team-sdlc-pack/state/run.json")[0] is True
    assert is_forbidden_path(".codex/cache.json")[0] is True
    assert is_forbidden_path(".agents/agents/worker.toml")[0] is True

    # 4. Permitted files
    assert is_forbidden_path("src/app.py")[0] is False
    assert is_forbidden_path("answers/BUG-01/submit.json")[0] is False
    assert is_forbidden_path("answers/BUG-01/evidence/test.log")[0] is False
    assert is_forbidden_path("docs/PROJECT_MAP_TEMPLATE.md")[0] is False


def test_integration_guard_blocks_exam_info_json_and_preserves_original(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    original_exam_info = '{"matchId": "PRACTICE-1", "immutable": true}\n'
    (repo_dir / "exam-info.json").write_text(original_exam_info, encoding="utf-8")
    run_git(repo_dir, ["add", "exam-info.json"])
    run_git(repo_dir, ["commit", "-m", "add exam-info"])

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    git_init(ws_dir)
    (ws_dir / "exam-info.json").write_text('{"hacked": true}\n', encoding="utf-8")

    worker_res = WorkerResult(
        task_id="TASK-FORBIDDEN-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["exam-info.json"],
        test_results=[],
        evidence_paths=[],
    )

    guard = IntegrationGuard(repo_dir, push_to_remote=False)
    res = guard.integrate(worker_res, ws_dir)

    assert res.success is False
    assert "Forbidden path blocked" in res.error_message
    # Verify original file in target_repo was preserved
    assert (repo_dir / "exam-info.json").read_text(encoding="utf-8") == original_exam_info


def test_integration_guard_blocks_secrets_and_caches(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    git_init(ws_dir)

    guard = IntegrationGuard(repo_dir, push_to_remote=False)

    forbidden_samples = [
        ".env",
        ".env.local",
        "secret_token.json",
        "PROJECT_MAP.md",
        "index-meta.json",
        "team-sdlc-pack/state/session.json",
        ".codex/agents/profile.toml",
    ]

    for forbidden_file in forbidden_samples:
        src = ws_dir / forbidden_file
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("data\n", encoding="utf-8")

        worker_res = WorkerResult(
            task_id="TASK-SAMPLE",
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=[forbidden_file],
            test_results=[],
            evidence_paths=[],
        )
        res = guard.integrate(worker_res, ws_dir)
        assert res.success is False
        assert "Forbidden path blocked" in res.error_message


# =====================================================================
# 2. GIT-05: Workspace Containment
# =====================================================================

def test_integration_guard_blocks_path_traversal(tmp_path: Path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    git_init(ws_dir)

    guard = IntegrationGuard(repo_dir, push_to_remote=False)

    traversal_cases = [
        "../outside.py",
        "../../escape.txt",
        "..\\windows_escape.py",
    ]

    for bad_path in traversal_cases:
        worker_res = WorkerResult(
            task_id="TASK-TRAVERSAL",
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=[bad_path],
            test_results=[],
            evidence_paths=[],
        )
        res = guard.integrate(worker_res, ws_dir)
        assert res.success is False
        assert "Invalid path traversal" in res.error_message


def test_integration_guard_blocks_escape_outside_match_root(tmp_path: Path):
    checkout_root = tmp_path / "checkout"
    git_init(checkout_root)

    match_root = checkout_root / "frontend"
    match_root.mkdir(parents=True)

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    git_init(ws_dir)

    # Worker tries to write outside match_root into sibling folder
    sibling_file = tmp_path / "sibling" / "file.py"
    sibling_file.parent.mkdir(parents=True, exist_ok=True)

    guard = IntegrationGuard(
        target_repo_path=match_root,
        match_root=match_root,
        checkout_root=checkout_root,
        push_to_remote=False,
    )

    worker_res = WorkerResult(
        task_id="TASK-ESCAPE",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["../backend/secret.py"],
        test_results=[],
        evidence_paths=[],
    )
    res = guard.integrate(worker_res, ws_dir)
    assert res.success is False


# =====================================================================
# 3. GIT-01: answers/<ID>/submit.json Commit Parity
# =====================================================================

def test_git_guard_blocks_when_submit_json_missing_from_commit(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Head commit only contains app.py, answers/TASK-1/submit.json is missing
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-1",
        answer_payload={"status": "complete"},
        evidence_files=[],
    )
    assert res.valid is False
    assert "missing required answer file" in res.error_message


def test_git_guard_blocks_when_submit_json_mismatches_payload(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-PARITY-01"
    ans_dir.mkdir(parents=True)
    (ans_dir / "submit.json").write_text(json.dumps({"answer": "version_1"}), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit.json"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Test with differing payload
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-PARITY-01",
        answer_payload={"answer": "version_2"},  # Differs from version_1 in commit
        evidence_files=[],
    )
    assert res.valid is False
    assert "Commit answer parity mismatch" in res.error_message


def test_git_guard_passes_when_submit_json_matches_payload(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-MATCH-01"
    ans_dir.mkdir(parents=True)
    expected_answer = {"fix": "applied", "verified": True, "score": 100}
    (ans_dir / "submit.json").write_text(json.dumps(expected_answer), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit.json matching"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-MATCH-01",
        answer_payload=expected_answer,
        evidence_files=[],
    )
    assert res.valid is True
    assert res.error_message is None


# =====================================================================
# 4. GIT-02: Relative Evidence Containment & Cat-File Verification
# =====================================================================

def test_git_guard_blocks_absolute_evidence_path(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-EV-01"
    ans_dir.mkdir(parents=True)
    ans_payload = {"fix": "ok"}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit.json"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Absolute paths must be rejected
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-EV-01",
        answer_payload=ans_payload,
        evidence_files=["/tmp/test_run.log"],
    )
    assert res.valid is False
    assert "Illegal absolute evidence path rejected" in res.error_message


def test_git_guard_blocks_traversing_evidence_path(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-EV-02"
    ans_dir.mkdir(parents=True)
    ans_payload = {"fix": "ok"}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit.json"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Path traversal must be rejected
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-EV-02",
        answer_payload=ans_payload,
        evidence_files=["../../etc/passwd"],
    )
    assert res.valid is False
    assert "Path traversal detected in evidence path" in res.error_message


def test_git_guard_blocks_when_evidence_file_missing_from_commit(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-EV-03"
    ans_dir.mkdir(parents=True)
    ans_payload = {"fix": "ok"}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit.json without evidence"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Evidence file is declared but was not committed
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-EV-03",
        answer_payload=ans_payload,
        evidence_files=["evidence/run.log"],
    )
    assert res.valid is False
    assert "missing from commit" in res.error_message


def test_git_guard_passes_when_relative_evidence_committed(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-EV-04"
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True)

    ans_payload = {"fix": "ok", "evidence": ["evidence/test_run.log"]}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")
    (ev_dir / "test_run.log").write_text("All 45 unit tests passed.\n", encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "add submit and evidence"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-EV-04",
        answer_payload=ans_payload,
        evidence_files=["evidence/test_run.log"],
    )
    assert res.valid is True
    assert res.error_message is None


# =====================================================================
# 5. GIT-03: Remote Branch Ref Push Verification (git ls-remote)
# =====================================================================

def test_git_guard_blocks_unpushed_commit(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    ans_dir = repo / "answers" / "TASK-UNPUSHED"
    ans_dir.mkdir(parents=True)
    ans_payload = {"fix": "ok"}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "local commit only, not pushed"])
    local_sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Commit exists locally but NOT on remote origin/main
    res = guard.validate_pre_submission(
        target_commit_sha=local_sha,
        remote_name="origin",
        assigned_branch_ref="main",
        backlog_item_key="TASK-UNPUSHED",
        answer_payload=ans_payload,
        evidence_files=[],
    )
    assert res.valid is False
    assert "Remote push verification failed" in res.error_message


def test_git_guard_blocks_commit_pushed_to_wrong_branch(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    # Create feature branch and push there instead of assigned main
    run_git(repo, ["checkout", "-b", "feature/wrong_branch"])
    ans_dir = repo / "answers" / "TASK-WRONG-BRANCH"
    ans_dir.mkdir(parents=True)
    ans_payload = {"fix": "ok"}
    (ans_dir / "submit.json").write_text(json.dumps(ans_payload), encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "pushed to wrong branch"])
    run_git(repo, ["push", "-u", "origin", "feature/wrong_branch"])
    sha = get_head_sha(repo)

    guard = PreSubmissionGitGuard(repo)
    # Target commit exists on feature/wrong_branch, but assigned ref is main
    res = guard.validate_pre_submission(
        target_commit_sha=sha,
        remote_name="origin",
        assigned_branch_ref="main",  # Assigned ref
        backlog_item_key="TASK-WRONG-BRANCH",
        answer_payload=ans_payload,
        evidence_files=[],
    )
    assert res.valid is False
    assert "Remote push verification failed" in res.error_message


# =====================================================================
# 6. SubmissionGuard & PreSubmissionGitGuard Integration
# =====================================================================

def test_submission_guard_executes_when_all_git_invariants_met(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    task_id = "TASK-E2E-GUARD"
    ans_dir = repo / "answers" / task_id
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True)

    answer_payload = {"status": "RESOLVED", "solution": "applied"}
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload), encoding="utf-8")
    (ev_dir / "test.log").write_text("PASS\n", encoding="utf-8")

    run_git(repo, ["add", "answers"])
    run_git(repo, ["commit", "-m", "complete task solution"])
    run_git(repo, ["push", "origin", "main"])
    sha = get_head_sha(repo)

    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id=task_id,
        task_type="BUG",
        title="Test E2E",
        description="Verify guarded submit",
        repository_path=str(repo),
        points=150,
        evidence_required=["evidence/test.log"],
    )
    store.add_task(env)
    store.update_task_status(task_id, WorkerStatus.READY_FOR_REVIEW)

    worker_res = WorkerResult(
        task_id=task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=[f"answers/{task_id}/submit.json", f"answers/{task_id}/evidence/test.log"],
        test_results=[{"command": "pytest", "passed": True}],
        evidence_paths=["evidence/test.log"],
    )

    rev = VerifiedRevision(commit_sha=sha, branch="main", is_clean=True, is_pushed=True)
    guard = SubmissionGuard(store, strict_git_gate=True, require_remote=True)

    gm_submit = MagicMock(return_value={"status": "ACCEPTED", "score": 150, "submission_id": "sub-123"})
    result = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=gm_submit,
        answer_payload=answer_payload,
        assigned_branch_ref="main",
        remote_name="origin",
    )

    assert result.success is True
    assert result.submission_id == "sub-123"
    assert result.points_awarded == 150
    gm_submit.assert_called_once()


def test_submission_guard_blocks_when_forbidden_file_in_worker_changes(offline_git_env):
    repo = offline_git_env["checkout_dir"]
    task_id = "TASK-FORBIDDEN-GUARD"

    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id=task_id,
        task_type="BUG",
        title="Forbidden test",
        description="Verify forbidden block in SubmissionGuard",
        repository_path=str(repo),
        points=100,
    )
    store.add_task(env)

    worker_res = WorkerResult(
        task_id=task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["app.py", ".env"],  # Worker touched .env!
        test_results=[{"command": "pytest", "passed": True}],
        evidence_paths=[],
    )

    rev = VerifiedRevision(commit_sha=get_head_sha(repo), branch="main", is_clean=True)
    guard = SubmissionGuard(store)

    gm_submit = MagicMock()
    with pytest.raises(SubmissionGateError, match="forbidden file '.env'"):
        guard.execute_guarded_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            game_master_submit_fn=gm_submit,
        )

    gm_submit.assert_not_called()
