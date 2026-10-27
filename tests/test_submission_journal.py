"""Tests for SubmissionJournal, TaskStore decoupling, and idempotent SubmissionGuard replay (Requirement R2)."""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mtm_competition.integration.git_guard import SubmissionGateError, VerifiedRevision
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.tasks.store import TaskStore
from mtm_core.contracts import TaskEnvelope, TaskState, WorkerResult, WorkerStatus


def test_submission_journal_in_flight_persistence_and_replay():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-JRN-01"
    sub_id = str(uuid.uuid4())
    idemp_key = str(uuid.uuid4())
    payload = {"clientSubmissionId": sub_id, "backlogItemKey": item_key, "answer": {"code": 42}}
    body_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    expected_sha = hashlib.sha256(body_bytes).hexdigest()

    sha = journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=body_bytes,
        git_revision="a" * 40,
        remote_ref="refs/heads/main",
    )
    assert sha == expected_sha

    # Retrieve in-flight attempt
    in_flight = journal.get_in_flight(match_uuid, item_key)
    assert in_flight is not None
    assert in_flight["state"] == "IN_FLIGHT"
    assert in_flight["client_submission_id"] == sub_id
    assert in_flight["idempotency_key"] == idemp_key
    assert in_flight["request_body_sha256"] == expected_sha
    assert in_flight["request_body_bytes"] == body_bytes

    # verify_or_abort with identical bytes succeeds
    assert journal.verify_or_abort(match_uuid, item_key, body_bytes) is True

    # verify_or_abort with altered bytes fails closed
    altered_bytes = json.dumps({"altered": True}, sort_keys=True).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.verify_or_abort(match_uuid, item_key, altered_bytes)


def test_submission_journal_rejects_new_uuid_or_key_for_unresolved_attempt():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-JRN-02"
    sub_id_1 = str(uuid.uuid4())
    sub_id_2 = str(uuid.uuid4())
    idemp_key_1 = str(uuid.uuid4())
    body = b'{"test": 1}'

    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id_1,
        idempotency_key=idemp_key_1,
        request_body_bytes=body,
        git_revision="b" * 40,
    )

    # Calling record_in_flight with a different clientSubmissionId must fail closed (API-06)
    with pytest.raises(ValueError, match="Rejecting generation of new UUID"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=sub_id_2,
            idempotency_key=idemp_key_1,
            request_body_bytes=body,
            git_revision="b" * 40,
        )

    # Calling record_in_flight with a different idempotencyKey must fail closed
    with pytest.raises(ValueError, match="Rejecting generation of new key"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=sub_id_1,
            idempotency_key=str(uuid.uuid4()),
            request_body_bytes=body,
            git_revision="b" * 40,
        )


def test_submission_journal_record_receipt_decouples_completion():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-JRN-03"
    sub_id = str(uuid.uuid4())
    idemp_key = str(uuid.uuid4())
    body = b'{"receipt_test": true}'

    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=body,
        git_revision="c" * 40,
    )

    # Record receipt
    receipt_id = "gm-receipt-12345"
    journal.record_receipt(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        receipt_id=receipt_id,
        http_code=201,
    )

    # No longer IN_FLIGHT
    assert journal.get_in_flight(match_uuid, item_key) is None

    entry = journal.get_entry(match_uuid, item_key)
    assert entry is not None
    assert entry["state"] == "ACCEPTED"
    assert entry["receipt_id"] == receipt_id
    assert entry["last_http_code"] == 201

    # Attempting to start in-flight for an already ACCEPTED item must fail
    with pytest.raises(ValueError, match="already ACCEPTED"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=sub_id,
            idempotency_key=idemp_key,
            request_body_bytes=body,
            git_revision="c" * 40,
        )


def test_task_store_decouples_accepted_receipt_from_points():
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="TASK-STORE-01",
        task_type="BUG",
        title="Decoupling Test",
        description="",
        repository_path=".",
        points=250,
    )
    store.add_task(env)

    # HTTP 201 receipt with status ACCEPTED and score 0 (or provisional)
    store.record_submission(
        task_id="TASK-STORE-01",
        status="ACCEPTED",
        score=0,
        submission_id="sub-accepted-1",
    )

    task = store.get_task("TASK-STORE-01")
    assert task["status"] in ("SUBMITTED", TaskState.SUBMITTED.value)
    # Confirmed points must remain 0
    assert store.get_total_confirmed_points() == 0

    sub = store.get_submission("TASK-STORE-01")
    assert sub["status"] == "ACCEPTED"
    assert sub["scoreStatus"] == "PENDING"
    assert sub["confirmed_points"] == 0

    # Points are awarded only when subsequent GM projection confirms PASSED (LIFE-03)
    store.update_submission_status("TASK-STORE-01", status="PASSED", score=250)
    assert store.get_total_confirmed_points() == 250
    task_after = store.get_task("TASK-STORE-01")
    assert task_after["status"] in ("PASSED", TaskState.PASSED.value)


def test_submission_guard_replays_exact_bytes_on_timeout_and_rejects_new_uuid(tmp_path):
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="TASK-GUARD-REPLAY",
        task_type="TASK",
        title="Replay Guard Test",
        description="",
        repository_path=str(tmp_path),
        points=100,
        evidence_required=[],
    )
    store.add_task(env)
    store.update_task_status("TASK-GUARD-REPLAY", TaskState.READY_TO_SUBMIT)

    worker_res = WorkerResult(
        task_id="TASK-GUARD-REPLAY",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=[],
    )
    rev = VerifiedRevision(commit_sha="e" * 40, branch="main", is_clean=True)
    guard = SubmissionGuard(store)

    match_uuid = str(uuid.uuid4())
    sub_id = str(uuid.uuid4())
    idemp_key = str(uuid.uuid4())
    answer_payload = {"codeChanges": True, "notes": "initial fix"}

    # Simulate timeout on outbound call
    gm_mock_fail = MagicMock(side_effect=TimeoutError("Network dropped"))
    res_fail = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=gm_mock_fail,
        match_uuid=match_uuid,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        answer_payload=answer_payload,
    )

    assert res_fail.success is False
    assert res_fail.status == "SUBMIT_IN_FLIGHT"
    assert store.is_in_flight("TASK-GUARD-REPLAY") is True

    # Verify attempt saved in journal
    in_flight = guard.journal.get_in_flight(match_uuid, "TASK-GUARD-REPLAY")
    assert in_flight is not None
    assert in_flight["client_submission_id"] == sub_id
    saved_bytes = in_flight["request_body_bytes"]

    # Trying to retry with a NEW client_submission_id must be rejected (API-06)
    with pytest.raises(SubmissionGateError, match="Rejecting generation of new UUID"):
        guard.execute_guarded_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            game_master_submit_fn=gm_mock_fail,
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),  # New UUID!
            idempotency_key=idemp_key,
            answer_payload=answer_payload,
        )

    # Retry replay with identical UUID and body succeeds
    gm_mock_success = MagicMock(return_value={"submission": {"id": "sub-rcpt-99", "status": "ACCEPTED", "scoreStatus": "PENDING"}})
    res_success = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=gm_mock_success,
        match_uuid=match_uuid,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        answer_payload=answer_payload,
    )

    assert res_success.success is True
    assert res_success.status == "ACCEPTED"
    assert res_success.points_awarded == 0
    # In TaskStore, status is SUBMITTED and confirmed points is 0
    assert store.get_total_confirmed_points() == 0
    task_final = store.get_task("TASK-GUARD-REPLAY")
    assert task_final["status"] == TaskState.SUBMITTED.value

    # Check journal has recorded receipt
    assert guard.journal.get_in_flight(match_uuid, "TASK-GUARD-REPLAY") is None
    entry = guard.journal.get_entry(match_uuid, "TASK-GUARD-REPLAY")
    assert entry["state"] == "ACCEPTED"
    assert entry["receipt_id"] == "sub-rcpt-99"
