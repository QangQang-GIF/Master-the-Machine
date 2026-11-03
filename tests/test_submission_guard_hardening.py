"""Unit tests for P2 Submission Gate Hardening."""
import hashlib
import json
import time
from pathlib import Path
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_competition.tasks.store import TaskStore
from mtm_competition.integration.git_guard import (
    GitGuard,
    PreSubmissionGitGuard,
    SubmissionGateError,
    VerifiedRevision,
)
from mtm_competition.integration.submission_guard import SubmissionGuard


def test_guard_rejects_placeholder_png(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    ans_dir = repo / "answers" / "BUG-01" / "evidence"
    ans_dir.mkdir(parents=True)
    
    # 1x1 minimal PNG (under 100 bytes)
    png_file = ans_dir / "screen.png"
    png_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15c4")

    env = TaskEnvelope(
        task_id="BUG-01",
        task_type="BUG",
        title="Fix dashboard",
        description="Fix the broken dashboard layout",
        repository_path=str(repo),
        points=100,
        evidence_required=["screen.png"],
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="BUG-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
        evidence_paths=["screen.png"],
    )
    rev = VerifiedRevision(commit_sha="a" * 40, branch="main", is_clean=True, is_pushed=True)
    
    with pytest.raises(SubmissionGateError, match="placeholder stub"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            answer_payload={"status": "DONE", "evidenceFiles": ["evidence/screen.png"]},
        )


def test_guard_rejects_synthetic_fixture(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    ans_dir = repo / "answers" / "CR-01" / "evidence"
    ans_dir.mkdir(parents=True)
    
    fixture_file = ans_dir / "cr-01-test-data.json"
    fixture_file.write_text('{"id": 1, "note": "fixture-cr-01-synthetic"}', encoding="utf-8")

    env = TaskEnvelope(
        task_id="CR-01",
        task_type="CR",
        title="Add feature",
        description="Implement CR-01",
        repository_path=str(repo),
        points=140,
        evidence_required=["cr-01-test-data.json"],
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="CR-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
        evidence_paths=["cr-01-test-data.json"],
    )
    rev = VerifiedRevision(commit_sha="b" * 40, branch="main", is_clean=True, is_pushed=True)
    
    with pytest.raises(SubmissionGateError, match="forbidden synthetic marker"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            answer_payload={"status": "DONE"},
        )


def test_guard_rejects_missing_or_mismatched_source_sidecar(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    repo.mkdir()
    ans_dir = repo / "answers" / "BUG-02" / "evidence"
    ans_dir.mkdir(parents=True)
    
    fixture_file = ans_dir / "bug-02-test-data.json"
    raw_content = b'{"customers": [{"id": 101, "balance": 500.0}]}'
    fixture_file.write_bytes(raw_content)

    env = TaskEnvelope(
        task_id="BUG-02",
        task_type="BUG",
        title="Fix balance",
        description="Fix precision balance calculation",
        repository_path=str(repo),
        points=100,
        evidence_required=["bug-02-test-data.json"],
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="BUG-02",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
        evidence_paths=["bug-02-test-data.json"],
    )
    rev = VerifiedRevision(commit_sha="c" * 40, branch="main", is_clean=True, is_pushed=True)
    
    # 1. Missing sidecar -> rejected
    with pytest.raises(SubmissionGateError, match="missing official '.source.json' sidecar"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            answer_payload={"status": "DONE"},
        )

    # 2. Sidecar with mismatched hash -> rejected
    sidecar_file = ans_dir / "bug-02-test-data.json.source.json"
    sidecar_file.write_text(json.dumps({
        "tool": "qa_data_store",
        "arguments": {"key": "BUG-02"},
        "sha256": "wrong_sha256_hash",
    }), encoding="utf-8")

    with pytest.raises(SubmissionGateError, match="sha256 mismatch"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            answer_payload={"status": "DONE"},
        )

    # 3. Correct sidecar -> passes
    real_sha = hashlib.sha256(raw_content).hexdigest()
    sidecar_file.write_text(json.dumps({
        "tool": "qa_data_store",
        "arguments": {"key": "BUG-02"},
        "sha256": real_sha,
    }), encoding="utf-8")

    # Should not raise
    guard.validate_pre_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        answer_payload={"status": "DONE"},
    )


def test_guard_blocks_failed_tests_when_not_near_deadline(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    now = 1000.0
    deadline = 2000.0  # 1000s remaining > 120s

    env = TaskEnvelope(
        task_id="TASK-01",
        task_type="TASK",
        title="Implement endpoint",
        description="Add customer endpoint",
        repository_path=str(tmp_path),
        points=100,
        deadline_epoch=deadline,
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="TASK-01",
        status="PARTIAL",
        test_results=[{"command": "pytest tests/test_api.py", "passed": False}],
    )
    rev = VerifiedRevision(commit_sha="d" * 40, branch="main", is_clean=True, is_pushed=True)

    with pytest.raises(SubmissionGateError, match="test failed"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            current_time=now,
            answer_payload={"status": "PARTIAL"},
        )


def test_guard_allows_partial_near_deadline_with_honest_fails(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    now = 1950.0
    deadline = 2000.0  # 50s remaining <= 120s (near deadline)

    env = TaskEnvelope(
        task_id="TASK-02",
        task_type="TASK",
        title="Implement endpoint",
        description="Add customer endpoint",
        repository_path=str(tmp_path),
        points=100,
        deadline_epoch=deadline,
    )
    store.add_task(env)
    rev = VerifiedRevision(commit_sha="e" * 40, branch="main", is_clean=True, is_pushed=True)

    # If status is DONE but tests failed -> rejected even near deadline
    worker_res_done = WorkerResult(
        task_id="TASK-02",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": False}],
    )
    with pytest.raises(SubmissionGateError, match="Cannot submit task TASK-02 as DONE near deadline"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res_done,
            revision=rev,
            current_time=now,
            answer_payload={"status": "DONE"},
        )

    # If status is PARTIAL with honest failure -> allowed near deadline!
    worker_res_partial = WorkerResult(
        task_id="TASK-02",
        status="PARTIAL",
        test_results=[{"command": "pytest", "passed": False}],
    )
    guard.validate_pre_submission(
        envelope=env,
        worker_result=worker_res_partial,
        revision=rev,
        current_time=now,
        answer_payload={"status": "PARTIAL"},
    )


def test_guard_enforces_submit_template(tmp_path: Path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)

    env = TaskEnvelope(
        task_id="PI-01",
        task_type="PI",
        title="Production incident analysis",
        description="Investigate memory outage",
        repository_path=str(tmp_path),
        points=150,
        metadata={
            "submitTemplate": {
                "rootCause": "",
                "mitigationPlan": "",
                "affectedServices": [],
            }
        },
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="PI-01",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "check_report", "passed": True}],
    )
    rev = VerifiedRevision(commit_sha="f" * 40, branch="main", is_clean=True, is_pushed=True)

    # Incomplete answer missing affectedServices
    bad_answer = {
        "status": "DONE",
        "rootCause": "Memory leak in redis connection pool",
        "mitigationPlan": "Restart container and add connection pooling limits",
    }
    with pytest.raises(SubmissionGateError, match="missing required key 'affectedServices' from submitTemplate"):
        guard.validate_pre_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            answer_payload=bad_answer,
        )

    # Complete answer
    good_answer = dict(bad_answer)
    good_answer["affectedServices"] = ["auth-service", "gateway"]
    guard.validate_pre_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        answer_payload=good_answer,
    )
