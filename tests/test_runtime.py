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


def test_agy_command_builder():
    runtime = CodexRuntime()
    cmd = runtime.build_agy_cmd(prompt="Hello AGY", effort="medium", model="gemini-3.8-flash-high")
    assert cmd[0] == "agy"
    assert "-p" in cmd and "Hello AGY" in cmd
    assert "--model" in cmd and "gemini-3.8-flash-high" in cmd
    assert "--effort" in cmd and "medium" in cmd
    assert "--dangerously-skip-permissions" in cmd


def test_agy_sandbox_and_permission_bypass_decoupling(monkeypatch):
    """R3: Verify build_agy_cmd decouples sandbox policies from permission bypass flags."""
    rt = CodexRuntime()

    # 1. Sandboxed mode defaults to NO permission bypass
    cmd_sb = rt.build_agy_cmd("Fix bug", sandbox_policy="sandbox")
    assert "--sandbox" in cmd_sb
    assert "accept-edits" in cmd_sb
    assert "--dangerously-skip-permissions" not in cmd_sb

    # 2. Restricted mode defaults to NO permission bypass
    cmd_res = rt.build_agy_cmd("Fix bug", sandbox_policy="restricted")
    assert "--sandbox" in cmd_res
    assert "accept-edits" in cmd_res
    assert "--dangerously-skip-permissions" not in cmd_res

    # 3. Workspace-sandbox mode defaults to NO permission bypass
    cmd_ws_sb = rt.build_agy_cmd("Fix bug", sandbox_policy="workspace-sandbox")
    assert "--sandbox" in cmd_ws_sb
    assert "accept-edits" in cmd_ws_sb
    assert "--dangerously-skip-permissions" not in cmd_ws_sb

    # 4. Read-only mode defaults to NO permission bypass
    cmd_ro = rt.build_agy_cmd("Audit code", sandbox_policy="read-only")
    assert "--sandbox" in cmd_ro
    assert "plan" in cmd_ro
    assert "--dangerously-skip-permissions" not in cmd_ro

    # 5. Sandboxed mode with explicit dangerously_skip_permissions=True includes bypass
    cmd_sb_bypass = rt.build_agy_cmd("Fix bug", sandbox_policy="sandbox", dangerously_skip_permissions=True)
    assert "--sandbox" in cmd_sb_bypass
    assert "accept-edits" in cmd_sb_bypass
    assert "--dangerously-skip-permissions" in cmd_sb_bypass

    # 6. Sandboxed mode with alias skip_permissions=True includes bypass
    cmd_alias = rt.build_agy_cmd("Fix bug", sandbox_policy="sandbox", skip_permissions=True)
    assert "--sandbox" in cmd_alias
    assert "--dangerously-skip-permissions" in cmd_alias

    # 7. Read-only mode with explicit bypass flag includes bypass
    cmd_ro_bypass = rt.build_agy_cmd("Audit code", sandbox_policy="read-only", dangerously_skip_permissions=True)
    assert "--sandbox" in cmd_ro_bypass
    assert "plan" in cmd_ro_bypass
    assert "--dangerously-skip-permissions" in cmd_ro_bypass

    # 8. Unrestricted mode with explicit dangerously_skip_permissions=False suppresses bypass
    cmd_unres_no_bypass = rt.build_agy_cmd("Fix bug", sandbox_policy="workspace-write", dangerously_skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd_unres_no_bypass

    cmd_none_no_bypass = rt.build_agy_cmd("Fix bug", sandbox_policy=None, dangerously_skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd_none_no_bypass

    # 9. Unrestricted default continues to include bypass (backward-compatibility)
    cmd_def = rt.build_agy_cmd("Fix bug")
    assert "--dangerously-skip-permissions" in cmd_def

    cmd_ws_write = rt.build_agy_cmd("Fix bug", sandbox_policy="workspace-write")
    assert "--dangerously-skip-permissions" in cmd_ws_write

    # 10. Case-insensitive sandbox policy matching
    cmd_case = rt.build_agy_cmd("Fix bug", sandbox_policy="SANDBOX")
    assert "--sandbox" in cmd_case
    assert "--dangerously-skip-permissions" not in cmd_case

    # 11. Environment variable overrides
    # MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS=1 enables bypass even in sandbox
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    cmd_env_bypass = rt.build_agy_cmd("Fix bug", sandbox_policy="sandbox")
    assert "--dangerously-skip-permissions" in cmd_env_bypass

    # Explicit argument overrides environment variable
    cmd_arg_wins = rt.build_agy_cmd("Fix bug", sandbox_policy="sandbox", dangerously_skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd_arg_wins

    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)

    # MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS=0 disables bypass in unrestricted mode
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "0")
    cmd_env_suppress = rt.build_agy_cmd("Fix bug")
    assert "--dangerously-skip-permissions" not in cmd_env_suppress

    # MTM_AGY_SANDBOX=1 triggers sandbox mode when no policy is set
    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)
    monkeypatch.setenv("MTM_AGY_SANDBOX", "1")
    cmd_env_sandbox = rt.build_agy_cmd("Fix bug")
    assert "--sandbox" in cmd_env_sandbox
    assert "--dangerously-skip-permissions" not in cmd_env_sandbox


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
