"""E2E-C — Real CLI Execution Suite.

Verifies real autonomous worker runs using the live CLI binaries (Codex CLI and Antigravity CLI)
against customer repository fixtures.

Matrix:
- Codex CLI: Simple BUG, Multi-file CR, Timeout termination
- Anti CLI (agy): Simple BUG, Multi-file CR, Timeout termination

Per Upgrade2.md:
"Không dùng mocked Worker cho tầng này. Nếu một CLI không sẵn có, đánh dấu BLOCKED; không chuyển thành PASS."
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerStatus
from mtm_competition.workers import WorkerLauncher

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def _check_live_cli_gate(pytestconfig, backend_name: str):
    """Enforce live_cli marker and CLI availability check."""
    mark_expr = pytestconfig.getoption("-m", default="")
    if "live_cli" not in mark_expr and os.environ.get("MTM_LIVE_CLI") != "1":
        pytest.skip(f"E2E-C Real CLI test. Skipped in default test run (run with -m live_cli).")

    binary = "codex" if backend_name == "codex" else "agy"
    if not shutil.which(binary):
        pytest.skip(f"BLOCKED: {binary} CLI binary not found on PATH. Not marked as PASS.")


@pytest.mark.live_cli
def test_e2e_c_codex_simple_bug(tmp_path, pytestconfig):
    """Codex CLI: Simple BUG execution."""
    _check_live_cli_gate(pytestconfig, "codex")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="BUG-CODEX-01",
        task_type="BUG",
        title="Fix discount threshold bug",
        description="Fix price >= threshold in calculator.py so pytest passes.",
        repository_path=str(work_repo),
        mvs=[{"id": "MVS-1", "description": "test_discount_exact_threshold"}],
        selected_skills=["mtm-bug-worker@0.1.0"],
        model="gpt-6.1-sol",
        reasoning_effort="low",
        time_budget_seconds=180,
        backend="codex",
    )

    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="codex")
    result, trace, ws = launcher.launch(envelope)
    assert result.status in (WorkerStatus.READY_FOR_REVIEW, WorkerStatus.COMPLETED)
    assert "calculator.py" in result.changed_files
    assert ">=" in (ws / "calculator.py").read_text(encoding="utf-8")


@pytest.mark.live_cli
def test_e2e_c_codex_multifile_cr(tmp_path, pytestconfig):
    """Codex CLI: Multi-file CR execution."""
    _check_live_cli_gate(pytestconfig, "codex")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="CR-CODEX-02",
        task_type="CR",
        title="Add tax calculator to checkout",
        description="Add tax calculation across cart and pricing modules.",
        repository_path=str(work_repo),
        selected_skills=["mtm-cr-worker@0.1.0"],
        model="gpt-6.1-sol",
        reasoning_effort="medium",
        time_budget_seconds=180,
        backend="codex",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="codex")
    result, trace, ws = launcher.launch(envelope)
    assert result.status in (WorkerStatus.READY_FOR_REVIEW, WorkerStatus.COMPLETED)
    assert len(result.changed_files) >= 1


@pytest.mark.live_cli
def test_e2e_c_codex_worker_timeout(tmp_path, pytestconfig):
    """Codex CLI: Process terminates cleanly on timeout."""
    _check_live_cli_gate(pytestconfig, "codex")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="TIMEOUT-CODEX-03",
        task_type="BUG",
        title="Timeout test",
        description="Do heavy work",
        repository_path=str(work_repo),
        time_budget_seconds=1,  # Short timeout
        backend="codex",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="codex")
    result, trace, ws = launcher.launch(envelope)
    assert result.status == WorkerStatus.TIMED_OUT


@pytest.mark.live_cli
def test_e2e_c_agy_simple_bug(tmp_path, pytestconfig):
    """Antigravity CLI (agy): Simple BUG functional execution."""
    _check_live_cli_gate(pytestconfig, "antigravity")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="BUG-AGY-01",
        task_type="BUG",
        title="Fix discount threshold bug",
        description="Fix price >= threshold in calculator.py so pytest passes.",
        repository_path=str(work_repo),
        mvs=[{"id": "MVS-1", "description": "test_discount_exact_threshold"}],
        selected_skills=["mtm-bug-worker@0.1.0"],
        model="gemini-3.8-flash-low",
        reasoning_effort="low",
        time_budget_seconds=180,
        backend="antigravity",
    )

    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="antigravity")
    result, trace, ws = launcher.launch(envelope)
    assert result.status in (WorkerStatus.READY_FOR_REVIEW, WorkerStatus.COMPLETED)
    assert "calculator.py" in result.changed_files
    assert ">=" in (ws / "calculator.py").read_text(encoding="utf-8")


@pytest.mark.live_cli
def test_e2e_c_agy_multifile_cr(tmp_path, pytestconfig):
    """Antigravity CLI (agy): Multi-file CR functional execution."""
    _check_live_cli_gate(pytestconfig, "antigravity")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="CR-AGY-02",
        task_type="CR",
        title="Add tax calculator to checkout",
        description="Add tax calculation across cart and pricing modules.",
        repository_path=str(work_repo),
        selected_skills=["mtm-cr-worker@0.1.0"],
        model="gemini-3.8-flash-high",
        reasoning_effort="medium",
        time_budget_seconds=180,
        backend="antigravity",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="antigravity")
    result, trace, ws = launcher.launch(envelope)
    assert result.status in (WorkerStatus.READY_FOR_REVIEW, WorkerStatus.COMPLETED)
    assert len(result.changed_files) >= 1


@pytest.mark.live_cli
def test_e2e_c_agy_worker_timeout(tmp_path, pytestconfig):
    """Antigravity CLI (agy): Process terminates cleanly on timeout."""
    _check_live_cli_gate(pytestconfig, "antigravity")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="TIMEOUT-AGY-03",
        task_type="BUG",
        title="Timeout test",
        description="Do heavy work",
        repository_path=str(work_repo),
        time_budget_seconds=1,  # Short timeout
        backend="antigravity",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="antigravity")
    result, trace, ws = launcher.launch(envelope)
    assert result.status == WorkerStatus.TIMED_OUT


@pytest.mark.live_cli
def test_e2e_c_codex_worker_preemption_quiescence(tmp_path, pytestconfig):
    """Codex CLI: Real WorkerSupervisor mid-execution preemption kills CLI process tree with zero post-preempt writes."""
    import time
    from mtm_competition.workers.supervisor import WorkerSupervisor
    from mtm_core.runtime.process import is_pid_alive

    _check_live_cli_gate(pytestconfig, "codex")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="PREEMPT-CODEX-04",
        task_type="CR",
        title="Preemption quiescence test",
        description="Analyze all files thoroughly before making changes.",
        repository_path=str(work_repo),
        time_budget_seconds=60,
        backend="codex",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="codex")
    supervisor = WorkerSupervisor(max_workers=1)

    spawned_pids = []
    proc = supervisor.launch_async(
        launcher.launch,
        envelope,
        on_pid_callback=lambda pid: spawned_pids.append(pid),
    )

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not spawned_pids:
        time.sleep(0.05)

    assert spawned_pids, "Real Codex CLI subprocess did not start in time"
    cli_pid = spawned_pids[0]

    preempted = supervisor.preempt_worker("PREEMPT-CODEX-04")
    assert preempted is not None
    assert preempted.status == "PREEMPTED"
    assert proc.future.done() is True
    assert not is_pid_alive(cli_pid), f"Codex CLI PID {cli_pid} survived preemption!"

    ws_dir = tmp_path / "scratch" / "ws_PREEMPT-CODEX-04_att1"
    assert not (ws_dir / "trace.jsonl").exists(), "Worker thread wrote trace.jsonl after preemption!"
    supervisor.shutdown_all()


@pytest.mark.live_cli
def test_e2e_c_agy_worker_preemption_quiescence(tmp_path, pytestconfig):
    """Antigravity CLI (agy): Real WorkerSupervisor mid-execution preemption kills CLI process tree with zero post-preempt writes."""
    import time
    from mtm_competition.workers.supervisor import WorkerSupervisor
    from mtm_core.runtime.process import is_pid_alive

    _check_live_cli_gate(pytestconfig, "antigravity")

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = TaskEnvelope(
        task_id="PREEMPT-AGY-04",
        task_type="CR",
        title="Preemption quiescence test",
        description="Analyze all files thoroughly before making changes.",
        repository_path=str(work_repo),
        time_budget_seconds=60,
        backend="antigravity",
    )
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch", backend="antigravity")
    supervisor = WorkerSupervisor(max_workers=1)

    spawned_pids = []
    proc = supervisor.launch_async(
        launcher.launch,
        envelope,
        on_pid_callback=lambda pid: spawned_pids.append(pid),
    )

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and not spawned_pids:
        time.sleep(0.05)

    assert spawned_pids, "Real agy CLI subprocess did not start in time"
    cli_pid = spawned_pids[0]

    preempted = supervisor.preempt_worker("PREEMPT-AGY-04")
    assert preempted is not None
    assert preempted.status == "PREEMPTED"
    assert proc.future.done() is True
    assert not is_pid_alive(cli_pid), f"agy CLI PID {cli_pid} survived preemption!"

    ws_dir = tmp_path / "scratch" / "ws_PREEMPT-AGY-04_att1"
    assert not (ws_dir / "trace.jsonl").exists(), "Worker thread wrote trace.jsonl after preemption!"
    supervisor.shutdown_all()
