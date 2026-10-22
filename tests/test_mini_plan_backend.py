"""Tests verifying Mini Plan: Orchestrator <-> Worker CLI Synchronization."""
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_core.config.loader import load_environment_config
from mtm_core.git import git_init, get_head_sha
from mtm_competition.routing.model_router import ModelRouter
from mtm_competition.workers.launcher import WorkerLauncher
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator


def test_config_file_backend_defaults():
    """Verify production.yaml and development.yaml specify orchestrator and worker backend policies."""
    prod = load_environment_config("production")
    assert prod["orchestrator"]["worker_backend"] == "codex"
    assert prod["worker"]["strict_backend"] is True
    assert prod["worker"]["allow_fallback"] is False

    dev = load_environment_config("development")
    assert dev["orchestrator"]["worker_backend"] == "antigravity"
    assert dev["worker"]["strict_backend"] is True
    assert dev["worker"]["allow_fallback"] is False


def test_no_silent_fallback_when_codex_missing_in_strict_mode(tmp_path):
    """In strict mode, if codex is requested but missing, raise RuntimeError (no silent fallback to agy)."""
    runtime = CodexRuntime(
        codex_path=None,
        agy_path="/bin/agy",
        default_backend="codex",
        strict_backend=True,
    )
    with pytest.raises(RuntimeError, match="Backend 'codex' explicitly selected but `codex` CLI is not available"):
        runtime.run(workspace=tmp_path, prompt="task", backend="codex")


def test_no_silent_fallback_when_agy_missing_in_strict_mode(tmp_path):
    """In strict mode, if antigravity is requested but missing, raise RuntimeError (no silent fallback to codex)."""
    runtime = CodexRuntime(
        codex_path="/bin/codex",
        agy_path=None,
        default_backend="antigravity",
        strict_backend=True,
    )
    with pytest.raises(RuntimeError, match="Backend 'antigravity' explicitly selected but `agy` CLI is not available"):
        runtime.run(workspace=tmp_path, prompt="task", backend="antigravity")


def test_model_router_selects_backend_specific_models_and_efforts():
    """ModelRouter chooses codex vs agy profiles based on backend, and respects explicit overrides."""
    router = ModelRouter()

    # 1. PI task routed on codex
    d_codex_pi = router.route({"task_type": "PI", "points": 300}, backend="codex")
    assert d_codex_pi.backend == "codex"
    assert d_codex_pi.model == "gpt-6.1-sol"
    assert d_codex_pi.reasoning_effort == "high"

    # 2. PI task routed on antigravity
    d_agy_pi = router.route({"task_type": "PI", "points": 300}, backend="antigravity")
    assert d_agy_pi.backend == "antigravity"
    assert d_agy_pi.model == "claude-sonnet-4-6"
    assert d_agy_pi.reasoning_effort == "high"

    # 3. Standard CR task routed on antigravity
    d_agy_cr = router.route({"task_type": "CR", "points": 250}, backend="antigravity")
    assert d_agy_cr.backend == "antigravity"
    assert d_agy_cr.model == "gemini-3.8-flash-high"
    assert d_agy_cr.reasoning_effort == "medium"

    # 4. Explicit user override takes precedence over routed defaults
    d_override = router.route(
        {"task_type": "CR", "points": 250, "model": "gpt-6.1-luna", "reasoning_effort": "low"},
        backend="codex",
    )
    assert d_override.model == "gpt-6.1-luna"
    assert d_override.reasoning_effort == "low"


def test_orchestrator_codex_backend_launches_codex_worker(tmp_path):
    """Orchestrator configured with worker_backend='codex' dispatches Codex CLI configuration."""
    repo = tmp_path / "repo"
    git_init(repo)
    scratch = tmp_path / "scratch"

    gm = MockGameMasterAdapter(tasks=[{
        "task_id": "CR-CODEX-1",
        "task_type": "CR",
        "title": "Codex Task",
        "description": "Desc",
        "points": 200,
        "evidence_required": [],
        "mvs": [{"id": "MVS-1"}],
    }])

    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo,
        scratch_dir=scratch,
        worker_backend="codex",
    )

    # Mock runtime.run to capture parameters passed down
    with patch.object(orchestrator.launcher.runtime, "run") as mock_run:
        mock_run.return_value = RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(input_tokens=100, output_tokens=50),
            usage={},
            commands=1,
            file_changes=1,
            last_message="done",
            elapsed=2.0,
        )

        orchestrator.sync_backlog()

        # Simulate worker creating fix and worker_result.json in prepared workspace
        orig_prepare = orchestrator.launcher.workspace_mgr.prepare_workspace

        def mock_prepare_workspace(*args, **kwargs):
            ws = orig_prepare(*args, **kwargs)
            (ws / "calc.py").write_text("def run(): pass\n")
            import json
            (ws / "worker_result.json").write_text(json.dumps({
                "task_id": "CR-CODEX-1",
                "status": "READY_FOR_REVIEW",
                "skill_ids": ["mtm-cr-worker@1.0.0"],
                "changed_files": ["calc.py"],
                "test_results": [{"command": "pytest", "passed": True}],
                "evidence_paths": [],
                "elapsed_seconds": 2.0,
            }))
            return ws

        with patch.object(orchestrator.launcher.workspace_mgr, "prepare_workspace", side_effect=mock_prepare_workspace):
            res = orchestrator.step()
            assert res is not None
            assert res["status"] in ("ACCEPTED", "PASSED")

        # Verify that runtime.run was called strictly with codex backend and codex model
        assert mock_run.call_count == 1
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["backend"] == "codex"
        assert call_kwargs["model"] == "gpt-6.1-sol"
        assert call_kwargs["effort"] == "medium"


def test_orchestrator_antigravity_backend_launches_agy_worker(tmp_path):
    """Orchestrator configured with worker_backend='antigravity' dispatches Anti CLI (agy) configuration."""
    repo = tmp_path / "repo"
    git_init(repo)
    scratch = tmp_path / "scratch"

    gm = MockGameMasterAdapter(tasks=[{
        "task_id": "CR-AGY-1",
        "task_type": "CR",
        "title": "Antigravity Task",
        "description": "Desc",
        "points": 200,
        "evidence_required": [],
        "mvs": [{"id": "MVS-1"}],
    }])

    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo,
        scratch_dir=scratch,
        worker_backend="antigravity",
    )

    with patch.object(orchestrator.launcher.runtime, "run") as mock_run:
        mock_run.return_value = RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(input_tokens=100, output_tokens=50),
            usage={},
            commands=1,
            file_changes=1,
            last_message="done",
            elapsed=2.0,
        )

        orchestrator.sync_backlog()

        orig_prepare = orchestrator.launcher.workspace_mgr.prepare_workspace

        def mock_prepare_workspace(*args, **kwargs):
            ws = orig_prepare(*args, **kwargs)
            (ws / "calc.py").write_text("def run(): pass\n")
            import json
            (ws / "worker_result.json").write_text(json.dumps({
                "task_id": "CR-AGY-1",
                "status": "READY_FOR_REVIEW",
                "skill_ids": ["mtm-cr-worker@1.0.0"],
                "changed_files": ["calc.py"],
                "test_results": [{"command": "pytest", "passed": True}],
                "evidence_paths": [],
                "elapsed_seconds": 2.0,
            }))
            return ws

        with patch.object(orchestrator.launcher.workspace_mgr, "prepare_workspace", side_effect=mock_prepare_workspace):
            res = orchestrator.step()
            assert res is not None
            assert res["status"] in ("ACCEPTED", "PASSED")

        # Verify that runtime.run was called strictly with antigravity backend and agy model
        assert mock_run.call_count == 1
        call_kwargs = mock_run.call_args.kwargs
        assert call_kwargs["backend"] == "antigravity"
        assert call_kwargs["model"] == "gemini-3.8-flash-high"
        assert call_kwargs["effort"] == "medium"
