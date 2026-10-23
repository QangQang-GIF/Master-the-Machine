"""Phase 2: Reliability, Preemption, Clock, and Budget Enforcement Tests."""
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import ExecutionTrace
from mtm_core.config.loader import get_environment_mode, load_environment_config
from mtm_competition.workers.supervisor import WorkerSupervisor, SupervisedProcess
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.tasks.store import TaskStore
from mtm_competition.orchestrator import CompetitionOrchestrator


def test_supervisor_runner_does_not_call_launcher_twice_on_exception(tmp_path):
    """P1.1: If launcher_fn raises an exception, _runner must NOT call launcher_fn a second time."""
    supervisor = WorkerSupervisor(max_workers=1)

    envelope = TaskEnvelope(
        task_id="BUG-CRASH-01",
        task_type="BUG",
        title="Crash task",
        description="Fails on first launch",
        repository_path=str(tmp_path),
        points=100,
    )

    call_count = 0

    def failing_launcher(env, attempt_number=1, on_process_start=None):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("Worker process crashed unexpectedly!")

    proc = supervisor.launch_async(failing_launcher, envelope)

    # Wait for completion
    with pytest.raises(RuntimeError, match="Worker process crashed"):
        supervisor.get_result(envelope.task_id, timeout=2.0)

    # Must be called EXACTLY once!
    assert call_count == 1
    assert proc.status == "FAILED"


def test_preemption_avoids_race_condition_when_already_completed(tmp_path):
    """P1.1: If worker already finished before preemption signal, do not overwrite status to PREEMPTED."""
    supervisor = WorkerSupervisor(max_workers=1)

    envelope = TaskEnvelope(
        task_id="BUG-RACE-01",
        task_type="BUG",
        title="Race task",
        description="Finishes fast",
        repository_path=str(tmp_path),
        points=100,
    )

    worker_res = WorkerResult(task_id="BUG-RACE-01", status=WorkerStatus.READY_FOR_REVIEW)

    def fast_launcher(env, attempt_number=1, on_process_start=None):
        return worker_res, ExecutionTrace(), tmp_path

    proc = supervisor.launch_async(fast_launcher, envelope)
    # Wait until finished
    res = supervisor.get_result(envelope.task_id, timeout=2.0)
    assert res is not None
    assert proc.status == "COMPLETED"

    # Preemption called after completion
    supervisor.preempt_worker(envelope.task_id)
    assert proc.status == "COMPLETED"


def test_paused_match_does_not_drain_budget_and_blocks_dispatch():
    """P1.2: PAUSED match maintains remaining seconds and disallows new task selection."""
    store = TaskStore(":memory:")
    scheduler = TournamentScheduler(store, concurrency_limit=1, match_duration_seconds=1800.0)

    # 1. Sync match as RUNNING with 1000s remaining
    scheduler.sync_with_game_master({"status": "RUNNING", "remaining_seconds": 1000.0})
    assert scheduler.is_paused is False
    assert scheduler.match_remaining_seconds <= 1000.0

    # 2. Match becomes PAUSED
    scheduler.sync_with_game_master({"status": "PAUSED", "remaining_seconds": 950.0})
    assert scheduler.is_paused is True
    assert scheduler.match_remaining_seconds == 950.0

    # Simulate elapsed wall-clock time while match is paused
    time.sleep(0.1)
    # Budget must NOT have decreased!
    assert scheduler.match_remaining_seconds == 950.0

    # Task dispatch blocked while paused
    env = TaskEnvelope(task_id="BUG-PAUSED", task_type="BUG", title="T", description="D", repository_path=".", points=100)
    store.add_task(env)
    assert scheduler.select_next_task() is None


def test_hard_token_budget_blocks_task_launch(tmp_path):
    """P1.3: Hard token budget prevents launching new tasks when budget is exceeded."""
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [{
        "task_id": "BUG-BUDGET-01",
        "task_type": "BUG",
        "title": "Budget Task",
        "description": "Desc",
        "points": 100,
    }]
    gm.get_mvs.return_value = []
    gm.start_working.return_value = {"status": "STARTED"}

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        token_budget=50_000,
    )
    orch.sync_backlog()

    # Simulate existing tokens already at or above budget
    orch.cumulative_billable_tokens = 55_000

    step_res = orch.step()
    assert step_res["status"] == "BUDGET_EXCEEDED"
    assert "token budget exceeded" in step_res["error"]


def test_environment_mode_standardization(monkeypatch):
    """P1.4: MTM_ENV and MTM_MODE standardization with explicit precedence."""
    monkeypatch.delenv("MTM_ENV", raising=False)
    monkeypatch.delenv("MTM_MODE", raising=False)

    assert get_environment_mode() == "development"

    monkeypatch.setenv("MTM_MODE", "production")
    assert get_environment_mode() == "production"

    # MTM_ENV takes precedence over MTM_MODE
    monkeypatch.setenv("MTM_ENV", "staging")
    assert get_environment_mode() == "staging"

    # Explicit argument takes top precedence
    assert get_environment_mode("production") == "production"


def test_supervisor_get_result_lifecycle(tmp_path):
    """P2: Verify WorkerSupervisor.get_result non-blocking, timeout, and preemption behaviors."""
    supervisor = WorkerSupervisor(max_workers=2)

    # Case 1: Worker currently running -> timeout=0.0 returns None non-blockingly
    envelope1 = TaskEnvelope(
        task_id="TASK-POLL-01",
        task_type="BUG",
        title="Poll Task",
        description="Sleeps",
        repository_path=str(tmp_path),
        time_budget_seconds=10,
    )

    def slow_launcher(env, attempt_number=1, on_process_start=None):
        time.sleep(0.3)
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    supervisor.launch_async(slow_launcher, envelope1)

    t0 = time.time()
    res = supervisor.get_result(envelope1.task_id, timeout=0.0)
    elapsed = time.time() - t0
    assert res is None
    assert elapsed < 0.1  # Immediate return, non-blocking

    # Case 2: Worker completes within timeout -> returns result
    res_completed = supervisor.get_result(envelope1.task_id, timeout=2.0)
    assert res_completed is not None
    assert res_completed[0].status == WorkerStatus.READY_FOR_REVIEW

    # Case 3: Worker exceeds timeout window -> returns None, worker continues running
    envelope2 = TaskEnvelope(
        task_id="TASK-TIMEOUT-02",
        task_type="BUG",
        title="Timeout Task",
        description="Sleeps longer",
        repository_path=str(tmp_path),
        time_budget_seconds=10,
    )

    def very_slow_launcher(env, attempt_number=1, on_process_start=None):
        time.sleep(0.5)
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    proc2 = supervisor.launch_async(very_slow_launcher, envelope2)
    t0 = time.time()
    res = supervisor.get_result(envelope2.task_id, timeout=0.05)
    elapsed = time.time() - t0
    assert res is None
    assert 0.04 <= elapsed <= 0.2
    assert not supervisor.is_finished(envelope2.task_id)

    # Case 4: Worker is preempted while running -> returns None, status is PREEMPTED
    supervisor.preempt_worker(envelope2.task_id)
    assert proc2.status == "PREEMPTED"
    res_preempted = supervisor.get_result(envelope2.task_id, timeout=0.5)
    assert res_preempted is None


def test_terminate_process_tree_cross_platform_and_unix_safety():
    """Verify terminate_process_tree cleanly terminates child tree and never kills parent process group."""
    from mtm_core.runtime.process import terminate_process_tree
    import subprocess
    import sys

    # 1. Spawn a real background process that sleeps
    cmd = [sys.executable, "-c", "import time; time.sleep(30)"]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    assert proc.poll() is None
    terminate_process_tree(proc)
    assert proc.poll() is not None

    # 2. Test Unix safety guard: mock pgid == parent_pgid
    mock_proc = MagicMock()
    mock_proc.pid = 99999
    mock_proc.poll.return_value = None
    with patch("sys.platform", "linux"), \
         patch("os.getpgid", return_value=12345, create=True), \
         patch("os.getpgrp", return_value=12345, create=True), \
         patch("signal.SIGKILL", 9, create=True), \
         patch("os.killpg", create=True) as mock_killpg:
        terminate_process_tree(mock_proc)
        # killpg must NEVER be called when child shares parent pgid!
        mock_killpg.assert_not_called()
        mock_proc.kill.assert_called()

    # 3. Test Unix group termination: mock pgid != parent_pgid
    mock_proc_child_group = MagicMock()
    mock_proc_child_group.pid = 99999
    mock_proc_child_group.poll.return_value = None
    with patch("sys.platform", "linux"), \
         patch("os.getpgid", return_value=99999, create=True), \
         patch("os.getpgrp", return_value=12345, create=True), \
         patch("signal.SIGKILL", 9, create=True), \
         patch("os.killpg", create=True) as mock_killpg:
        terminate_process_tree(mock_proc_child_group)
        # killpg MUST be called on child's pgid when distinct from parent!
        mock_killpg.assert_called_once_with(99999, 9)

