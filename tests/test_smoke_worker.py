"""Smoke test: Prove one autonomous Worker run end-to-end on fixture repo."""
import json
import shutil
from pathlib import Path
import pytest

from runner.codex_adapter import WorkerAdapter
from runner.qa_gate import validate_worker_result

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def test_smoke_worker_run(tmp_path):
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
        "model": "gemini-3.8-flash-high",
        "reasoning_effort": "low",
        "time_budget_seconds": 120,
        "limits": {"network": "none", "allowed_paths": [str(work_repo)]}
    }

    # 3. Execute worker autonomously via WorkerAdapter
    adapter = WorkerAdapter(use_agy_fallback=True)
    worker_result, trace = adapter.execute(envelope)

    # 4. Verify outputs
    assert worker_result["task_id"] == "BUG-SMOKE-01"
    assert worker_result["status"] in ("READY_FOR_REVIEW", "COMPLETED")
    assert "calculator.py" in worker_result["changed_files"]
    assert worker_result["elapsed_seconds"] > 0
    assert trace.turns >= 1

    # 5. Deterministic QA Gate validation
    validate_worker_result(worker_result, workspace_dir=work_repo)

    # 6. Verify the code was actually fixed
    fixed_code = (work_repo / "calculator.py").read_text(encoding="utf-8")
    assert ">=" in fixed_code
