"""Smoke test: Prove autonomous Worker runs end-to-end on fixture repo (auto & backend-pinned)."""
import json
import os
import shutil
from pathlib import Path
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerStatus
from mtm_competition.workers import WorkerLauncher
from mtm_competition.verification import validate_worker_result

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.mark.live_cli
def test_smoke_worker_run(tmp_path, pytestconfig):
    """Functional smoke test: Worker MUST complete autonomously and fix the code."""
    mark_expr = pytestconfig.getoption("-m", default="")
    if "live_cli" not in mark_expr and os.environ.get("MTM_LIVE_CLI") != "1":
        pytest.skip("Smoke test requires real CLI execution. Skipped in default test run (run with -m live_cli).")

    has_codex = shutil.which("codex") is not None
    if not has_codex:
        pytest.skip("BLOCKED: codex CLI not available on PATH for live smoke execution.")

    chosen_backend = "codex"

    # 1. Prepare temporary isolated fixture repo
    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    # 2. Build TaskEnvelope pointing to isolated work_repo
    envelope = {
        "task_id": "BUG-SMOKE-01",
        "task_type": "BUG",
        "title": "Fix discount threshold bug in calculator.py",
        "description": (
            "In calculator.py, `calculate_discount(price, threshold=100.0, rate=0.1)` has a bug: "
            "`if price > threshold:`. It must apply the discount when price is equal to or greater than threshold "
            "(`if price >= threshold:`). Fix this line and ensure `pytest test_calculator.py` passes."
        ),
        "repository_path": str(work_repo),
        "base_revision": "HEAD",
        "deadline_epoch": None,
        "points": 200,
        "mvs": [{"id": "MVS-1", "description": "test_discount_exact_threshold"}],
        "evidence_required": [],
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low",
        "time_budget_seconds": 180,
        "limits": {"network": "none", "allowed_paths": [str(work_repo)]},
        "backend": chosen_backend,
    }

    # 3. Execute worker autonomously via WorkerLauncher
    launcher = WorkerLauncher(
        scratch_dir=tmp_path / "scratch",
        backend=chosen_backend,
        strict_backend=True,
    )
    worker_result, trace, ws_path = launcher.launch(envelope, backend=chosen_backend)

    # 4. Strict functional assertion: TIMED_OUT is NOT considered a functional success
    res_dict = worker_result.to_dict() if hasattr(worker_result, "to_dict") else worker_result
    assert res_dict["task_id"] == "BUG-SMOKE-01"
    assert res_dict["status"] in ("READY_FOR_REVIEW", "COMPLETED")
    assert "calculator.py" in res_dict["changed_files"]
    assert res_dict["elapsed_seconds"] > 0
    assert trace.turns >= 1
    assert trace.normalized_credits >= 0.0
    assert "normalized_credits" in res_dict.get("usage", {})

    if os.environ.get("MTM_AGENT_CONTEXT_ENABLED") == "1":
        task_md = (ws_path / "TASK.md").read_text(encoding="utf-8")
        assert "Specialist Agent Profile" in task_md
        assert "Project Knowledge Index" in task_md
        assert "Selected MTM Skill Instructions" in task_md

    # 5. Deterministic QA Gate validation
    validate_worker_result(res_dict, workspace_dir=ws_path)

    # 6. Verify the code was actually fixed
    fixed_code = (ws_path / "calculator.py").read_text(encoding="utf-8")
    assert ">=" in fixed_code


@pytest.mark.live_cli
@pytest.mark.parametrize("pinned_backend", ["codex"])
def test_smoke_worker_pinned_backend(tmp_path, pytestconfig, pinned_backend):
    """Backend-pinned functional smoke test (SV-02 / SV-04 / R-03): never substitutes one CLI for the other."""
    mark_expr = pytestconfig.getoption("-m", default="")
    if "live_cli" not in mark_expr and os.environ.get("MTM_LIVE_CLI") != "1":
        pytest.skip("Smoke test requires real CLI execution. Skipped in default test run (run with -m live_cli).")

    if shutil.which("codex") is None:
        pytest.skip("BLOCKED: Pinned backend 'codex' is not installed on PATH.")

    work_repo = tmp_path / f"work_repo_{pinned_backend}"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = {
        "task_id": f"BUG-PINNED-{pinned_backend.upper()}",
        "task_type": "BUG",
        "title": "Fix discount threshold bug in calculator.py",
        "description": (
            "In calculator.py, `calculate_discount(price, threshold=100.0, rate=0.1)` has a bug: "
            "`if price > threshold:`. Change it to `if price >= threshold:` and verify `pytest test_calculator.py`."
        ),
        "repository_path": str(work_repo),
        "base_revision": "HEAD",
        "points": 200,
        "mvs": [{"id": "MVS-1", "description": "test_discount_exact_threshold"}],
        "evidence_required": [],
        "selected_skills": ["mtm-bug-worker@1.0.0"],
        "model": "gpt-6.1-sol",
        "reasoning_effort": "low",
        "time_budget_seconds": 180,
        "backend": pinned_backend,
    }

    launcher = WorkerLauncher(
        scratch_dir=tmp_path / "scratch",
        backend=pinned_backend,
        strict_backend=True,
    )
    worker_result, trace, ws_path = launcher.launch(envelope, backend=pinned_backend)
    res_dict = worker_result.to_dict() if hasattr(worker_result, "to_dict") else worker_result
    assert res_dict["status"] in ("READY_FOR_REVIEW", "COMPLETED")
    assert "calculator.py" in res_dict["changed_files"]
    assert trace.backend == pinned_backend
    assert trace.normalized_credits > 0.0
    validate_worker_result(res_dict, workspace_dir=ws_path)
    assert ">=" in (ws_path / "calculator.py").read_text(encoding="utf-8")


@pytest.mark.live_cli
def test_smoke_worker_timeout_behavior(tmp_path, pytestconfig):
    """Timeout smoke test: Exceeding time budget MUST result in TIMED_OUT, never READY_FOR_REVIEW."""
    mark_expr = pytestconfig.getoption("-m", default="")
    if "live_cli" not in mark_expr and os.environ.get("MTM_LIVE_CLI") != "1":
        pytest.skip("Timeout smoke test requires real CLI execution. Skipped in default test run (run with -m live_cli).")

    has_codex = shutil.which("codex") is not None
    if not has_codex:
        pytest.skip("BLOCKED: codex CLI not available on PATH.")

    chosen_backend = "codex"

    work_repo = tmp_path / "work_repo"
    shutil.copytree(FIXTURES / "sample_repo", work_repo)

    envelope = {
        "task_id": "TIMEOUT-SMOKE-02",
        "task_type": "BUG",
        "title": "Timeout verification task",
        "description": "Perform complex refactoring across entire codebase.",
        "repository_path": str(work_repo),
        "base_revision": "HEAD",
        "time_budget_seconds": 1,  # Strict 1-second budget forces timeout
        "backend": chosen_backend,
    }

    launcher = WorkerLauncher(
        scratch_dir=tmp_path / "scratch",
        backend=chosen_backend,
        strict_backend=True,
    )
    worker_result, trace, ws_path = launcher.launch(envelope, backend=chosen_backend)

    res_dict = worker_result.to_dict() if hasattr(worker_result, "to_dict") else worker_result
    # Must produce TIMED_OUT and NEVER READY_FOR_REVIEW
    assert res_dict["status"] == WorkerStatus.TIMED_OUT
    assert res_dict["status"] != WorkerStatus.READY_FOR_REVIEW
