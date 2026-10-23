"""Phase 1: Dedicated regression and hardening tests for P0.1 - P0.4."""
import sys
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_core.git import git_init, run_git, get_head_sha
from mtm_competition.integration.integration_guard import IntegrationGuard, IntegrationResult
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionGateError
from mtm_competition.verification.qa_gate import validate_qa_gate, QAGateError
from mtm_competition.tasks.store import TaskStore
from mtm_competition.adapters.official_game_master import OfficialGameMasterAdapter, GameMasterAPIError


def test_integration_guard_rollback_preserves_unrelated_untracked_files(tmp_path):
    """P0.1: Rollback must not use `clean -fd` or `reset --hard` that could wipe out unrelated files."""
    local_repo = tmp_path / "canonical_repo"
    git_init(local_repo)
    (local_repo / "app.py").write_text("def app(): pass\n", encoding="utf-8")
    run_git(local_repo, ["add", "app.py"])
    run_git(local_repo, ["commit", "-m", "initial"])

    # Simulate an unrelated, untracked user file in the canonical repo
    unrelated_file = local_repo / "unrelated_notes.txt"
    unrelated_file.write_text("Crucial human notes\n", encoding="utf-8")

    ws = tmp_path / "worker_ws"
    ws.mkdir()
    (ws / "app.py").write_text("invalid syntax !!!\n", encoding="utf-8")

    worker_res = WorkerResult(
        task_id="BUG-ROLLBACK-TEST",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["app.py"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    guard = IntegrationGuard(local_repo, push_to_remote=False, verify_post_integration=True)
    res = guard.integrate(
        worker_res,
        ws,
        verification_cmd=f'{sys.executable} -c "import sys; sys.exit(1)"',
    )

    assert res.success is False
    assert res.verification_passed is False
    # Verify the unrelated untracked file was NOT deleted!
    assert unrelated_file.exists()
    assert unrelated_file.read_text(encoding="utf-8") == "Crucial human notes\n"
    # Verify app.py was restored to original content
    assert (local_repo / "app.py").read_text(encoding="utf-8") == "def app(): pass\n"


def test_integration_guard_blocks_symlinks_and_path_escapes(tmp_path):
    """P0.1: Worker patch cannot contain symlinks or traverse outside repo."""
    local_repo = tmp_path / "repo"
    git_init(local_repo)

    ws = tmp_path / "worker_ws"
    ws.mkdir()
    (ws / "bad.py").write_text("print(1)\n", encoding="utf-8")

    worker_res = WorkerResult(
        task_id="BUG-ESCAPE",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["../bad.py"],
        test_results=[{"command": "pytest", "passed": True}],
    )
    guard = IntegrationGuard(local_repo, push_to_remote=False)
    res = guard.integrate(worker_res, ws)
    assert res.success is False
    assert "Invalid path traversal" in res.error_message


def test_qa_gate_mvs_enforcement_blocks_missing_or_failed_mvs(tmp_path):
    """P0.2: Mandatory MVS enforcement blocks submission if MVS test is missing or failed."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "code.py").write_text("x = 1\n", encoding="utf-8")

    envelope = {
        "task_id": "BUG-MVS-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug",
        "repository_path": str(ws),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
        "mvs": [{"id": "MVS-SPECIAL", "description": "test_special_calculation"}],
    }

    # Worker only ran a generic unrelated test
    worker_res = {
        "task_id": "BUG-MVS-01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["code.py"],
        "test_results": [{"command": "pytest -k test_unrelated", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 5.0,
    }

    with pytest.raises(QAGateError, match="Mandatory MVS requirement 'MVS-SPECIAL'"):
        validate_qa_gate(envelope, worker_res, workspace_dir=ws, reverify_tests=False)


def test_submission_guard_differentiates_rejection_from_timeout(tmp_path):
    """P0.3: 4xx error sets REJECTED immediately, while network timeout keeps SUBMIT_IN_FLIGHT."""
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    git_init(repo)
    rev = GitGuard(repo).verify_head()

    env = TaskEnvelope(
        task_id="BUG-4XX-01",
        task_type="BUG",
        title="4xx Rejection",
        description="Test client error rejection",
        repository_path=str(repo),
        points=150,
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="BUG-4XX-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
    )

    # 1. 409 Conflict rejection
    err_409 = GameMasterAPIError(status_code=409, message="Duplicate submission rejected")
    sub_res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=MagicMock(side_effect=err_409),
    )
    assert sub_res.success is False
    assert sub_res.status == "REJECTED"
    assert store.get_submission("BUG-4XX-01")["status"] == "REJECTED"

    # 2. Network Timeout on a new task
    env_timeout = TaskEnvelope(
        task_id="BUG-TIMEOUT-01",
        task_type="BUG",
        title="Timeout Task",
        description="Test network timeout",
        repository_path=str(repo),
        points=150,
    )
    store.add_task(env_timeout)
    worker_res_to = WorkerResult(
        task_id="BUG-TIMEOUT-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
    )
    sub_res_to = guard.execute_guarded_submission(
        envelope=env_timeout,
        worker_result=worker_res_to,
        revision=rev,
        game_master_submit_fn=MagicMock(side_effect=TimeoutError("Request timed out")),
        game_master_status_fn=MagicMock(side_effect=ConnectionError("GM unreachable")),
    )
    assert sub_res_to.success is False
    assert sub_res_to.status == "SUBMIT_IN_FLIGHT"
    # Preserved in SUBMIT_IN_FLIGHT so reconciliation will handle it later
    assert store.is_in_flight("BUG-TIMEOUT-01") is True


def test_task_store_scoring_separation_and_grading_reconciliation():
    """P0.4: Confirmed points awarded only when Grader confirms PASSED."""
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="BUG-SCORE-01",
        task_type="BUG",
        title="Scoring separation",
        description="Test provisional vs confirmed points",
        repository_path=".",
        points=250,
    )
    store.add_task(env)

    # 1. Submission is ACCEPTED
    store.record_submission("BUG-SCORE-01", status="ACCEPTED", score=250)
    sub = store.get_submission("BUG-SCORE-01")
    assert sub["status"] == "ACCEPTED"
    assert sub["provisional_points"] == 250
    assert sub["confirmed_points"] == 0
    assert store.get_total_confirmed_points() == 0
    assert store.get_total_provisional_points() == 250

    # 2. Grading reconciliation loop queries GM and receives PASSED
    mock_status_check = MagicMock(return_value={"status": "PASSED", "points": 250})
    reconciled = store.reconcile_grading_lifecycle(mock_status_check)
    assert len(reconciled) == 1
    assert reconciled[0]["status"] == "PASSED"

    # Confirmed points are now active!
    sub_passed = store.get_submission("BUG-SCORE-01")
    assert sub_passed["status"] == "PASSED"
    assert sub_passed["confirmed_points"] == 250
    assert store.get_total_confirmed_points() == 250

    # 3. Another task FAILED by grader
    env_failed = TaskEnvelope(
        task_id="BUG-SCORE-02",
        task_type="BUG",
        title="Failed task",
        description="Fails grader",
        repository_path=".",
        points=100,
    )
    store.add_task(env_failed)
    store.record_submission("BUG-SCORE-02", status="ACCEPTED", score=100)
    mock_failed_check = MagicMock(return_value={"status": "FAILED", "points": 0})
    store.reconcile_grading_lifecycle(mock_failed_check)

    sub_fail = store.get_submission("BUG-SCORE-02")
    assert sub_fail["status"] == "FAILED"
    assert sub_fail["confirmed_points"] == 0
    assert sub_fail["provisional_points"] == 0
    # Total confirmed points is still strictly 250 (failed task awarded 0)
    assert store.get_total_confirmed_points() == 250
