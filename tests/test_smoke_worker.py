"""Smoke test: Prove one autonomous Worker run end-to-end on fixture repo."""
import json
import shutil
from pathlib import Path
import pytest

from mtm_competition.workers import WorkerLauncher
from mtm_competition.verification import validate_worker_result

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


@pytest.mark.skipif(
    shutil.which("codex") is None and shutil.which("agy") is None,
    reason="Codex CLI or Antigravity CLI required for live smoke execution",
)
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

    # 3. Execute worker autonomously via WorkerLauncher
    launcher = WorkerLauncher(scratch_dir=tmp_path / "scratch")
    worker_result, trace, ws_path = launcher.launch(envelope)

    # 4. Verify outputs
    res_dict = worker_result.to_dict() if hasattr(worker_result, "to_dict") else worker_result
    assert res_dict["task_id"] == "BUG-SMOKE-01"
    assert res_dict["status"] in ("READY_FOR_REVIEW", "COMPLETED")
    assert "calculator.py" in res_dict["changed_files"]
    assert res_dict["elapsed_seconds"] > 0
    assert trace.turns >= 1

    # 5. Deterministic QA Gate validation
    validate_worker_result(res_dict, workspace_dir=ws_path)

    # 6. Verify the code was actually fixed
    fixed_code = (ws_path / "calculator.py").read_text(encoding="utf-8")
    assert ">=" in fixed_code
