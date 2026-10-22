"""Unit tests for TaskEnvelope, WorkerResult contracts and QA Gate."""
import json
from pathlib import Path
import pytest

from runner.qa_gate import validate_task_envelope, validate_worker_result, QAGateError

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
