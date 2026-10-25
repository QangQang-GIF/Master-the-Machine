"""Portable profile/context and secure verification regression coverage."""
import sys
from unittest.mock import MagicMock
import pytest
from mtm_core.runtime import RuntimeResult, ExecutionTrace
from mtm_competition.workers.launcher import WorkerLauncher
from mtm_competition.verification.qa_gate import QAGateError, validate_schema
from mtm_competition.verification.command_safety import parse_safe_command, run_verification_command

def test_worker_opt_in_context(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AGENT_CONTEXT_ENABLED", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def faulty(): pass\n")
    runtime = MagicMock()
    runtime.run.return_value = RuntimeResult(exit_code=1, timed_out=False, trace=ExecutionTrace(), usage={}, commands=0, file_changes=0, last_message="", elapsed=0.1)
    launcher = WorkerLauncher(tmp_path / "scratch", runtime=runtime, backend="codex")
    launcher.launch({"task_id": "BUG-CONTEXT", "task_type": "BUG", "title": "Repair faulty service", "description": "Fix service.py", "repository_path": str(repo)})
    ws = runtime.run.call_args.kwargs["workspace"]
    task_text = (ws / "TASK.md").read_text(encoding="utf-8")
    assert "Specialist Agent Profile" in task_text
    assert "Selected MTM Skill Instructions" in task_text
    assert "Project Knowledge Index" in task_text

def test_invalid_schema_becomes_qa_error():
    with pytest.raises(QAGateError, match="Schema validation failed"):
        validate_schema({"status": "NOPE"}, "worker_result")

def test_prod_commands_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_ENV", "production")
    assert parse_safe_command("python -m pytest -q") == ["python", "-m", "pytest", "-q"]
    for attack in ["python -c 'import os'", "sh -c 'echo danger'", "pytest ; echo hacked", "pytest && echo bad"]:
        with pytest.raises(ValueError):
            parse_safe_command(attack)
    with pytest.raises(ValueError):
        run_verification_command("echo test", tmp_path)

def test_dev_legacy_runner_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_ENV", "development")
    monkeypatch.delenv("MTM_SECURE_TEST_COMMANDS", raising=False)
    assert run_verification_command(f'{sys.executable} -c "print(123)"', tmp_path).returncode == 0
