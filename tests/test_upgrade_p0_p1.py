"""Comprehensive verification tests for P0, P1, and CLI Backend upgrades."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import ExecutionTrace, CodexRuntime
from mtm_core.config.loader import verify_skill_checksums
from mtm_core.git import git_init, get_head_sha, run_git
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.state_machine import TaskStateMachine
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.verification.qa_gate import validate_qa_gate, QAGateError
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.integration_guard import IntegrationGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionGateError
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterAPIError,
    GameMasterConnectionError,
)
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.workers.supervisor import WorkerSupervisor


def test_official_game_master_adapter_mocked_http():
    """Verify OfficialGameMasterAdapter performs correct HTTP requests and parses responses."""
    adapter = OfficialGameMasterAdapter(api_base_url="http://test-server:9000", api_key="secret-token", team_id="team-shark")

    fake_start_response = {
        "backlogId": "BUG-1",
        "replay": False,
        "submitTemplate": {"status": "SUBMITTED"},
        "minimumVerificationScenarios": [{"scenario": "test"}],
    }

    with patch.object(adapter, "_request") as mock_req:
        mock_req.return_value = fake_start_response

        res = adapter.start_working("8c0e1b22-1111-4222-8333-444444444444", "BUG-1")
        assert res["backlogId"] == "BUG-1"
        assert res["replay"] is False
        mock_req.assert_called_with(
            "POST",
            "/api/v1/matches/8c0e1b22-1111-4222-8333-444444444444/backlogs/start",
            data={"backlogItemKey": "BUG-1"},
        )


def test_official_game_master_adapter_client_error_no_retry():
    """4xx errors should immediately raise GameMasterAPIError without futile retries."""
    adapter = OfficialGameMasterAdapter(
        api_base_url="http://test-server:9000",
        match_uuid="8c0e1b22-1111-4222-8333-444444444444",
    )

    import urllib.error
    import io

    # Mock an HTTP 409 Conflict (e.g. duplicate submission)
    err = urllib.error.HTTPError(
        url="http://test-server:9000/api/v1/matches/8c0e1b22-1111-4222-8333-444444444444/submissions",
        code=409,
        msg="Conflict",
        hdrs={},
        fp=io.BytesIO(b'{"error": {"code": "ALREADY_SUBMITTED", "message": "Task already submitted"}}'),
    )

    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(GameMasterAPIError) as exc_info:
            adapter.submit("BUG-1", "a" * 40, answer={"status": "SUBMITTED"})
        assert exc_info.value.status_code == 409
        assert "already submitted" in exc_info.value.message.lower()


def test_git_push_pipeline_in_integration_guard(tmp_path):
    """Verify IntegrationGuard stages, commits, and pushes to a configured Git remote."""
    # 1. Setup bare remote
    remote_dir = tmp_path / "remote_repo.git"
    run_git(tmp_path, ["init", "--bare", str(remote_dir)])

    # 2. Setup local repo tracking bare remote
    local_repo = tmp_path / "local_repo"
    git_init(local_repo)
    run_git(local_repo, ["remote", "add", "origin", str(remote_dir)])

    # Initial commit pushed to remote
    (local_repo / "initial.txt").write_text("v1\n")
    run_git(local_repo, ["add", "initial.txt"])
    run_git(local_repo, ["commit", "-m", "init"])
    run_git(local_repo, ["push", "-u", "origin", "main"])

    # 3. Setup worker workspace with a fix
    ws_dir = tmp_path / "worker_ws"
    ws_dir.mkdir()
    (ws_dir / "service.py").write_text("def run(): return 'fixed'\n")

    worker_res = WorkerResult(
        task_id="CR-PUSH-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["service.py"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    # 4. Integrate and push
    guard = IntegrationGuard(local_repo, push_to_remote=True, remote_name="origin")
    res = guard.integrate(worker_res, ws_dir)

    assert res.success is True
    assert res.pushed is True
    assert res.commit_sha is not None

    # 5. Verify GitGuard confirms HEAD is clean and pushed
    git_guard = GitGuard(local_repo)
    verified = git_guard.verify_head(expected_sha=res.commit_sha, verify_pushed=True)
    assert verified.is_clean is True
    assert verified.is_pushed is True


def test_integration_guard_post_verification_failure_rollbacks(tmp_path):
    """If post-integration verification fails on target repo, changes are rolled back."""
    local_repo = tmp_path / "local_repo"
    git_init(local_repo)
    (local_repo / "calc.py").write_text("def add(a, b): return a + b\n")
    run_git(local_repo, ["add", "-A"])
    run_git(local_repo, ["commit", "-m", "base"])
    base_sha = get_head_sha(local_repo)

    ws_dir = tmp_path / "worker_ws"
    ws_dir.mkdir()
    (ws_dir / "calc.py").write_text("syntax error !!!\n")

    worker_res = WorkerResult(
        task_id="BUG-ROLLBACK",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["calc.py"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    # Use a failing verification command (exit 1)
    guard = IntegrationGuard(local_repo, push_to_remote=False, verify_post_integration=True)
    res = guard.integrate(worker_res, ws_dir, verification_cmd="python -c 'import calc'")

    assert res.success is False
    assert res.verification_passed is False
    # Ensure local repo was cleanly rolled back to base_sha
    assert get_head_sha(local_repo) == base_sha
    assert "def add(a, b): return a + b" in (local_repo / "calc.py").read_text()


def test_qa_gate_blocks_cr_bug_without_test_results(tmp_path):
    """QA Gate strictly blocks CR, BUG, and PI with READY_FOR_REVIEW if test_results is empty."""
    envelope = {
        "task_id": "BUG-NO-TESTS",
        "task_type": "BUG",
        "title": "Bug without tests",
        "description": "Fix bug",
        "repository_path": str(tmp_path),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@1.0.0"],
        "time_budget_seconds": 300,
    }
    (tmp_path / "fix.py").write_text("x = 1\n")

    result_no_tests = {
        "task_id": "BUG-NO-TESTS",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@1.0.0"],
        "changed_files": ["fix.py"],
        "test_results": [],  # Empty test results on BUG!
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }

    with pytest.raises(QAGateError, match="requires test verification, but test_results is empty"):
        validate_qa_gate(envelope, result_no_tests, workspace_dir=tmp_path)


def test_scheduler_server_clock_and_paused_state():
    """TournamentScheduler correctly synchronizes with server clock and pauses execution."""
    store = TaskStore(":memory:")
    scheduler = TournamentScheduler(store, match_duration_seconds=1000)

    # 1. Synchronize with remote server state (server clock 50s ahead of local)
    server_now = time.time() + 50.0
    scheduler.sync_with_game_master({
        "status": "RUNNING",
        "remaining_seconds": 800.0,
        "server_epoch": server_now,
    })

    assert scheduler.clock_offset == pytest.approx(50.0, abs=1.0)
    assert scheduler.current_server_time == pytest.approx(server_now, abs=1.0)
    assert scheduler.match_remaining_seconds == pytest.approx(800.0, abs=2.0)
    assert not scheduler.is_paused

    # 2. Simulate match PAUSED
    scheduler.sync_with_game_master({
        "status": "PAUSED",
        "remaining_seconds": 800.0,
        "server_epoch": server_now + 10.0,
    })
    assert scheduler.is_paused is True
    # In PAUSED mode, select_next_task must return None
    t = TaskEnvelope(task_id="T1", task_type="TASK", title="t", description="d", repository_path="/tmp")
    store.add_task(t)
    assert scheduler.select_next_task() is None


def test_pi_preemption_logic():
    """Scheduler detects when an incoming PI should preempt a running worker."""
    store = TaskStore(":memory:")
    scheduler = TournamentScheduler(store)

    # Task 1: Running long CR task (running for 10 seconds of 300s budget)
    cr_task = TaskEnvelope(task_id="CR-SLOW", task_type="CR", title="Slow", description="d", repository_path="/tmp", time_budget_seconds=300)
    store.add_task(cr_task)
    scheduler.assign_task("CR-SLOW")

    # Incoming PI task
    pi_task = {"task_id": "PI-EMERGENCY", "task_type": "PI", "points": 300}

    # Scheduler check_preemption should target CR-SLOW
    preempt_id = scheduler.check_preemption(pi_task)
    assert preempt_id == "CR-SLOW"

    # Requeue CR-SLOW
    scheduler.requeue_task("CR-SLOW")
    assert "CR-SLOW" not in scheduler.running_tasks
    t = store.get_task("CR-SLOW")
    assert t["status"] == TaskState.QUEUED.value


def test_submission_in_flight_timeout_recovery(tmp_path):
    """If network times out during submit, SubmissionGuard checks Game Master before retrying."""
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    git_init(repo)
    rev = GitGuard(repo).verify_head()

    env = TaskEnvelope(
        task_id="BUG-RECON",
        task_type="BUG",
        title="Reconcile",
        description="d",
        repository_path=str(repo),
        points=200,
    )
    store.add_task(env)
    worker_res = WorkerResult(task_id="BUG-RECON", status=WorkerStatus.READY_FOR_REVIEW, test_results=[{"command": "pytest", "passed": True}])

    # 1. Simulate network timeout on submit call
    mock_submit_timeout = MagicMock(side_effect=TimeoutError("Connection timed out to Game Master"))
    # Server actually recorded it as ACCEPTED despite timeout
    mock_status_fn = MagicMock(return_value={"status": "ACCEPTED", "points": 200, "task_id": "BUG-RECON"})

    sub_res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=mock_submit_timeout,
        game_master_status_fn=mock_status_fn,
    )

    # Reconciled successfully!
    assert sub_res.success is True
    assert sub_res.points_awarded == 200
    assert store.has_submitted("BUG-RECON") is True


def test_skill_checksum_verification():
    """Verify production skill checksum verification succeeds on repo skills and catches tampering."""
    project_root = Path(__file__).resolve().parent.parent

    # Legitimate skills check passes
    assert verify_skill_checksums(project_root) is True

    # Tampering test: temporary wrong checksum file
    tmp_cs = project_root / "releases" / "tampered.sha256"
    tmp_cs.write_text("badhash00000000000000000000000000000000000000000000000000000000  .agents/skills/mtm-bug-worker/SKILL.md\n")
    try:
        with pytest.raises(RuntimeError, match="Skill checksum mismatch"):
            verify_skill_checksums(project_root, checksum_file=tmp_cs)
    finally:
        if tmp_cs.exists():
            tmp_cs.unlink()
