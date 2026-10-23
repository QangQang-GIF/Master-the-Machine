"""Comprehensive tests validating P0 and P1 audit findings for Master The Machine 2026.

Verifies:
1. P0.1 Scoring: points=0 is not falsy, FAILED awards 0, PASSED awards confirmed points,
   orchestrator sets TaskState.FAILED on grader failure.
2. P0.2 Git Push: require_remote and verify_pushed=True are strictly enforced; unpushed commits block submission.
3. P0.3 QA Gate: Independent re-verification runs test commands; fake passed claims from workers are rejected.
4. P0.4 Mid-execution preemption: WorkerSupervisor terminates subprocess handles and preempts tasks when PI arrives.
5. P0.5 In-flight & Once-only: Startup reconciliation of SUBMIT_IN_FLIGHT, once-only blocks FAILED tasks.
6. P1: Calibrated server time in calculate_expected_value, auto-enforcement of skill integrity in production.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from mtm_competition.integration.git_guard import GitGuard, VerifiedRevision
from mtm_competition.integration.integration_guard import IntegrationGuard, IntegrationResult
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult, _extract_submission_score
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.scheduling.priority import PriorityQueue
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.tasks.scoring import calculate_expected_value
from mtm_competition.tasks.store import TaskStore
from mtm_competition.verification.qa_gate import QAGateError, reverify_tests_independently, validate_qa_gate
from mtm_competition.workers.supervisor import SupervisedProcess, WorkerSupervisor
from mtm_core.contracts import TaskEnvelope, TaskState, WorkerResult, WorkerStatus
from mtm_core.git import get_head_sha, git_init, run_git
from mtm_core.runtime import ExecutionTrace


# =====================================================================
# 1. P0.1 Scoring Fixes: Zero points, FAILED states, Grading resolution
# =====================================================================

def test_extract_submission_score_distinguishes_zero_from_none():
    # 1. ACCEPTED with points=0 must be 0, not default points
    assert _extract_submission_score({"points": 0}, envelope_points=200, status="ACCEPTED") == 0
    # 2. ACCEPTED without points must be 0 (provisional)
    assert _extract_submission_score({}, envelope_points=200, status="ACCEPTED") == 0
    # 3. ACCEPTED with explicit points
    assert _extract_submission_score({"points": 200}, envelope_points=200, status="ACCEPTED") == 200

    # 4. FAILED with points=0 must be 0
    assert _extract_submission_score({"points": 0}, envelope_points=200, status="FAILED") == 0
    # 5. FAILED without points must be 0
    assert _extract_submission_score({}, envelope_points=200, status="FAILED") == 0

    # 6. PASSED without points awards default points
    assert _extract_submission_score({}, envelope_points=200, status="PASSED") == 200
    # 7. PASSED with points=0 awards 0
    assert _extract_submission_score({"points": 0}, envelope_points=200, status="PASSED") == 0
    # 8. PASSED with partial points awards partial
    assert _extract_submission_score({"points": 150}, envelope_points=200, status="PASSED") == 150


def test_submission_guard_does_not_award_points_on_failed_response(tmp_path):
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="BUG-SCORE-01",
        task_type="BUG",
        title="Score test",
        description="Verify scoring",
        repository_path=str(tmp_path),
        points=200,
    )
    store.add_task(env)
    store.update_task_status("BUG-SCORE-01", TaskState.READY_TO_SUBMIT)

    worker_res = WorkerResult(
        task_id="BUG-SCORE-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=[],
        test_results=[],
        evidence_paths=[],
    )
    rev = VerifiedRevision(commit_sha="abcd1234ef", branch="main", is_clean=True)
    guard = SubmissionGuard(store)

    # GM returns FAILED with points=0
    gm_submit = MagicMock(return_value={"status": "FAILED", "points": 0, "submission_id": "sub-1"})
    res = guard.execute_guarded_submission(env, worker_res, rev, gm_submit)

    assert res.success is False
    assert res.status == "FAILED"
    assert res.points_awarded == 0

    # Verify task state in store is FAILED, not PASSED or SUBMITTED
    task = store.get_task("BUG-SCORE-01")
    assert task["status"] == TaskState.FAILED.value
    sub = store.get_submission("BUG-SCORE-01")
    assert sub["score"] == 0
    assert sub["status"] == "FAILED"


def test_orchestrator_grading_resolution_sets_failed_state(tmp_path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    run_git(repo_dir, ["add", "app.py"])
    run_git(repo_dir, ["commit", "-m", "init"])

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "IN_PROGRESS", "remaining_seconds": 1800, "total_score": 0}
    gm.list_backlog.return_value = [{
        "task_id": "BUG-GRAD-01",
        "task_type": "BUG",
        "title": "Bug",
        "description": "Fix bug",
        "points": 200,
        "evidence_required": [],
    }]
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []
    # Submit returns ACCEPTED with 0 points
    gm.submit.return_value = {"status": "ACCEPTED", "points": 0, "submission_id": "s-1"}
    # Async grader later reports FAILED with 0 points
    gm.get_submission_status.return_value = {"status": "FAILED", "points": 0}

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
    )
    orch.sync_backlog()

    # Worker produces ready fix
    ws_path = tmp_path / "ws"
    ws_path.mkdir(parents=True, exist_ok=True)
    (ws_path / "app.py").write_text("x = 2\n", encoding="utf-8")
    mock_res = WorkerResult(
        task_id="BUG-GRAD-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["app.py"],
        test_results=[{"command": "echo ok", "passed": True}],
        evidence_paths=[],
    )
    with patch.object(orch.launcher, "launch", return_value=(mock_res, ExecutionTrace(), ws_path)):
        step_res = orch.step()

    assert step_res["status"] == "FAILED"
    assert step_res["points"] == 0

    # Ensure store recorded FAILED, not SUBMITTED or REJECTED
    task = orch.store.get_task("BUG-GRAD-01")
    assert task["status"] == TaskState.FAILED.value
    sub = orch.store.get_submission("BUG-GRAD-01")
    assert sub["status"] == "FAILED"
    assert sub["score"] == 0


# =====================================================================
# 2. P0.2 Push & Remote Verification Fixes
# =====================================================================

def test_integration_guard_require_remote_fails_without_origin(tmp_path):
    repo_dir = tmp_path / "local_only"
    git_init(repo_dir)
    (repo_dir / "f.txt").write_text("hello\n", encoding="utf-8")
    run_git(repo_dir, ["add", "f.txt"])
    run_git(repo_dir, ["commit", "-m", "init"])

    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True)
    (ws_dir / "f.txt").write_text("updated\n", encoding="utf-8")

    worker_res = WorkerResult(
        task_id="TASK-REMOTE-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["f.txt"],
        test_results=[],
        evidence_paths=[],
    )

    guard = IntegrationGuard(repo_dir, push_to_remote=True, require_remote=True)
    res = guard.integrate(worker_res, ws_dir)

    assert res.success is False
    assert "not configured" in res.error_message
    assert res.pushed is False


def test_git_guard_verify_head_fails_when_unpushed(tmp_path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "f.txt").write_text("hello\n", encoding="utf-8")
    run_git(repo_dir, ["add", "f.txt"])
    run_git(repo_dir, ["commit", "-m", "init"])
    sha = get_head_sha(repo_dir)

    guard = GitGuard(repo_dir)
    # Without remote, verify_pushed=True must raise ValueError
    with pytest.raises(ValueError, match="remote 'origin' is not configured"):
        guard.verify_head(expected_sha=sha, verify_pushed=True)


def test_orchestrator_blocks_submission_if_push_fails(tmp_path):
    # Set up a bare remote and clone it
    remote_dir = tmp_path / "bare_remote.git"
    res1 = run_git(tmp_path, ["init", "--bare", str(remote_dir)])
    assert res1.returncode == 0, f"git init --bare failed: {res1.stderr}"

    local_dir = tmp_path / "local_repo"
    res2 = run_git(tmp_path, ["clone", str(remote_dir), str(local_dir)])
    assert res2.returncode == 0, f"git clone failed: {res2.stderr}"

    # Explicitly configure Git identity for the cloned repo in test fixture
    run_git(local_dir, ["config", "user.name", "Test Committer"])
    run_git(local_dir, ["config", "user.email", "test@committer.local"])
    run_git(local_dir, ["config", "core.autocrlf", "false"])

    (local_dir / "code.py").write_text("a = 1\n", encoding="utf-8")
    res3 = run_git(local_dir, ["add", "code.py"])
    assert res3.returncode == 0, f"git add failed: {res3.stderr}"

    res4 = run_git(local_dir, ["commit", "-m", "initial"])
    assert res4.returncode == 0, f"git commit failed: {res4.stderr}"

    res5 = run_git(local_dir, ["branch", "-M", "main"])
    assert res5.returncode == 0, f"git branch -M main failed: {res5.stderr}"

    res6 = run_git(local_dir, ["push", "-u", "origin", "main"])
    assert res6.returncode == 0, f"git push failed: {res6.stderr}"

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "IN_PROGRESS", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [{
        "task_id": "CR-PUSH-01",
        "task_type": "CR",
        "title": "Push Test",
        "description": "Test unpushed block",
        "points": 100,
        "evidence_required": [],
    }]
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=local_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=True,
    )
    orch.sync_backlog()

    ws_dir = tmp_path / "ws_push"
    ws_dir.mkdir(parents=True)
    (ws_dir / "code.py").write_text("a = 2\n", encoding="utf-8")

    worker_res = WorkerResult(
        task_id="CR-PUSH-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["code.py"],
        test_results=[{"command": "echo ok", "passed": True}],
        evidence_paths=[],
    )

    # Simulate git push failure in IntegrationGuard
    with patch.object(orch.integration_guard, "integrate", return_value=IntegrationResult(
        success=True,
        commit_sha=get_head_sha(local_dir),
        pushed=False,  # Failed or unpushed!
    )):
        with patch.object(orch.launcher, "launch", return_value=(worker_res, ExecutionTrace(), ws_dir)):
            step_res = orch.step()

    assert step_res["status"] == "INTEGRATION_FAILED"
    assert "was not pushed to remote" in step_res["error"]
    # GM submit must NOT have been called
    gm.submit.assert_not_called()


# =====================================================================
# 3. P0.3 QA Gate Independent Test Execution Fixes
# =====================================================================

def test_reverify_tests_independently_runs_real_commands(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    # 1. Success command
    assert reverify_tests_independently(ws, [f'{sys.executable} -c "import sys; sys.exit(0)"']) is True
    # 2. Failing command
    assert reverify_tests_independently(ws, [f'{sys.executable} -c "import sys; sys.exit(2)"']) is False


def test_qa_gate_blocks_fake_worker_test_claims(tmp_path):
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "solution.py").write_text("print('done')\n", encoding="utf-8")

    envelope = TaskEnvelope(
        task_id="BUG-FAKE-01",
        task_type="BUG",
        title="Bug",
        description="Fix bug",
        repository_path=str(ws),
        points=100,
    ).to_dict()
    # Worker lies that the test passed
    worker_result = WorkerResult(
        task_id="BUG-FAKE-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["solution.py"],
        test_results=[{"command": f'{sys.executable} -c "import sys; sys.exit(1)"', "passed": True}],
        evidence_paths=[],
    ).to_dict()

    # When reverify_tests=True, it executes the command independently and catches the fraud
    with pytest.raises(QAGateError, match="Independent test re-verification failed"):
        validate_qa_gate(envelope, worker_result, workspace_dir=ws, reverify_tests=True)


# =====================================================================
# 4. P0.4 Mid-Execution PI Preemption & Process Termination
# =====================================================================

def test_worker_supervisor_preempt_kills_active_subprocess(tmp_path):
    supervisor = WorkerSupervisor()
    envelope = TaskEnvelope(
        task_id="CR-SLOW-01",
        task_type="CR",
        title="Slow task",
        description="Slow execution",
        repository_path=str(tmp_path),
        time_budget_seconds=30,
    )

    # Launch a slow subprocess
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(15)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    def dummy_launcher(env, attempt_number=1, on_process_start=None):
        if on_process_start:
            on_process_start(proc)
        proc.wait()
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    supervised = supervisor.launch_async(dummy_launcher, envelope)
    time.sleep(0.1)

    assert supervised.status == "RUNNING"
    assert proc.poll() is None  # Still alive

    # Preempt the worker
    preempted = supervisor.preempt_worker("CR-SLOW-01")
    assert preempted is not None
    assert preempted.status == "PREEMPTED"

    # Wait briefly and verify process was killed
    time.sleep(0.2)
    assert proc.poll() is not None  # Process has terminated


def test_orchestrator_mid_execution_pi_preemption(tmp_path):
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "mod.py").write_text("x = 0\n", encoding="utf-8")
    run_git(repo_dir, ["add", "mod.py"])
    run_git(repo_dir, ["commit", "-m", "init"])

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "IN_PROGRESS", "remaining_seconds": 1800}
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []

    # Initial backlog has 1 slow CR task
    backlog_items = [{
        "task_id": "CR-BACKGROUND-01",
        "task_type": "CR",
        "title": "Long running CR",
        "description": "CR task",
        "points": 100,
        "evidence_required": [],
    }]

    def mock_list_backlog():
        return list(backlog_items)

    gm.list_backlog.side_effect = mock_list_backlog

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
    )
    orch.sync_backlog()

    # Worker sleeps for 1.0 second, during which a PI arrives in backlog
    def slow_worker_launch(envelope, attempt_number=1, on_process_start=None):
        # Inject PI task into GM backlog mid-execution
        backlog_items.append({
            "task_id": "PI-OUTAGE-URGENT",
            "task_type": "PI",
            "title": "Urgent Outage",
            "description": "Fix immediately",
            "points": 500,
            "evidence_required": [],
        })
        time.sleep(0.3)
        return WorkerResult(task_id=envelope.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    with patch.object(orch.launcher, "launch", side_effect=slow_worker_launch):
        step_res = orch.step()

    # Must return PREEMPTED!
    assert step_res == {"task_id": "CR-BACKGROUND-01", "status": "PREEMPTED"}

    # Next selected task must be the PI!
    next_task = orch.scheduler.select_next_task()
    assert next_task["task_id"] == "PI-OUTAGE-URGENT"


# =====================================================================
# 5. P0.5 In-Flight Recovery & Once-Only Policy on FAILED
# =====================================================================

def test_task_store_once_only_blocks_failed_task_resubmission(tmp_path):
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="BUG-ONCE-01",
        task_type="BUG",
        title="Once-only test",
        description="Verify once only on fail",
        repository_path=str(tmp_path),
        points=100,
    )
    store.add_task(env)
    store.update_task_status("BUG-ONCE-01", TaskState.READY_TO_SUBMIT)

    # First submission fails
    store.record_submission("BUG-ONCE-01", status="FAILED", score=0)
    assert store.has_submitted("BUG-ONCE-01") is True

    # Attempt second submission -> start_submission must return False
    can_start = store.start_submission("BUG-ONCE-01", "rev2")
    assert can_start is False

    # SubmissionGuard must reject with SubmissionGateError
    guard = SubmissionGuard(store)
    worker_res = WorkerResult(task_id="BUG-ONCE-01", status=WorkerStatus.READY_FOR_REVIEW)
    rev = VerifiedRevision(commit_sha="rev2", branch="main", is_clean=True)
    with pytest.raises(Exception, match="already been submitted"):
        guard.validate_pre_submission(env, worker_res, rev)


def test_orchestrator_startup_reconciles_in_flight_submissions(tmp_path):
    db_file = tmp_path / "persisted.db"
    store = TaskStore(str(db_file))
    env = TaskEnvelope(
        task_id="BUG-CRASH-01",
        task_type="BUG",
        title="Crash Recovery",
        description="Task in flight before crash",
        repository_path=str(tmp_path),
        points=200,
    )
    store.add_task(env)
    # Simulate submission in-flight before sudden crash
    store.start_submission("BUG-CRASH-01", "rev-sha")

    # Game Master says the submission actually passed on server
    gm = MagicMock()
    gm.get_submission_status.return_value = {"status": "PASSED", "points": 200, "submission_id": "sub-crash-1"}
    gm.get_match_state.return_value = {"status": "IN_PROGRESS", "remaining_seconds": 1800}
    gm.list_backlog.return_value = []

    # New orchestrator boots up with the persisted database
    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=str(db_file),
    )

    # Assert submission was reconciled on startup
    sub = orch.store.get_submission("BUG-CRASH-01")
    assert sub["status"] == "PASSED"
    assert sub["score"] == 200
    task = orch.store.get_task("BUG-CRASH-01")
    assert task["status"] == TaskState.PASSED.value


# =====================================================================
# 6. P1: Server-Time Aware EV Scoring & Production Integrity
# =====================================================================

def test_calculate_expected_value_uses_current_time_argument():
    task = {
        "task_id": "BUG-EV-01",
        "task_type": "BUG",
        "points": 100,
        "deadline_epoch": 1000.0,
    }
    # When current server time is 700.0, seconds_left = 300 (which is expected_time * 1.66 -> deadline_factor = 1.5)
    ev_early = calculate_expected_value(task, current_time=700.0)

    # When current server time is 950.0, seconds_left = 50 (< expected_time 180 -> deadline_factor = 0.5)
    ev_late = calculate_expected_value(task, current_time=950.0)

    # Imminent deadline has lower EV due to high risk of expiry
    assert ev_early > ev_late


def test_production_mode_enforces_skill_integrity_check(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_ENV", "production")
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "IN_PROGRESS"}
    gm.list_backlog.return_value = []

    # In production mode, verify_skill_integrity should automatically be True
    with patch("mtm_core.config.loader.verify_skill_checksums") as mock_verify:
        orch = CompetitionOrchestrator(
            gm_adapter=gm,
            repository_path=tmp_path,
            scratch_dir=tmp_path / "scratch",
            db_path=":memory:",
            verify_skill_integrity=False,  # Explicitly False, but overridden by production
        )
        assert orch.verify_skill_integrity is True
        mock_verify.assert_called_once()
