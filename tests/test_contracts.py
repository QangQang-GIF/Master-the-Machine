"""Unit tests for TaskEnvelope, WorkerResult contracts and QA Gate."""
import json
from pathlib import Path
import pytest

from mtm_competition.verification import (
    validate_task_envelope,
    validate_worker_result,
    validate_qa_gate,
    QAGateError,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


def test_valid_task_envelope():
    path = FIXTURES / "sample_task_envelope.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    # Point repository_path to actual absolute path
    data["repository_path"] = str(FIXTURES / "sample_repo")
    validate_task_envelope(data)


def test_invalid_task_envelope_missing_fields():
    data = {"task_id": "BUG-01", "task_type": "BUG"}
    with pytest.raises(Exception):
        validate_task_envelope(data)


def test_invalid_task_envelope_bad_type():
    path = FIXTURES / "sample_task_envelope.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["task_type"] = "INVALID_TYPE"
    with pytest.raises(Exception):
        validate_task_envelope(data)


def test_valid_worker_result():
    path = FIXTURES / "sample_worker_result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_worker_result(data)


def test_invalid_worker_result_bad_status():
    path = FIXTURES / "sample_worker_result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "UNKNOWN_STATUS"
    with pytest.raises(Exception):
        validate_worker_result(data)


def test_qa_gate_missing_file_check():
    path = FIXTURES / "sample_worker_result.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["changed_files"] = ["non_existent_file.py"]
    with pytest.raises(QAGateError, match="missing on disk"):
        validate_worker_result(data, workspace_dir=FIXTURES / "sample_repo")


from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus


def test_qa_gate_invalid_task_id(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("print('hello')", encoding="utf-8")

    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
    }
    result = {
        "task_id": "BUG-02",  # Mismatch!
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="Task ID mismatch"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_qa_gate_path_escape(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
    }
    result = {
        "task_id": "BUG-01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["../outside.py"],
        "test_results": [{"command": "pytest", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="Path escape detected"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_qa_gate_allowed_paths_containment(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    src_dir = repo / "src"
    src_dir.mkdir()
    (src_dir / "valid.py").write_text("# ok", encoding="utf-8")
    (repo / "forbidden.py").write_text("# bad", encoding="utf-8")

    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
        "allowed_paths": [str(src_dir)],
    }
    result = {
        "task_id": "BUG-01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["forbidden.py"],
        "test_results": [{"command": "pytest", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="outside declared allowed_paths"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_qa_gate_missing_required_evidence(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("print('hello')", encoding="utf-8")

    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
        "evidence_required": ["evidence_log.txt"],
    }
    result = {
        "task_id": "BUG-01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest", "passed": True}],
        "evidence_paths": [],  # Missing evidence!
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="Required evidence missing from WorkerResult"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_qa_gate_failed_test_in_ready_for_review(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("print('hello')", encoding="utf-8")

    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
    }
    result = {
        "task_id": "BUG-01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest", "passed": False}],  # Test failed!
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="test failed"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_qa_gate_wrong_base_sha(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "calc.py").write_text("print('hello')", encoding="utf-8")

    envelope = {
        "task_id": "BUG-01",
        "task_type": "BUG",
        "title": "Fix bug",
        "description": "Fix bug in calc",
        "repository_path": str(repo),
        "base_revision": "abc1234",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
    }
    result = {
        "task_id": "BUG-01",
        "status": "READY_FOR_REVIEW",
        "base_revision": "deadbeef",  # Mismatch!
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }
    with pytest.raises(QAGateError, match="Base revision mismatch"):
        validate_qa_gate(envelope, result, workspace_dir=repo)


def test_mtm_core_contract_dataclasses(tmp_path):
    envelope = TaskEnvelope(
        task_id="BUG-42",
        task_type="BUG",
        title="Sample",
        description="Desc",
        repository_path=str(tmp_path),
    )
    assert envelope.repo_path == str(tmp_path)
    assert envelope.time_budget == 420

    res = WorkerResult(
        task_id="BUG-42",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["file.py"],
    )
    assert res.status == "READY_FOR_REVIEW"
    assert res.skills_used == ["mtm-task-worker@0.1.0"]

