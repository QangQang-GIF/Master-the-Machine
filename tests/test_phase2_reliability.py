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
