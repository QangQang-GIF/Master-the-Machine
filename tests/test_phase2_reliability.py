"""Phase 2: Reliability, Preemption, Clock, and Budget Enforcement Tests."""
import os
import subprocess
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import ExecutionTrace
from mtm_core.runtime.process import is_pid_alive
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


def test_terminate_process_tree_kills_grandchild_processes(tmp_path):
    """Verify terminate_process_tree kills parent -> child -> grandchild tree and verifies grandchild PID is dead."""
    from mtm_core.runtime.process import terminate_process_tree, is_pid_alive
    import subprocess
    import sys

    pid_file = tmp_path / "grandchild.pid"
    grandchild_code = (
        f"import os, time, pathlib; "
        f"pathlib.Path({repr(str(pid_file))}).write_text(str(os.getpid()), encoding='utf-8'); "
        f"time.sleep(120)"
    )
    parent_script = (
        f"import subprocess, sys, time; "
        f"child = subprocess.Popen([sys.executable, '-c', {repr(grandchild_code)}]); "
        f"time.sleep(120)"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", parent_script],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )

    # Wait until grandchild writes its PID file
    deadline = time.time() + 5.0
    while not pid_file.exists() and time.time() < deadline:
        time.sleep(0.05)

    assert pid_file.exists(), "Grandchild process failed to write PID file"
    grandchild_pid = int(pid_file.read_text(encoding="utf-8").strip())
    assert proc.poll() is None, "Parent should still be running"
    assert is_pid_alive(grandchild_pid), "Grandchild should be alive before termination"

    # Terminate the entire process tree
    terminate_process_tree(proc)

    # Both parent and grandchild must be terminated
    proc.wait(timeout=3)
    assert proc.poll() is not None, "Parent process should be terminated"

    # Allow brief OS cleanup window and verify grandchild PID is gone
    deadline_dead = time.time() + 3.0
    while is_pid_alive(grandchild_pid) and time.time() < deadline_dead:
        time.sleep(0.1)
    assert not is_pid_alive(grandchild_pid), f"Grandchild process {grandchild_pid} must be terminated"


def test_orchestrator_worker_timeout_state_machine(tmp_path):
    """P1 State Machine: When worker times out in Orchestrator.step(), state transitions to TIMED_OUT and frees capacity."""
    from mtm_core.git import git_init, run_git

    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    run_git(repo_dir, ["add", "app.py"])
    run_git(repo_dir, ["commit", "-m", "init"])

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []
    gm.list_backlog.return_value = [{
        "task_id": "BUG-TO-01",
        "task_type": "BUG",
        "title": "Slow Bug",
        "description": "Times out",
        "points": 100,
        "time_budget_seconds": 1,
    }]

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
    )
    orch.sync_backlog()

    def hanging_launcher(envelope, attempt_number=1, on_process_start=None):
        time.sleep(3.0)
        return WorkerResult(task_id=envelope.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    with patch.object(orch.launcher, "launch", side_effect=hanging_launcher):
        res = orch.step()

    assert res["task_id"] == "BUG-TO-01"
    assert res["status"] == "TIMED_OUT"
    assert orch.store.get_task("BUG-TO-01")["status"] == TaskState.TIMED_OUT.value
    assert orch.scheduler.active_worker_count == 0
    assert orch.supervisor.active_worker_count() == 0
    assert orch.store.get_attempt_count("BUG-TO-01") == 1


def test_orchestrator_pi_preemption_and_full_resume_lifecycle(tmp_path):
    """P1 State Machine: CR is preempted by incoming PI mid-step -> PI executes & submits -> CR resumes & submits."""
    from mtm_core.git import git_init, run_git

    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "service.py").write_text("status = 'down'\n", encoding="utf-8")
    run_git(repo_dir, ["add", "service.py"])
    run_git(repo_dir, ["commit", "-m", "init"])

    backlog_items = [{
        "task_id": "CR-SLOW-01",
        "task_type": "CR",
        "title": "Feature CR",
        "description": "Add feature",
        "points": 200,
        "time_budget_seconds": 60,
    }]

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []
    gm.list_backlog.side_effect = lambda: list(backlog_items)
    gm.submit.side_effect = lambda tid, rev, payload=None: {"status": "PASSED", "points": 500 if "PI" in tid else 200}
    gm.get_submission_status.side_effect = lambda tid: {"status": "PASSED", "points": 500 if "PI" in tid else 200}

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
        verify_post_integration=False,
    )
    orch.sync_backlog()

    cr_attempts = []

    def dynamic_launcher(envelope, attempt_number=1, on_process_start=None):
        ws = orch.launcher.workspace_mgr.prepare_workspace(repo_dir, envelope.task_id, attempt=attempt_number)
        if envelope.task_id == "CR-SLOW-01":
            cr_attempts.append(attempt_number)
            if len(cr_attempts) == 1:
                # First run of CR: PI arrives while CR is running!
                backlog_items.append({
                    "task_id": "PI-URGENT-01",
                    "task_type": "PI",
                    "title": "Production Outage",
                    "description": "Urgent fix",
                    "points": 500,
                    "time_budget_seconds": 60,
                })
                time.sleep(0.3)
            (ws / "service.py").write_text("status = 'cr_done'\n", encoding="utf-8")
        else:
            (ws / "service.py").write_text("status = 'pi_fixed'\n", encoding="utf-8")

        return (
            WorkerResult(
                task_id=envelope.task_id,
                status=WorkerStatus.READY_FOR_REVIEW,
                changed_files=["service.py"],
                test_results=[{"command": "pytest", "passed": True}],
                elapsed_seconds=0.1,
            ),
            ExecutionTrace(),
            ws,
        )

    with patch.object(orch.launcher, "launch", side_effect=dynamic_launcher):
        # Step 1: CR starts, gets preempted by PI
        step1 = orch.step()
        assert step1 == {"task_id": "CR-SLOW-01", "status": "PREEMPTED"}
        assert orch.store.get_task("CR-SLOW-01")["status"] == TaskState.QUEUED.value
        assert orch.scheduler.active_worker_count == 0

        # Step 2: PI is prioritized and completes to PASSED
        step2 = orch.step()
        assert step2["task_id"] == "PI-URGENT-01"
        assert step2["status"] == "PASSED"
        assert step2["points"] == 500

        # Step 3: CR resumes from QUEUED and completes to PASSED
        step3 = orch.step()
        assert step3["task_id"] == "CR-SLOW-01"
        assert step3["status"] == "PASSED"
        assert step3["points"] == 200

    assert orch.store.get_total_confirmed_points() == 700


def test_orchestrator_start_working_failure_does_not_leak_scheduler_capacity(tmp_path):
    """P1 State Machine: Transient errors on start_working/get_mvs retry with backoff and requeue to QUEUED
    (with cooldown so next tasks run without leaking scheduler capacity), while permanent 4xx errors
    transition immediately to REJECTED without wasting retries.
    """
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [
        {"task_id": "BUG-FAIL-START", "task_type": "BUG", "title": "T1", "description": "D1", "points": 300},
        {"task_id": "BUG-PERM-400", "task_type": "BUG", "title": "T2", "description": "D2", "points": 200},
        {"task_id": "BUG-NEXT-OK", "task_type": "BUG", "title": "T3", "description": "D3", "points": 100},
    ]

    call_counts = {"BUG-FAIL-START": 0, "BUG-PERM-400": 0, "BUG-NEXT-OK": 0}

    def start_working_side_effect(tid):
        call_counts[tid] = call_counts.get(tid, 0) + 1
        if tid == "BUG-FAIL-START":
            raise RuntimeError("GM 503 Service Unavailable")
        if tid == "BUG-PERM-400":
            raise RuntimeError("HTTP 400 Bad Request: Invalid task state")
        return {"status": "STARTED"}

    gm.start_working.side_effect = start_working_side_effect
    gm.get_mvs.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
        start_working_max_retries=2,
        start_working_backoff_sec=0.001,
        start_working_max_requeues=1,
    )
    orch.sync_backlog()

    # Step 1: BUG-FAIL-START encounters transient 503 -> retries in-step (1 initial + 2 retries = 3 calls),
    # then requeues to QUEUED with a cooldown instead of immediately discarding as REJECTED!
    res1 = orch.step()
    assert res1["task_id"] == "BUG-FAIL-START"
    assert res1["status"] == "TRANSIENT_RETRY_QUEUED"
    assert call_counts["BUG-FAIL-START"] == 3
    assert orch.store.get_task("BUG-FAIL-START")["status"] == TaskState.QUEUED.value
    # Capacity MUST be freed!
    assert orch.scheduler.has_capacity is True
    assert orch.scheduler.active_worker_count == 0

    # Step 2: Because BUG-FAIL-START is on transient cooldown, scheduler selects BUG-PERM-400 next.
    # BUG-PERM-400 fails with permanent 400 -> NO retries (1 call only), immediately REJECTED!
    res2 = orch.step()
    assert res2["task_id"] == "BUG-PERM-400"
    assert res2["status"] == "FAILED"
    assert call_counts["BUG-PERM-400"] == 1
    assert orch.store.get_task("BUG-PERM-400")["status"] == TaskState.REJECTED.value
    assert orch.scheduler.has_capacity is True
    assert orch.scheduler.active_worker_count == 0

    # Step 3: Scheduler selects BUG-NEXT-OK without deadlocking
    next_candidate = orch.scheduler.select_next_task()
    assert next_candidate is not None
    assert next_candidate["task_id"] == "BUG-NEXT-OK"

    # Step 4: If BUG-FAIL-START cooldown expires and it fails again beyond start_working_max_requeues=1,
    # it finally transitions to REJECTED so it cannot loop forever.
    orch.scheduler.clear_cooldown("BUG-FAIL-START")
    orch.store.update_task_status("BUG-NEXT-OK", TaskState.PASSED)
    res4 = orch.step()
    assert res4["task_id"] == "BUG-FAIL-START"
    assert res4["status"] == "FAILED"
    assert orch.store.get_task("BUG-FAIL-START")["status"] == TaskState.REJECTED.value


def test_orchestrator_start_working_transient_recovers_in_step(tmp_path):
    """P1: Transient 503/429 during start_working or get_mvs succeeds when server recovers on retry."""
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [
        {"task_id": "BUG-FLAKY-503", "task_type": "BUG", "title": "Flaky", "description": "D", "points": 250},
    ]

    attempts = {"start": 0, "mvs": 0}

    def flaky_start(tid):
        attempts["start"] += 1
        if attempts["start"] == 1:
            raise RuntimeError("HTTP 503 Service Unavailable")
        return {"status": "STARTED"}

    def flaky_mvs(tid):
        attempts["mvs"] += 1
        if attempts["mvs"] == 1:
            raise RuntimeError("HTTP 429 Too Many Requests")
        return []

    gm.start_working.side_effect = flaky_start
    gm.get_mvs.side_effect = flaky_mvs
    gm.submit.return_value = {"status": "PASSED", "score": 250}

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
        start_working_max_retries=2,
        start_working_backoff_sec=0.001,
    )
    orch.sync_backlog()

    from mtm_competition.integration.integration_guard import IntegrationResult
    from mtm_competition.integration.git_guard import VerifiedRevision
    (tmp_path / "src").mkdir(parents=True, exist_ok=True)
    (tmp_path / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")

    def fast_worker(envelope, attempt_number=1, on_process_start=None, cancel_event=None):
        res = WorkerResult(
            task_id=envelope.task_id,
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=["src/app.py"],
            test_results=[{"command": "pytest", "passed": True, "output": "ok"}],
        )
        return res, ExecutionTrace(), tmp_path

    with patch.object(orch.launcher, "launch", side_effect=fast_worker), \
         patch.object(orch.integration_guard, "integrate", return_value=IntegrationResult(success=True, commit_sha="sha-flaky", pushed=False)), \
         patch.object(orch.git_guard, "verify_head", return_value=VerifiedRevision(commit_sha="sha-flaky", branch="main", is_clean=True)):
        step_res = orch.step()

    assert step_res["task_id"] == "BUG-FLAKY-503"
    assert step_res["status"] == "PASSED"
    assert step_res["points"] == 250
    assert attempts["start"] == 2
    assert attempts["mvs"] == 2


def test_orchestrator_crash_recovery_requeues_orphaned_running_tasks(tmp_path):
    """P1 State Machine: Tasks orphaned in RUNNING/ASSIGNED state during a process crash are requeued on startup."""
    db_file = tmp_path / "crash_recovery.db"
    store = TaskStore(str(db_file))
    env = TaskEnvelope(
        task_id="PI-ORPHAN-01",
        task_type="PI",
        title="Orphaned PI",
        description="Crashed mid-run",
        repository_path=str(tmp_path),
        points=500,
    )
    store.add_task(env)
    store.update_task_status("PI-ORPHAN-01", TaskState.RUNNING)
    assert store.get_task("PI-ORPHAN-01")["status"] == "RUNNING"

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1500}
    gm.list_backlog.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=str(db_file),
    )

    # Must be automatically recovered back to QUEUED on startup!
    recovered_task = orch.store.get_task("PI-ORPHAN-01")
    assert recovered_task["status"] == TaskState.QUEUED.value
    selected = orch.scheduler.select_next_task()
    assert selected is not None
    assert selected["task_id"] == "PI-ORPHAN-01"


def test_orchestrator_match_finished_mid_execution_cancels_worker(tmp_path):
    """P1 State Machine: If match transitions to FINISHED while worker is running, worker is cancelled and not submitted."""
    gm = MagicMock()
    match_states = [
        {"status": "RUNNING", "remaining_seconds": 100},
        {"status": "FINISHED", "remaining_seconds": 0},
    ]
    gm.get_match_state.side_effect = lambda: match_states[0] if len(match_states) == 1 else match_states.pop(0)
    gm.start_working.return_value = {"status": "STARTED"}
    gm.get_mvs.return_value = []
    gm.list_backlog.return_value = [{
        "task_id": "BUG-FIN-01",
        "task_type": "BUG",
        "title": "Late task",
        "description": "Match ends mid-run",
        "points": 100,
    }]

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
    )
    orch.sync_backlog()
    # Reset match_states so step() sees RUNNING first, then FINISHED inside the polling loop
    match_states[:] = [
        {"status": "RUNNING", "remaining_seconds": 10},
        {"status": "FINISHED", "remaining_seconds": 0},
    ]

    def slow_worker(envelope, attempt_number=1, on_process_start=None):
        time.sleep(0.5)
        return WorkerResult(task_id=envelope.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), tmp_path

    with patch.object(orch.launcher, "launch", side_effect=slow_worker):
        res = orch.step()

    assert res == {"task_id": "BUG-FIN-01", "status": "FINISHED"}
    assert orch.store.get_task("BUG-FIN-01")["status"] == TaskState.ABORTED.value
    assert orch.scheduler.active_worker_count == 0
    assert orch.supervisor.active_worker_count() == 0
    gm.submit.assert_not_called()


def test_crash_recovery_reconciles_with_gm_and_enforces_fencing_tokens(tmp_path):
    """P1 Recovery & Retry Safety:
    1. Reconciles orphaned ASSIGNED/RUNNING/VERIFYING tasks with Game Master before requeuing
       (so tasks already graded on server before local DB commit are marked PASSED, not re-run).
    2. Terminates stale zombie worker PIDs recorded in SQLite on startup.
    3. Rotates fencing tokens on recovery and blocks stale attempts from mutating state or submitting.
    4. Preserves READY_TO_SUBMIT across crash boundaries.
    """
    db_file = tmp_path / "crash_fencing.db"
    store = TaskStore(str(db_file))

    for tid, state, pts in [
        ("BUG-GRADED-ON-GM", TaskState.VERIFYING, 200),
        ("BUG-ZOMBIE-PID", TaskState.RUNNING, 150),
        ("BUG-ASSIGNED-ORPHAN", TaskState.ASSIGNED, 100),
        ("BUG-READY-SUBMIT", TaskState.READY_TO_SUBMIT, 300),
    ]:
        store.add_task(
            TaskEnvelope(
                task_id=tid,
                task_type="BUG",
                title=tid,
                description="Boundary crash test",
                repository_path=str(tmp_path),
                points=pts,
            )
        )
        store.update_task_status(tid, state)

    # Spawn a real sleeper process to act as an orphaned zombie worker from a crashed orchestrator
    zombie_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stale_token = store.issue_fencing_token("BUG-ZOMBIE-PID", active_pid=zombie_proc.pid)
    assert store.validate_fencing_token("BUG-ZOMBIE-PID", stale_token) is True

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1500}
    gm.list_backlog.return_value = []

    def gm_status_side_effect(tid):
        if tid == "BUG-GRADED-ON-GM":
            return {"status": "PASSED", "score": 200.0}
        return {"status": "WORKING"}

    gm.get_submission_status.side_effect = gm_status_side_effect

    try:
        orch = CompetitionOrchestrator(
            gm_adapter=gm,
            repository_path=tmp_path,
            scratch_dir=tmp_path / "scratch",
            db_path=str(db_file),
        )

        # 1. Task already graded on GM must be reconciled to PASSED (not downgraded to QUEUED!)
        graded_row = orch.store.get_task("BUG-GRADED-ON-GM")
        assert graded_row["status"] == TaskState.PASSED.value
        assert graded_row["awarded_points"] == 200.0

        # 2. Zombie worker process must be terminated on recovery and task reset to QUEUED
        zombie_proc.wait(timeout=5.0)
        assert zombie_proc.poll() is not None
        zombie_row = orch.store.get_task("BUG-ZOMBIE-PID")
        assert zombie_row["status"] == TaskState.QUEUED.value
        assert zombie_row["active_pid"] is None

        # 3. Stale fencing token must now be invalid and rejected at state boundaries
        assert orch.store.validate_fencing_token("BUG-ZOMBIE-PID", stale_token) is False
        with pytest.raises(ValueError, match="Fencing token mismatch"):
            orch.store.transition_state(
                "BUG-ZOMBIE-PID",
                TaskState.VERIFYING,
                fencing_token=stale_token,
            )

        # 4. ASSIGNED orphan is reset to QUEUED, while READY_TO_SUBMIT remains READY_TO_SUBMIT
        assert orch.store.get_task("BUG-ASSIGNED-ORPHAN")["status"] == TaskState.QUEUED.value
        assert orch.store.get_task("BUG-READY-SUBMIT")["status"] == TaskState.READY_TO_SUBMIT.value
    finally:
        if zombie_proc.poll() is None:
            zombie_proc.kill()


def test_ready_to_submit_resumes_directly_without_rerunning_worker(tmp_path):
    """P2 Scheduler & Orchestrator: Tasks in READY_TO_SUBMIT are NOT downgraded to QUEUED,
    and Orchestrator.step() resumes them directly at integration/submission without re-running the LLM worker,
    while still verifying the Git commit via GitGuard.verify_head.
    """
    from mtm_core.git import get_head_sha, git_init, run_git

    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "app.py").write_text("x = 42\n", encoding="utf-8")
    run_git(repo_dir, ["add", "."])
    run_git(repo_dir, ["commit", "-m", "verified commit"])
    real_head_sha = get_head_sha(repo_dir)

    db_file = tmp_path / "ready_to_submit.db"
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = []
    gm.submit.return_value = {"status": "PASSED", "score": 350}

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=str(db_file),
        push_to_remote=False,
    )

    ws_path = tmp_path / "scratch" / "ws_CR-READY-01_att1"
    ws_path.mkdir(parents=True, exist_ok=True)
    verified_res = WorkerResult(
        task_id="CR-READY-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["src/app.py"],
        test_results=[{"command": "pytest", "passed": True, "output": "ok"}],
    )

    orch.store.add_task(
        TaskEnvelope(
            task_id="CR-READY-01",
            task_type="CR",
            title="Already QA Verified",
            description="Should not re-run worker",
            repository_path=str(repo_dir),
            points=350,
        )
    )
    import json
    orch.store.save_verified_checkpoint(
        "CR-READY-01",
        worker_result_json=json.dumps(verified_res.to_dict()),
        workspace_path=str(ws_path),
        revision_sha=real_head_sha,
    )
    orch.store.update_task_status("CR-READY-01", TaskState.READY_TO_SUBMIT)

    # Scheduler must select CR-READY-01 while preserving READY_TO_SUBMIT state
    candidate = orch.scheduler.select_next_task()
    assert candidate is not None
    assert candidate["task_id"] == "CR-READY-01"
    assert candidate["status"] == TaskState.READY_TO_SUBMIT.value
    assert orch.store.get_task("CR-READY-01")["status"] == TaskState.READY_TO_SUBMIT.value

    # Orchestrator step() must submit directly without invoking launcher.launch or gm.start_working
    with patch.object(orch.launcher, "launch") as mock_launch:
        step_res = orch.step()
        mock_launch.assert_not_called()
        gm.start_working.assert_not_called()

    assert step_res["task_id"] == "CR-READY-01"
    assert step_res["status"] == "PASSED"
    assert step_res["points"] == 350
    assert orch.store.get_task("CR-READY-01")["status"] == TaskState.PASSED.value
    gm.submit.assert_called_once_with(
        task_id="CR-READY-01",
        git_revision=real_head_sha,
        evidence=[],
    )


def test_worker_supervisor_real_subprocess_quiescence_on_timeout_and_preempt(tmp_path):
    """P2 WorkerSupervisor: Real worker with child/grandchild processes and startup race
    must leave zero surviving processes and zero workspace file writes after timeout or preemption.
    """
    supervisor = WorkerSupervisor(max_workers=2)

    # Case 1: Worker with real child + grandchild process tree continuously writing to workspace,
    # plus a Python post-communicate write attempt that would race if not gated by cancel_event.
    ws_timeout = tmp_path / "ws_timeout"
    ws_timeout.mkdir(parents=True, exist_ok=True)
    pid_file = ws_timeout / "pids.txt"
    heartbeat_file = ws_timeout / "heartbeat.txt"
    post_write_file = ws_timeout / "post_cancel_leak.txt"

    child_code = (
        "import subprocess, sys, time, pathlib\n"
        f"ws = pathlib.Path(r'{ws_timeout}')\n"
        "grandchild = subprocess.Popen([sys.executable, '-c', "
        "\"import time, pathlib; p = pathlib.Path(r'\" + str(ws / 'heartbeat.txt') + \"'); \"\n"
        "\"\\nwhile True:\\n p.write_text(str(time.time_ns()))\\n time.sleep(0.01)\"])\n"
        f"pathlib.Path(r'{pid_file}').write_text(f'{{grandchild.pid}}')\n"
        "time.sleep(60)\n"
    )

    def real_tree_worker(env: TaskEnvelope, attempt_number: int = 1, on_process_start=None, cancel_event=None):
        p = subprocess.Popen(
            [sys.executable, "-c", child_code],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        if on_process_start:
            on_process_start(p)
        try:
            p.communicate(timeout=60)
        except Exception:
            pass
        # Simulate CodexRuntime post-communicate trace/workspace write that must be blocked on cancel/timeout
        if cancel_event is None or not cancel_event.is_set():
            post_write_file.write_text("LEAKED_WRITE_AFTER_KILL")
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), ws_timeout

    env_timeout = TaskEnvelope(
        task_id="TASK-TREE-TIMEOUT",
        task_type="BUG",
        title="Timeout Tree",
        description="Tree",
        repository_path=str(tmp_path),
    )
    proc_info = supervisor.launch_async(real_tree_worker, env_timeout)

    # Wait until grandchild PID file and heartbeat file exist
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not pid_file.exists():
        time.sleep(0.02)
    assert pid_file.exists(), "Child process did not spawn grandchild in time"
    grandchild_pid = int(pid_file.read_text().strip())

    # Trigger timeout_worker
    assert supervisor.timeout_worker("TASK-TREE-TIMEOUT") is not None

    # Worker thread future MUST be done and no post-cancel write may exist
    assert proc_info.future.done() is True
    assert not post_write_file.exists(), "Worker thread wrote to workspace after timeout!"
    assert not is_pid_alive(grandchild_pid), "Grandchild process survived timeout_worker!"

    # Snapshot heartbeat file after timeout_worker returns and verify it never changes again
    snap_1 = heartbeat_file.read_text() if heartbeat_file.exists() else ""
    time.sleep(0.15)
    snap_2 = heartbeat_file.read_text() if heartbeat_file.exists() else ""
    assert snap_1 == snap_2, "Grandchild process continued writing to workspace after timeout_worker returned!"

    # Case 2: Preemption racing with Popen startup (preempt_worker called BEFORE Popen / on_process_start)
    ws_preempt = tmp_path / "ws_preempt"
    ws_preempt.mkdir(parents=True, exist_ok=True)
    race_leak_file = ws_preempt / "race_leak.txt"

    def startup_race_worker(env: TaskEnvelope, attempt_number: int = 1, on_process_start=None, cancel_event=None):
        time.sleep(0.08)  # Preemption happens during this window before Popen
        if cancel_event is not None and cancel_event.is_set():
            return WorkerResult(task_id=env.task_id, status=WorkerStatus.FAILED), ExecutionTrace(), ws_preempt
        p = subprocess.Popen(
            [sys.executable, "-c", f"import pathlib, time; pathlib.Path(r'{race_leak_file}').write_text('LEAK'); time.sleep(30)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        if on_process_start:
            on_process_start(p)
        p.communicate(timeout=5)
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.READY_FOR_REVIEW), ExecutionTrace(), ws_preempt

    env_race = TaskEnvelope(
        task_id="TASK-RACE-PREEMPT",
        task_type="BUG",
        title="Startup Race",
        description="Race",
        repository_path=str(tmp_path),
    )
    race_proc = supervisor.launch_async(startup_race_worker, env_race)
    time.sleep(0.01)  # Preempt while worker is still in pre-Popen startup sleep
    assert supervisor.preempt_worker("TASK-RACE-PREEMPT") is not None
    assert race_proc.future.done() is True
    time.sleep(0.1)
    assert not race_leak_file.exists(), "Preempted startup-race worker still spawned Popen and wrote to workspace!"
    supervisor.shutdown_all()


def test_real_codex_cli_binary_pipeline_timeout_and_preemption_quiescence(tmp_path, monkeypatch):
    """P2 End-to-End WorkerSupervisor + WorkerLauncher + CodexRuntime with a real executable `codex` CLI:
    Verifies that when CodexRuntime spawns a real `codex` process (which in turn spawns a child process
    writing to the workspace), mid-execution preemption and timeout kill the entire process tree,
    quiesce the Python worker thread, and prevent any post-kill workspace writes (including trace.jsonl).
    """
    from mtm_competition.workers.launcher import WorkerLauncher
    from mtm_core.git import git_init, run_git

    # 1. Create a real git repository for WorkspaceManager to clone
    repo_dir = tmp_path / "customer_repo"
    git_init(repo_dir)
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    run_git(repo_dir, ["add", "app.py"])
    run_git(repo_dir, ["commit", "-m", "init"])

    # 2. Create a real executable `codex` CLI binary on PATH that spawns a child process writing to cwd
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    codex_script = bin_dir / "codex_runner.py"
    codex_script.write_text(
        "import os, pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([\n"
        "    sys.executable, '-c',\n"
        "    \"import pathlib, time\\n\"\n"
        "    \"p = pathlib.Path('live_child_tick.txt')\\n\"\n"
        "    \"while True:\\n\"\n"
        "    \"    p.write_text(str(time.time_ns()), encoding='utf-8')\\n\"\n"
        "    \"    time.sleep(0.01)\\n\"\n"
        "])\n"
        "pathlib.Path('codex_child.pid').write_text(str(child.pid), encoding='utf-8')\n"
        "sys.stdout.write('{\"type\": \"turn.started\"}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )

    if sys.platform == "win32":
        codex_bin = bin_dir / "codex.cmd"
        codex_bin.write_text(f'@"{sys.executable}" "{codex_script}" %*\r\n', encoding="utf-8")
    else:
        codex_bin = bin_dir / "codex"
        codex_bin.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{codex_script}" "$@"\n', encoding="utf-8")
        codex_bin.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    scratch_dir = tmp_path / "scratch"
    launcher = WorkerLauncher(scratch_dir=scratch_dir, backend="codex")
    supervisor = WorkerSupervisor(max_workers=1)

    env = TaskEnvelope(
        task_id="CODEX-REAL-PREEMPT",
        task_type="CR",
        title="Full Codex Pipeline Preempt",
        description="Test real CodexRuntime + WorkerLauncher + WorkerSupervisor preemption",
        repository_path=str(repo_dir),
        time_budget_seconds=60,
        backend="codex",
    )

    spawned_pids = []
    try:
        proc = supervisor.launch_async(
            launcher.launch,
            env,
            attempt_number=1,
            on_pid_callback=lambda pid: spawned_pids.append(pid),
        )

        ws_dir = scratch_dir / "CODEX-REAL-PREEMPT_att1"
        child_pid_file = ws_dir / "codex_child.pid"
        tick_file = ws_dir / "live_child_tick.txt"

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and not (child_pid_file.exists() and tick_file.exists()):
            time.sleep(0.02)

        assert spawned_pids, "CodexRuntime did not register subprocess PID"
        assert child_pid_file.exists(), "codex CLI did not spawn child process in workspace"
        grandchild_pid = int(child_pid_file.read_text(encoding="utf-8").strip())

        # Preempt via WorkerSupervisor
        preempted = supervisor.preempt_worker("CODEX-REAL-PREEMPT")
        assert preempted is not None
        assert preempted.status == "PREEMPTED"
        assert preempted.quiesced is True
        assert proc.future.done() is True

        # Both codex CLI and its child process must be dead, and trace.jsonl must NOT be written after preempt
        assert not is_pid_alive(spawned_pids[0]), "codex CLI process survived preemption!"
        assert not is_pid_alive(grandchild_pid), "codex child process survived preemption!"
        assert not (ws_dir / "trace.jsonl").exists(), "CodexRuntime wrote trace.jsonl after preemption!"

        snap_before = tick_file.read_text(encoding="utf-8")
        time.sleep(0.15)
        snap_after = tick_file.read_text(encoding="utf-8")
        assert snap_before == snap_after, "Child process modified workspace after preempt_worker returned!"
    finally:
        supervisor.shutdown_all()


def test_codex_runtime_cancellation_before_and_after_popen_has_no_unbound_local(tmp_path, monkeypatch):
    """P1 CodexRuntime Fault Injection:
    1. When cancel_event is already set BEFORE subprocess.Popen, CodexRuntime.run() returns a valid
       RuntimeResult with last_message='' and raw_stdout='' (no TypeError).
    2. When cancel_event is set immediately AFTER subprocess.Popen (inside on_process_start callback)
       for both 'codex' and 'antigravity' backends, CodexRuntime.run() terminates and reaps the process,
       assigns stdout_text='', does not raise UnboundLocalError, and does not write log_file.
    """
    import threading
    from mtm_core.runtime.codex import CodexRuntime

    bin_dir = tmp_path / "bin_cancel"
    bin_dir.mkdir(parents=True, exist_ok=True)
    sleeper_script = bin_dir / "cli_sleeper.py"
    sleeper_script.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")

    for cli_name in ("codex", "agy"):
        if sys.platform == "win32":
            wrapper = bin_dir / f"{cli_name}.cmd"
            wrapper.write_text(f'@"{sys.executable}" "{sleeper_script}" %*\r\n', encoding="utf-8")
        else:
            wrapper = bin_dir / cli_name
            wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{sleeper_script}" "$@"\n', encoding="utf-8")
            wrapper.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")

    rt = CodexRuntime(default_backend="codex", allow_agy_fallback=True)
    ws = tmp_path / "ws_cancel"

    # Case 1: cancel_event set BEFORE Popen
    pre_cancel = threading.Event()
    pre_cancel.set()
    res_pre = rt.run(workspace=ws, prompt="noop", cancel_event=pre_cancel)
    assert res_pre.timed_out is True
    assert res_pre.exit_code == -1
    assert res_pre.last_message == ""
    assert res_pre.raw_stdout == ""

    # Case 2: cancel_event set immediately AFTER Popen (inside on_process_start) for both backends
    for backend_name in ("codex", "antigravity"):
        post_cancel = threading.Event()
        log_file = tmp_path / f"trace_{backend_name}.jsonl"
        spawned = []

        def _trigger_cancel_on_start(proc):
            spawned.append(proc.pid)
            post_cancel.set()

        res_post = rt.run(
            workspace=ws,
            prompt="noop",
            backend=backend_name,
            log_file=log_file,
            on_process_start=_trigger_cancel_on_start,
            cancel_event=post_cancel,
        )
        assert len(spawned) == 1
        assert res_post.timed_out is True
        assert res_post.exit_code == -1
        assert res_post.last_message == ""
        assert not is_pid_alive(spawned[0]), f"{backend_name} subprocess survived immediate cancellation!"
        assert not log_file.exists(), f"{backend_name} wrote log_file after cancellation!"

        # Case 3: cancel_event set asynchronously 80ms AFTER proc.communicate() has started blocking
        mid_cancel = threading.Event()
        mid_spawned = []

        def _delayed_cancel_after_start(proc):
            mid_spawned.append(proc.pid)
            threading.Timer(0.08, mid_cancel.set).start()

        t_mid = time.monotonic()
        res_mid = rt.run(
            workspace=ws,
            prompt="noop",
            backend=backend_name,
            timeout=30,
            log_file=log_file,
            on_process_start=_delayed_cancel_after_start,
            cancel_event=mid_cancel,
        )
        assert (time.monotonic() - t_mid) < 3.0, f"{backend_name} blocked in communicate() despite mid-flight cancel_event!"
        assert res_mid.timed_out is True
        assert res_mid.exit_code == -1
        assert not is_pid_alive(mid_spawned[0]), f"{backend_name} subprocess survived mid-communicate cancellation!"
        assert not log_file.exists()


def test_ready_to_submit_fails_closed_on_missing_or_corrupted_checkpoint_and_unverified_git(tmp_path):
    """P1 Orchestrator _resume_ready_to_submit Fault Injection:
    Must fail closed (requeue to QUEUED, clear checkpoint, never call gm.submit) when:
    - Case A: verified_result_json is missing/empty
    - Case B: verified_result_json is corrupted JSON
    - Case C: required evidence file in envelope.evidence_required is missing on disk
    - Case D: saved_rev does not match actual Git repository HEAD (GitGuard.verify_head fails)
    """
    import json
    from mtm_core.git import get_head_sha, git_init, run_git

    repo_dir = tmp_path / "repo_fail_closed"
    git_init(repo_dir)
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
    run_git(repo_dir, ["add", "."])
    run_git(repo_dir, ["commit", "-m", "initial"])
    real_sha = get_head_sha(repo_dir)

    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=tmp_path / "scratch",
        db_path=str(tmp_path / "fail_closed.db"),
        push_to_remote=False,
    )

    # Case A: Missing verified_result_json
    orch.store.add_task(
        TaskEnvelope(
            task_id="READY-MISSING-JSON",
            task_type="BUG",
            title="Missing Checkpoint JSON",
            description="No JSON",
            repository_path=str(repo_dir),
            points=400,
        )
    )
    orch.store.update_task_status("READY-MISSING-JSON", TaskState.READY_TO_SUBMIT)
    res_a = orch.step()
    assert res_a["task_id"] == "READY-MISSING-JSON"
    assert res_a["status"] == "CHECKPOINT_INVALID_REQUEUED"
    assert orch.store.get_task("READY-MISSING-JSON")["status"] == TaskState.QUEUED.value
    gm.submit.assert_not_called()

    # Case B: Corrupted verified_result_json
    orch.store.save_verified_checkpoint(
        "READY-MISSING-JSON",
        worker_result_json="{corrupted_json!!",
        workspace_path=str(repo_dir),
        revision_sha=real_sha,
    )
    orch.store.update_task_status("READY-MISSING-JSON", TaskState.READY_TO_SUBMIT)
    res_b = orch.step()
    assert res_b["task_id"] == "READY-MISSING-JSON"
    assert res_b["status"] == "CHECKPOINT_INVALID_REQUEUED"
    assert orch.store.get_task("READY-MISSING-JSON")["status"] == TaskState.QUEUED.value
    assert orch.store.get_task("READY-MISSING-JSON")["verified_result_json"] is None
    gm.submit.assert_not_called()

    # Case C: Required evidence file missing on disk
    orch.store.add_task(
        TaskEnvelope(
            task_id="READY-MISSING-EVIDENCE",
            task_type="BUG",
            title="Missing Evidence",
            description="Evidence required",
            repository_path=str(repo_dir),
            points=500,
            evidence_required=["reports/postmortem.md"],
        )
    )
    valid_res_missing_ev_disk = WorkerResult(
        task_id="READY-MISSING-EVIDENCE",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["src/app.py"],
        evidence_paths=["reports/postmortem.md"],  # Declared in JSON, but file does not exist on disk!
        test_results=[{"command": "pytest", "passed": True}],
    )
    orch.store.save_verified_checkpoint(
        "READY-MISSING-EVIDENCE",
        worker_result_json=json.dumps(valid_res_missing_ev_disk.to_dict()),
        workspace_path=str(repo_dir),
        revision_sha=real_sha,
    )
    orch.store.update_task_status("READY-MISSING-EVIDENCE", TaskState.READY_TO_SUBMIT)
    res_c = orch.step()
    assert res_c["task_id"] == "READY-MISSING-EVIDENCE"
    assert res_c["status"] == "CHECKPOINT_INVALID_REQUEUED"
    assert "missing on disk" in res_c["error"]
    assert orch.store.get_task("READY-MISSING-EVIDENCE")["status"] == TaskState.QUEUED.value
    gm.submit.assert_not_called()

    # Case D: Tampered/mismatched saved_rev rejected by GitGuard.verify_head
    valid_res = WorkerResult(
        task_id="READY-MISSING-JSON",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["src/app.py"],
        test_results=[{"command": "pytest", "passed": True}],
    )
    orch.store.save_verified_checkpoint(
        "READY-MISSING-JSON",
        worker_result_json=json.dumps(valid_res.to_dict()),
        workspace_path=str(repo_dir),
        revision_sha="deadbeef00000000000000000000000000000000",
    )
    orch.store.update_task_status("READY-MISSING-JSON", TaskState.READY_TO_SUBMIT)
    res_d = orch.step()
    assert res_d["task_id"] == "READY-MISSING-JSON"
    assert res_d["status"] == "CHECKPOINT_INVALID_REQUEUED"
    assert "GitGuard" in res_d["error"]
    assert orch.store.get_task("READY-MISSING-JSON")["status"] == TaskState.QUEUED.value
    gm.submit.assert_not_called()


def test_scheduler_strict_cooldown_returns_none_when_all_queued_tasks_on_cooldown(tmp_path):
    """P2 TournamentScheduler Strict Cooldown Enforcement:
    When all queued tasks are on a transient retry cooldown, select_next_task() MUST return None
    instead of bypassing cooldown and burning retry budgets prematurely.
    """
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [
        {"task_id": "BUG-COOL-1", "task_type": "BUG", "title": "C1", "description": "D1", "points": 300},
        {"task_id": "BUG-COOL-2", "task_type": "BUG", "title": "C2", "description": "D2", "points": 200},
    ]

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
    )
    orch.sync_backlog()

    # Put both tasks on a 60s cooldown
    orch.scheduler.set_cooldown("BUG-COOL-1", 60.0)
    orch.scheduler.set_cooldown("BUG-COOL-2", 60.0)

    # Scheduler must strictly return None while all tasks are on cooldown
    assert orch.scheduler.select_next_task() is None

    # Orchestrator step() must return None and NOT call gm.start_working
    step_res = orch.step()
    assert step_res is None
    gm.start_working.assert_not_called()

    # Once BUG-COOL-2 cooldown expires/clears, scheduler selects BUG-COOL-2
    orch.scheduler.clear_cooldown("BUG-COOL-2")
    selected = orch.scheduler.select_next_task()
    assert selected is not None
    assert selected["task_id"] == "BUG-COOL-2"


def test_crash_recovery_pid_reuse_protection_does_not_kill_unrelated_process(tmp_path):
    """P2 TaskStore Crash Recovery PID Reuse Protection:
    If the OS recycles a stale active_pid to a new unrelated process whose creation timestamp
    does not match active_pid_started_at, reconcile_orphaned_tasks() MUST NOT kill the process.
    """
    from mtm_core.runtime.process import get_process_create_time

    db_file = tmp_path / "pid_reuse.db"
    store = TaskStore(str(db_file))
    store.add_task(
        TaskEnvelope(
            task_id="BUG-REUSED-PID",
            task_type="BUG",
            title="PID Reuse Test",
            description="Do not kill innocent process",
            repository_path=str(tmp_path),
            points=200,
        )
    )
    store.update_task_status("BUG-REUSED-PID", TaskState.RUNNING)

    innocent_proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        actual_create_time = get_process_create_time(innocent_proc.pid)
        assert actual_create_time is not None

        # Simulate a stale DB record where the previous worker with this PID started 120 seconds earlier
        store.set_active_pid(
            "BUG-REUSED-PID",
            innocent_proc.pid,
            pid_started_at=actual_create_time - 120.0,
        )

        requeued = store.reconcile_orphaned_tasks()
        assert len(requeued) == 1
        assert requeued[0]["task_id"] == "BUG-REUSED-PID"
        assert requeued[0]["to_state"] == TaskState.QUEUED.value

        # Innocent process MUST still be alive!
        time.sleep(0.1)
        assert innocent_proc.poll() is None, "reconcile_orphaned_tasks killed a reused innocent PID!"
        assert is_pid_alive(innocent_proc.pid) is True

        # Event ORPHAN_PID_SKIPPED_REUSED must be recorded
        events = store.get_events("BUG-REUSED-PID")
        assert any(e["event_type"] == "ORPHAN_PID_SKIPPED_REUSED" for e in events)
        assert store.get_task("BUG-REUSED-PID")["active_pid"] is None
    finally:
        if innocent_proc.poll() is None:
            innocent_proc.kill()
            innocent_proc.wait(timeout=5)
