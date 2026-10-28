"""Unit tests for mtm_core.runtime (CodexRuntime, timeout semantics, trace parsing)."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_competition.workers import WorkerLauncher


def test_codex_command_builder(tmp_path):
    runtime = CodexRuntime()
    cmd = runtime.build_codex_cmd(
        workspace=tmp_path,
        prompt="Fix the bug",
        model="gpt-6.1-sol",
        effort="high",
        sandbox_policy="workspace-write",
        network_access=True,
        codex_config={"tool_output_token_limit": 2000, "model_verbosity": "low"},
    )
    assert cmd[0] == "codex"
    assert cmd[1] == "exec"
    assert "--json" in cmd
    assert "-m" in cmd and "gpt-6.1-sol" in cmd
    assert '-c' in cmd and 'model_reasoning_effort="high"' in cmd
    assert '-c' in cmd and 'tool_output_token_limit=2000' in cmd
    assert '-c' in cmd and 'model_verbosity="low"' in cmd
    assert cmd[-1] == "Fix the bug"


def test_strict_timeout_semantics(tmp_path):
    """Exit gate requirement: timeout must set timed_out=True and status=TIMED_OUT, NOT READY_FOR_REVIEW."""
    runtime = CodexRuntime(prefer_codex=True, allow_agy_fallback=True, codex_path="/usr/bin/codex")

    # Mock Popen to simulate TimeoutExpired
    mock_proc = MagicMock()
    mock_proc.communicate.side_effect = [
        subprocess.TimeoutExpired(cmd=["codex"], timeout=1),
        ('{"type": "item.completed", "item": {"type": "file_change"}}\n', ""),
    ]
    mock_proc.returncode = -1

    with patch("subprocess.Popen", return_value=mock_proc):
        res = runtime.run(
            workspace=tmp_path,
            prompt="Quick task",
            timeout=1,
        )
        assert res.timed_out is True
        assert res.exit_code == -1
        assert mock_proc.kill.called


def test_worker_launcher_timeout_produces_timed_out_status(tmp_path):
    """Verify that WorkerLauncher maps runtime timeout directly to TIMED_OUT status."""
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch")

    envelope = {
        "task_id": "BUG-TIMEOUT-01",
        "task_type": "BUG",
        "title": "Simulate timeout",
        "description": "This task should time out.",
        "repository_path": str(tmp_path),
        "base_revision": "HEAD",
        "deadline_epoch": None,
        "points": 100,
        "mvs": [],
        "evidence_required": [],
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low",
        "time_budget_seconds": 60,
    }

    mock_runtime_result = RuntimeResult(
        exit_code=-1,
        timed_out=True,
        trace=ExecutionTrace(),
        usage={"input_tokens": 100, "cached_input_tokens": 50, "output_tokens": 50, "billable_tokens": 100, "total_tokens": 150, "reasoning_output_tokens": 0},
        commands=0,
        file_changes=0,
        last_message="Process timed out",
        elapsed=1.05,
    )

    with patch.object(launcher.runtime, "run", return_value=mock_runtime_result):
        result, trace, ws_path = launcher.launch(envelope)
        assert result.status == "TIMED_OUT"
        assert result.status != "READY_FOR_REVIEW"
        assert "timed out" in result.unresolved_risks[0].lower()


def test_token_accounting_formula():
    """Verify billable tokens formula matches: uncached input + output."""
    trace = ExecutionTrace(
        input_tokens=10000,
        cached_input_tokens=8500,
        output_tokens=1200,
        reasoning_output_tokens=400,
    )
    # Uncached = 10000 - 8500 = 1500
    # Billable = 1500 + 1200 = 2700
    assert trace.billable_tokens == 2700
    assert trace.total_tokens == 11200


def test_token_usage_model():
    from mtm_core.runtime.usage import TokenUsage
    usage = TokenUsage.calculate(
        input_tokens=5000,
        cached_input_tokens=3000,
        output_tokens=800,
        reasoning_output_tokens=200,
    )
    assert usage.billable_tokens == 2800  # (5000 - 3000) + 800
    assert usage.total_tokens == 5800
    d = usage.to_dict()
    assert d["billable_tokens"] == 2800


def test_process_helpers(tmp_path):
    import sys
    from mtm_core.runtime.process import is_cli_available, run_command
    assert is_cli_available("python3") or is_cli_available("python")
    assert not is_cli_available("non_existent_binary_xyz_123")

    rc, stdout, stderr, timed_out = run_command([sys.executable, "-c", "print('hello')"], cwd=tmp_path, timeout=5)
    assert rc == 0
    assert "hello" in stdout
    assert not timed_out


def test_worker_launcher_production_mode_blocks_nested_codex_cli(tmp_path):
    """RUNTIME-01: Verify WorkerLauncher rejects nested codex exec subprocess in production path."""
    launcher = WorkerLauncher(
        scratch_dir=tmp_path / "scratch",
        backend="codex",
        production_mode=True,
    )

    envelope = {
        "task_id": "PROD-TASK-01",
        "task_type": "TASK",
        "title": "Production task",
        "description": "Must not spawn nested codex exec",
        "repository_path": str(tmp_path),
        "base_revision": "HEAD",
        "deadline_epoch": None,
        "points": 100,
        "mvs": [],
        "evidence_required": [],
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low",
        "time_budget_seconds": 60,
    }

    with pytest.raises(RuntimeError, match="RUNTIME-01 Invariant"):
        launcher.launch(envelope)

