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
    """P1 State Machine: If gm.start_working raises an error, scheduler capacity is freed for subsequent tasks."""
    gm = MagicMock()
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1800}
    gm.list_backlog.return_value = [
        {"task_id": "BUG-FAIL-START", "task_type": "BUG", "title": "T1", "description": "D1", "points": 300},
        {"task_id": "BUG-NEXT-OK", "task_type": "BUG", "title": "T2", "description": "D2", "points": 100},
    ]

    def start_working_side_effect(tid):
        if tid == "BUG-FAIL-START":
            raise RuntimeError("GM 503 Service Unavailable")
        return {"status": "STARTED"}

    gm.start_working.side_effect = start_working_side_effect
    gm.get_mvs.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=":memory:",
        push_to_remote=False,
    )
    orch.sync_backlog()

    # Step 1 fails during start_working
    res1 = orch.step()
    assert res1["task_id"] == "BUG-FAIL-START"
    assert res1["status"] == "FAILED"
    assert orch.store.get_task("BUG-FAIL-START")["status"] == TaskState.REJECTED.value
    # Capacity MUST be freed!
    assert orch.scheduler.has_capacity is True
    assert orch.scheduler.active_worker_count == 0

    # Step 2 is able to select BUG-NEXT-OK without deadlocking
    next_candidate = orch.scheduler.select_next_task()
    assert next_candidate is not None
    assert next_candidate["task_id"] == "BUG-NEXT-OK"


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
