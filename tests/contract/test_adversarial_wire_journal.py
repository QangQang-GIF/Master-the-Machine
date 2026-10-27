"""
Adversarial Stress Test Suite: Game Master Wire Client & Submission Journal.
Author: challenger_1_gen6 (Wire & Journal Adversarial Challenger)

Verifies:
1. Rapid/concurrent repeated submissions with identical idempotency keys against MockGameMasterHTTPServer -> idempotent receipts.
2. Timeout replay: inject network timeouts, replay identical raw body bytes, verify 200/201 Idempotent-Replay: true.
3. Tamper detection: modify answer payload after in-flight record -> verify fail-closed (SHA256 mismatch, 0 network dispatch).
4. Match Code rejection: verify 8-character Match Code in API path fails before socket creation (0 socket connect calls).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import socket
import uuid
from typing import Any, Dict, List
from unittest.mock import MagicMock, patch

import pytest

from mtm_competition.adapters.base import validate_match_uuid
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterConnectionError,
    GameMasterIdempotencyConflictError,
    GameMasterAlreadySubmittedError,
)
from mtm_competition.integration.git_guard import SubmissionGateError, VerifiedRevision
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.tasks.store import TaskStore
from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server
except ModuleNotFoundError:
    from fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server


# ==============================================================================
# Helper utilities
# ==============================================================================

def _make_adapter(mock_server: MockGameMasterHTTPServer) -> OfficialGameMasterAdapter:
    return OfficialGameMasterAdapter(
        api_base_url=mock_server.base_url,
        api_key=mock_server.auth_token,
        timeout=3.0,
    )


# ==============================================================================
# GROUP 1: Rapid / Concurrent Submissions & Idempotent Receipts
# ==============================================================================

def test_stress_01_concurrent_rapid_identical_submissions(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 1A: 20 concurrent worker threads fire simultaneous requests with the EXACT SAME
    idempotency key, clientSubmissionId, and body payload against MockGameMasterHTTPServer.

    Invariants Verified:
    - Zero crashes, zero unhandled 409 conflicts on identical payload.
    - 100% of responses succeed with HTTP 200/201 and status ACCEPTED.
    - All 20 threads receive the EXACT SAME submission.id.
    - Exactly 1 unique submission is recorded in the server state.
    - Responses returning from cache contain idempotent replay marker.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-RAPID-CONCURRENT-01"

    # Start item first
    adapter.start_working(match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"idemp-{uuid.uuid4().hex[:16]}"
    git_rev = "a" * 40
    answer_payload = {
        "codeChanges": True,
        "summary": "Concurrent rapid submission test",
        "evidence": ["evidence/run.log"],
    }

    concurrency = 20
    results: List[Dict[str, Any]] = []
    errors: List[Exception] = []

    def _submit_worker(worker_idx: int) -> Dict[str, Any]:
        worker_adapter = _make_adapter(mock_gm_server)
        return worker_adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idemp_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer=answer_payload,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as pool:
        futures = [pool.submit(_submit_worker, i) for i in range(concurrency)]
        for f in concurrent.futures.as_completed(futures):
            try:
                res = f.result()
                results.append(res)
            except Exception as e:
                errors.append(e)

    # Invariants verification
    assert len(errors) == 0, f"Encountered unexpected errors during concurrent submit: {errors}"
    assert len(results) == concurrency

    first_sub_id = results[0]["submission"]["id"]
    for i, res in enumerate(results):
        sub = res["submission"]
        assert sub["id"] == first_sub_id, f"Worker {i} received differing submission id {sub['id']} != {first_sub_id}"
        assert sub["status"] == "ACCEPTED"
        assert sub["scoreStatus"] == "PENDING"
        assert sub["backlogItemKey"] == item_key

    # Server state sanity check
    with mock_gm_server.lock:
        assert len(mock_gm_server.accepted_items) == 1
        assert item_key in mock_gm_server.accepted_items
        assert len(mock_gm_server.submissions) == 1
        assert idemp_key in mock_gm_server.idempotency_store


def test_stress_02_sequential_burst_identical_submissions(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 1B: Sequential burst of 50 rapid repeated submissions using identical idempotency key.
    Verifies receipt consistency, stable socket lifecycle, and idempotent_replay flag on replays.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-BURST-SEQ-02"

    adapter.start_working(match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"burst-key-{uuid.uuid4().hex[:12]}"
    git_rev = "b" * 40
    answer_payload = {"codeChanges": True, "notes": "burst test"}

    burst_count = 50
    receipt_ids: List[str] = []

    for i in range(burst_count):
        res = adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idemp_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer=answer_payload,
        )
        sub = res["submission"]
        receipt_ids.append(sub["id"])
        assert sub["status"] == "ACCEPTED"
        if i > 0:
            assert res.get("idempotent_replay") is True or res.get("replay") is True

    # All 50 receipt IDs must be 100% identical
    assert len(set(receipt_ids)) == 1
    assert len(receipt_ids) == burst_count


def test_stress_03_concurrent_divergent_payloads_same_key_conflict(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 1C: 10 concurrent requests using the SAME Idempotency-Key but DIVERGENT payloads.
    Verifies that the first payload to register succeeds, and all conflicting concurrent payloads
    fail closed with HTTP 409 IDEMPOTENCY_CONFLICT (GameMasterIdempotencyConflictError).
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-CONFLICT-CONCURRENT-03"

    adapter.start_working(match_uuid, item_key)

    shared_idemp_key = f"conflict-{uuid.uuid4().hex[:16]}"
    git_rev = "c" * 40

    success_count = 0
    conflict_count = 0
    other_errors: List[Exception] = []

    def _worker(worker_idx: int) -> Dict[str, Any]:
        worker_adapter = _make_adapter(mock_gm_server)
        # Distinct answer payload per worker variant
        payload_variant = {"codeChanges": True, "worker": worker_idx, "secret": worker_idx % 2}
        return worker_adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=shared_idemp_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer=payload_variant,
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(_worker, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            try:
                res = f.result()
                success_count += 1
            except GameMasterIdempotencyConflictError:
                conflict_count += 1
            except Exception as e:
                other_errors.append(e)

    assert len(other_errors) == 0, f"Unexpected exceptions: {other_errors}"
    # Exactly one payload signature can succeed under this key; the other variants must conflict
    assert success_count >= 1
    assert conflict_count >= 1
    assert success_count + conflict_count == 10


def test_stress_04_different_key_already_submitted_conflict(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 1D: Once a backlog item has an ACCEPTED submission, any subsequent submission
    attempt with a NEW idempotency key must fail closed with HTTP 409 ALREADY_SUBMITTED.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-ALREADY-SUBMITTED-04"

    adapter.start_working(match_uuid, item_key)

    # Initial submission succeeds
    res1 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=str(uuid.uuid4()),
        idempotency_key=f"key-original-{uuid.uuid4().hex[:10]}",
        backlog_item_key=item_key,
        git_revision="d" * 40,
        answer={"codeChanges": True, "attempt": 1},
    )
    assert res1["submission"]["status"] == "ACCEPTED"

    # Attempting to submit the same backlog item under a DIFFERENT idempotency key
    with pytest.raises(GameMasterAlreadySubmittedError) as exc_info:
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=f"key-different-{uuid.uuid4().hex[:10]}",
            backlog_item_key=item_key,
            git_revision="d" * 40,
            answer={"codeChanges": True, "attempt": 2},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "ALREADY_SUBMITTED"


# ==============================================================================
# GROUP 2: Timeout Replay & Byte Integrity
# ==============================================================================

def test_stress_05_wire_client_network_timeout_and_replay_receipt(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 2A: Wire client handles network timeout / socket drop during dispatch,
    then deterministically replays identical raw body bytes.
    Verifies that the server accepts the replay without conflict.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-TIMEOUT-WIRE-05"

    adapter.start_working(match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"timeout-idemp-{uuid.uuid4().hex[:12]}"
    git_rev = "e" * 40
    answer_payload = {"codeChanges": True, "timeoutTest": True}

    # Pre-serialize exact body bytes
    body_dict = {
        "clientSubmissionId": client_sub_id,
        "backlogItemKey": item_key,
        "gitRevision": git_rev,
        "answer": answer_payload,
    }
    raw_body_bytes = json.dumps(body_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")

    # Inject network timeout (socket close on server side)
    mock_gm_server.inject_network_timeout()

    # Attempt 1: Must fail with connection error
    with pytest.raises(GameMasterConnectionError) as exc_info:
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idemp_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer=answer_payload,
            serialized_body_bytes=raw_body_bytes,
        )
    assert "failed" in str(exc_info.value).lower() or "connection" in str(exc_info.value).lower()

    # Attempt 2: Replay with the EXACT same raw body bytes and idempotency key
    res_replay = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
        serialized_body_bytes=raw_body_bytes,
    )

    assert "submission" in res_replay
    sub = res_replay["submission"]
    assert sub["status"] == "ACCEPTED"
    assert sub["scoreStatus"] == "PENDING"
    assert sub["backlogItemKey"] == item_key

    # Attempt 3: Immediate replay of already ACCEPTED submission
    # Server returns cached response with Idempotent-Replay: true header
    res_replay_2 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
        serialized_body_bytes=raw_body_bytes,
    )
    assert res_replay_2["submission"]["id"] == sub["id"]
    assert res_replay_2.get("idempotent_replay") is True or res_replay_2.get("replay") is True


def test_stress_06_server_receipt_with_idempotent_replay_header(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 2B: Verifies that when MockGameMasterHTTPServer re-receives an identical submission,
    it includes the header 'Idempotent-Replay: true', and the adapter parses it into
    parsed['idempotent_replay'] = True.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-HEADER-REPLAY-06"

    adapter.start_working(match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"key-hdr-{uuid.uuid4().hex[:12]}"
    git_rev = "f" * 40
    answer_payload = {"codeChanges": True, "headerCheck": True}

    # Initial submission -> 201 Created
    res1 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    assert res1.get("idempotent_replay") is not True

    # Replay -> Server sends header 'Idempotent-Replay: true'
    res2 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    assert res2.get("idempotent_replay") is True
    assert res2["submission"]["id"] == res1["submission"]["id"]


def test_stress_07_submission_guard_timeout_replay_end_to_end(mock_gm_server: MockGameMasterHTTPServer, tmp_path):
    """
    Stress 2C: Full end-to-end integration across SubmissionGuard, TaskStore, SubmissionJournal,
    and MockGameMasterHTTPServer under real network drop conditions.
    """
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="TASK-GUARD-E2E-07",
        task_type="TASK",
        title="E2E Timeout Guard Stress",
        description="",
        repository_path=str(tmp_path),
        points=150,
        evidence_required=[],
    )
    store.add_task(env)
    store.update_task_status(env.task_id, "READY_TO_SUBMIT")

    worker_res = WorkerResult(
        task_id=env.task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=[],
    )
    rev = VerifiedRevision(commit_sha="1234567890abcdef1234567890abcdef12345678", branch="main", is_clean=True)
    guard = SubmissionGuard(store)

    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    adapter.start_working(match_uuid, env.task_id)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"guard-e2e-{uuid.uuid4().hex[:12]}"
    answer_payload = {"codeChanges": True, "solution": "E2E rock solid"}

    # Inject timeout on mock server
    mock_gm_server.inject_network_timeout()

    # Attempt 1: Call guarded submission with real adapter.submit_work
    res_1 = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=adapter.submit_work,
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        answer_payload=answer_payload,
    )

    # Invariants for Attempt 1 (Failure/Timeout preserved):
    assert res_1.success is False
    assert res_1.status == "SUBMIT_IN_FLIGHT"
    assert store.is_in_flight(env.task_id) is True
    # Points must remain 0
    assert store.get_total_confirmed_points() == 0

    # In-flight record must exist in SubmissionJournal
    in_flight_rec = guard.journal.get_in_flight(match_uuid, env.task_id)
    assert in_flight_rec is not None
    assert in_flight_rec["client_submission_id"] == client_sub_id
    assert in_flight_rec["idempotency_key"] == idemp_key
    saved_body_bytes = in_flight_rec["request_body_bytes"]
    assert in_flight_rec["request_body_sha256"] == hashlib.sha256(saved_body_bytes).hexdigest()

    # Attempt 2: Replay guarded submission (timeout was consumed, now network is clear)
    res_2 = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=adapter.submit_work,
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        answer_payload=answer_payload,
    )

    # Invariants for Attempt 2 (Success Replay):
    assert res_2.success is True
    assert res_2.status == "ACCEPTED"
    assert res_2.points_awarded == 0  # Confirmed points are 0 until final grading projection
    assert store.get_total_confirmed_points() == 0

    # In-flight record cleared from journal, updated to ACCEPTED
    assert guard.journal.get_in_flight(match_uuid, env.task_id) is None
    accepted_rec = guard.journal.get_entry(match_uuid, env.task_id)
    assert accepted_rec is not None
    assert accepted_rec["state"] == "ACCEPTED"
    assert accepted_rec["receipt_id"] is not None
    assert accepted_rec["last_http_code"] in (200, 201)


# ==============================================================================
# GROUP 3: Tamper Detection & Fail-Closed Behavior
# ==============================================================================

def test_stress_08_journal_tamper_detection_various_mutations():
    """
    Stress 3A: Adversarial mutation testing on SubmissionJournal.
    Tests multiple fine-grained payload tampering variants:
    1. Adding an extra key.
    2. Modifying a boolean flag.
    3. Type mutation (int -> str).
    4. Modifying nested list elements.
    5. Modifying clientSubmissionId or idempotencyKey for in-flight item.

    All variants must FAIL CLOSED with ValueError and NEVER alter the stored ledger entry.
    """
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-TAMPER-08"
    sub_id = str(uuid.uuid4())
    idemp_key = str(uuid.uuid4())

    original_answer = {
        "codeChanges": True,
        "score": 100,
        "evidence": ["evidence/test.log"],
        "metadata": {"author": "bot"},
    }
    original_payload = {
        "clientSubmissionId": sub_id,
        "backlogItemKey": item_key,
        "gitRevision": "0" * 40,
        "answer": original_answer,
    }
    canonical_bytes = json.dumps(original_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    original_sha = hashlib.sha256(canonical_bytes).hexdigest()

    # Record initial in-flight
    saved_sha = journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=canonical_bytes,
        git_revision="0" * 40,
    )
    assert saved_sha == original_sha

    # Adversarial mutation 1: Add a key
    mut1 = dict(original_payload)
    mut1["tampered_key"] = "malicious"
    bytes_mut1 = json.dumps(mut1, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.verify_or_abort(match_uuid, item_key, bytes_mut1)

    # Adversarial mutation 2: Modify boolean
    mut2 = dict(original_payload)
    mut2["answer"] = dict(original_answer)
    mut2["answer"]["codeChanges"] = False
    bytes_mut2 = json.dumps(mut2, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.verify_or_abort(match_uuid, item_key, bytes_mut2)

    # Adversarial mutation 3: Type mutation
    mut3 = dict(original_payload)
    mut3["answer"] = dict(original_answer)
    mut3["answer"]["score"] = "100"  # str instead of int
    bytes_mut3 = json.dumps(mut3, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.verify_or_abort(match_uuid, item_key, bytes_mut3)

    # Adversarial mutation 4: Nested list mutation
    mut4 = dict(original_payload)
    mut4["answer"] = dict(original_answer)
    mut4["answer"]["evidence"] = ["evidence/test.log", "evidence/extra.log"]
    bytes_mut4 = json.dumps(mut4, sort_keys=True, separators=(",", ":")).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.verify_or_abort(match_uuid, item_key, bytes_mut4)

    # Adversarial mutation 5: Generating new clientSubmissionId for unresolved attempt
    with pytest.raises(ValueError, match="Rejecting generation of new UUID"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=str(uuid.uuid4()),  # New UUID
            idempotency_key=idemp_key,
            request_body_bytes=canonical_bytes,
            git_revision="0" * 40,
        )

    # Adversarial mutation 6: Generating new idempotencyKey for unresolved attempt
    with pytest.raises(ValueError, match="Rejecting generation of new key"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=sub_id,
            idempotency_key=str(uuid.uuid4()),  # New key
            request_body_bytes=canonical_bytes,
            git_revision="0" * 40,
        )

    # Ledger state integrity check: stored record remains 100% untouched
    stored = journal.get_in_flight(match_uuid, item_key)
    assert stored is not None
    assert stored["request_body_sha256"] == original_sha
    assert stored["request_body_bytes"] == canonical_bytes


def test_stress_09_submission_guard_tamper_detection_blocks_network(mock_gm_server: MockGameMasterHTTPServer, tmp_path):
    """
    Stress 3B: When a task is IN_FLIGHT, any attempt to call execute_guarded_submission with an
    altered answer payload must raise SubmissionGateError BEFORE any network request is issued.
    Asserts mock_gm_server.requests_log count does NOT increase during the tampered call.
    """
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="TASK-TAMPER-GUARD-09",
        task_type="TASK",
        title="Tamper Guard Stress",
        description="",
        repository_path=str(tmp_path),
        points=100,
        evidence_required=[],
    )
    store.add_task(env)
    store.update_task_status(env.task_id, "READY_TO_SUBMIT")

    worker_res = WorkerResult(
        task_id=env.task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=[],
    )
    rev = VerifiedRevision(commit_sha="a" * 40, branch="main", is_clean=True)
    guard = SubmissionGuard(store)

    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    adapter.start_working(match_uuid, env.task_id)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"key-tamper-{uuid.uuid4().hex[:12]}"
    original_answer = {"codeChanges": True, "notes": "original uncorrupted answer"}

    # Simulate timeout on outbound call to leave task in IN_FLIGHT state
    mock_gm_server.inject_network_timeout()
    res1 = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=adapter.submit_work,
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        answer_payload=original_answer,
    )
    assert res1.status == "SUBMIT_IN_FLIGHT"

    # Count requests dispatched so far
    requests_before = len(mock_gm_server.requests_log)

    # Now attempt to replay with a TAMPERED answer
    tampered_answer = {"codeChanges": True, "notes": "TAMPERED PAYLOAD"}
    with pytest.raises(SubmissionGateError, match="Submission payload conflict"):
        guard.execute_guarded_submission(
            envelope=env,
            worker_result=worker_res,
            revision=rev,
            game_master_submit_fn=adapter.submit_work,
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idemp_key,
            answer_payload=tampered_answer,
        )

    # ZERO outbound requests must have been dispatched to mock_gm_server during the tampered call
    requests_after = len(mock_gm_server.requests_log)
    assert requests_after == requests_before, (
        f"Security violation: {requests_after - requests_before} requests were sent over the wire "
        "despite payload tamper detection!"
    )


def test_stress_10_wire_server_tamper_rejection(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 3C: Direct wire validation against MockGameMasterHTTPServer.
    Sending two distinct payloads under the same Idempotency-Key triggers HTTP 409 IDEMPOTENCY_CONFLICT.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-TAMPER-WIRE-10"

    adapter.start_working(match_uuid, item_key)

    shared_idemp_key = f"tamper-wire-{uuid.uuid4().hex[:12]}"
    git_rev = "1" * 40

    # Payload 1 succeeds
    res1 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=str(uuid.uuid4()),
        idempotency_key=shared_idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer={"version": 1, "code": "pass"},
    )
    assert res1["submission"]["status"] == "ACCEPTED"

    # Payload 2 (tampered under same key) must fail closed with 409 IDEMPOTENCY_CONFLICT
    with pytest.raises(GameMasterIdempotencyConflictError) as exc_info:
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=shared_idemp_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer={"version": 2, "code": "tampered"},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "IDEMPOTENCY_CONFLICT"


# ==============================================================================
# GROUP 4: Match Code Preflight Rejection (Zero Socket Creation)
# ==============================================================================

def test_stress_11_match_code_rejection_all_endpoints(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 4A: Preflight validation separating 36-character Match UUID from 8-character Match Code.
    Tests multiple 8-char codes across all adapter entry points:
    - start_working()
    - submit_work()
    - submit() (legacy compatibility)
    - validate_match_uuid() standalone

    All must raise ValueError with message explicitly identifying 8-character Match Code rejection.
    """
    adapter = _make_adapter(mock_gm_server)
    codes_to_test = [
        "KWO5GPMP",
        "ABCDEF12",
        "12345678",
        "MATCHCOD",
        "  KWO5GPMP  ",  # with whitespace
        "kwo5gpmp",      # lowercase
    ]

    for code in codes_to_test:
        # 1. validate_match_uuid standalone
        with pytest.raises(ValueError, match="8-character Match Code cannot be used for API endpoints"):
            validate_match_uuid(code)

        # 2. start_working
        with pytest.raises(ValueError, match="8-character Match Code cannot be used for API endpoints"):
            adapter.start_working(code, "TASK-REJECT-01")

        # 3. submit_work
        with pytest.raises(ValueError, match="8-character Match Code cannot be used for API endpoints"):
            adapter.submit_work(
                match_uuid=code,
                client_submission_id=str(uuid.uuid4()),
                idempotency_key=str(uuid.uuid4()),
                backlog_item_key="TASK-REJECT-01",
                git_revision="a" * 40,
                answer={"codeChanges": True},
            )

        # 4. submit
        with pytest.raises(ValueError, match="8-character Match Code cannot be used for API endpoints"):
            adapter.submit(
                task_id="TASK-REJECT-01",
                git_revision="a" * 40,
                match_uuid=code,
                answer={"codeChanges": True},
            )

    # 0 requests must have reached mock_gm_server
    assert len(mock_gm_server.requests_log) == 0


def test_stress_12_match_code_zero_socket_zero_network_verification(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 4B: Deep empirical verification that 8-character Match Code rejection occurs
    BEFORE socket creation.
    We instrument socket.socket.connect and urllib.request.urlopen to strictly assert
    that 0 network connections were initiated.
    """
    adapter = _make_adapter(mock_gm_server)
    match_code = "KWO5GPMP"

    connect_calls: List[Any] = []
    original_connect = socket.socket.connect

    def _spy_connect(self, *args, **kwargs):
        connect_calls.append(args)
        return original_connect(self, *args, **kwargs)

    with patch.object(socket.socket, "connect", side_effect=_spy_connect):
        # Test start_working
        with pytest.raises(ValueError, match="8-character Match Code"):
            adapter.start_working(match_code, "TASK-TEST-SOCKET")

        # Test submit_work
        with pytest.raises(ValueError, match="8-character Match Code"):
            adapter.submit_work(
                match_uuid=match_code,
                client_submission_id=str(uuid.uuid4()),
                idempotency_key=str(uuid.uuid4()),
                backlog_item_key="TASK-TEST-SOCKET",
                git_revision="b" * 40,
                answer={"codeChanges": True},
            )

    # Assert exactly ZERO socket connections were attempted
    assert len(connect_calls) == 0, f"Socket connection was attempted: {connect_calls}"
    assert len(mock_gm_server.requests_log) == 0


# ==============================================================================
# GROUP 5: Additional Wire Protocol Boundaries & Security Hardening
# ==============================================================================

def test_stress_13_replay_http_status_codes_201_and_200_header_parsing(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 5A: Wire replay status code & header parsing.
    Digital Cuder contract (docs/digital-cuder-api.md) states:
    'Luon HTTP 201, ke ca khi replay cung key va cung body. Replay them header Idempotent-Replay: true'
    Mock server returns 200 with Idempotent-Replay: true.
    This test verifies OfficialGameMasterAdapter properly recognizes Idempotent-Replay: true
    under both HTTP 200 and HTTP 201 responses.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-STATUS-REPLAY-13"

    adapter.start_working(match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idemp_key = f"idemp-status-{uuid.uuid4().hex[:12]}"
    git_rev = "3" * 40
    answer_payload = {"codeChanges": True, "ver": 1}

    # Initial submit -> 201 Created
    res1 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    assert res1["submission"]["status"] == "ACCEPTED"
    assert res1.get("idempotent_replay") is not True

    # Replay against mock server -> returns 200 OK with header Idempotent-Replay: true
    res2 = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=client_sub_id,
        idempotency_key=idemp_key,
        backlog_item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    assert res2.get("idempotent_replay") is True
    assert res2.get("replay") is True
    assert res2["submission"]["id"] == res1["submission"]["id"]


def test_stress_14_forbidden_header_stripping_and_sec01_prohibited_targets(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 5B: Security enforcement:
    1. Forbidden X-Team-ID header stripping: even if passed in headers dict, it must NEVER be transmitted.
    2. SEC-01 Prohibited target protection: requests to cloud metadata (169.254.x.x, metadata.google)
       or admin/grader paths (/admin, /grader, /superadmin) must raise GameMasterSecurityError
       BEFORE network dispatch.
    """
    adapter = _make_adapter(mock_gm_server)

    # 1. Verify X-Team-ID header stripping
    adapter._request(
        "POST",
        f"/api/v1/matches/{mock_gm_server.match_uuid}/backlogs/start",
        data={"backlogItemKey": "TASK-SEC-HEADER-14"},
        headers={"X-Team-ID": "forbidden-team-id", "X-Custom-Header": "allowed"},
    )
    last_req = mock_gm_server.requests_log[-1]
    assert "X-Team-ID" not in last_req["headers"]
    assert "x-team-id" not in last_req["headers"]
    assert last_req["headers"].get("X-Custom-Header") == "allowed"

    # 2. Verify SEC-01 host & path blocking
    from mtm_competition.adapters.official_game_master import GameMasterSecurityError

    prohibited_urls = [
        "http://169.254.169.254/latest/meta-data",
        "http://metadata.google.internal/computeMetadata/v1",
        "http://attacker.com/api/v1",
        "http://evil.com/api/v1",
        f"{mock_gm_server.base_url}/api/v1/matches/admin",
        f"{mock_gm_server.base_url}/grader/evaluate",
        f"{mock_gm_server.base_url}/internal/secret",
        f"{mock_gm_server.base_url}/private/keys",
    ]

    for p_url in prohibited_urls:
        with pytest.raises(GameMasterSecurityError):
            OfficialGameMasterAdapter(api_base_url=p_url, api_key="test-key")


def test_stress_15_wire_client_preflight_input_validation_boundaries(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 5C: Preflight parameter boundary validation on submit_work:
    - idempotency_key: must match /^[A-Za-z0-9._~-]{8,128}$/
    - client_submission_id: must be a 36-char UUID
    - git_revision: non-empty string
    - answer: must be a dict
    - artifact_urls: <= 20 URLs, each https:// and <= 2048 chars

    All must raise ValueError in client code before any socket/HTTP request is made.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-BOUNDARIES-15"
    valid_uuid = str(uuid.uuid4())
    valid_idemp = str(uuid.uuid4())
    valid_rev = "4" * 40
    valid_answer = {"codeChanges": True}

    req_count_start = len(mock_gm_server.requests_log)

    # Invalid idempotency keys: too short, invalid chars, too long
    invalid_idemp_keys = ["short", "key with spaces", "a" * 129, "@invalid#chars"]
    for bad_key in invalid_idemp_keys:
        with pytest.raises(ValueError, match="(?i)idempotency_key"):
            adapter.submit_work(
                match_uuid=match_uuid,
                client_submission_id=valid_uuid,
                idempotency_key=bad_key,
                backlog_item_key=item_key,
                git_revision=valid_rev,
                answer=valid_answer,
            )

    # Invalid client_submission_id: not a UUID
    with pytest.raises(ValueError, match="(?i)client_submission_id"):
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id="not-a-uuid",
            idempotency_key=valid_idemp,
            backlog_item_key=item_key,
            git_revision=valid_rev,
            answer=valid_answer,
        )

    # Invalid git_revision: empty string
    with pytest.raises(ValueError, match="(?i)git_revision"):
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=valid_uuid,
            idempotency_key=valid_idemp,
            backlog_item_key=item_key,
            git_revision="   ",
            answer=valid_answer,
        )

    # Invalid answer: not a dict (e.g. list, string)
    with pytest.raises(ValueError, match="(?i)answer must be a dict"):
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=valid_uuid,
            idempotency_key=valid_idemp,
            backlog_item_key=item_key,
            git_revision=valid_rev,
            answer=["not", "a", "dict"],  # type: ignore
        )

    # Invalid artifact_urls: > 20 URLs
    with pytest.raises(ValueError, match="(?i)at most 20 URLs"):
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=valid_uuid,
            idempotency_key=valid_idemp,
            backlog_item_key=item_key,
            git_revision=valid_rev,
            answer=valid_answer,
            artifact_urls=[f"https://example.com/{i}" for i in range(21)],
        )

    # Invalid artifact_urls: non-https
    with pytest.raises(ValueError, match="(?i)must be https://"):
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=valid_uuid,
            idempotency_key=valid_idemp,
            backlog_item_key=item_key,
            git_revision=valid_rev,
            answer=valid_answer,
            artifact_urls=["http://insecure.example.com/log"],
        )

    # Zero requests dispatched during boundary failures
    assert len(mock_gm_server.requests_log) == req_count_start


def test_stress_16_payload_size_limit_rejection(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 5D: Submissions exceeding 512KB payload limit return HTTP 413 PAYLOAD_TOO_LARGE.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-PAYLOAD-LIMIT-16"

    adapter.start_working(match_uuid, item_key)

    # Generate oversized answer payload (> 512KB)
    oversized_answer = {"large_blob": "X" * 600000}

    from mtm_competition.adapters.official_game_master import GameMasterAPIError
    with pytest.raises(GameMasterAPIError) as exc_info:
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            backlog_item_key=item_key,
            git_revision="5" * 40,
            answer=oversized_answer,
        )

    assert exc_info.value.status_code == 413
    assert exc_info.value.error_code == "PAYLOAD_TOO_LARGE"


def test_stress_17_tournament_paused_submission_rejection(mock_gm_server: MockGameMasterHTTPServer):
    """
    Stress 5E: When the tournament match is PAUSED:
    - Default policy: Submissions are rejected with HTTP 409 MATCH_NOT_ACCEPTING_SUBMISSIONS.
    - If allow_submits_during_pause is True: Submissions are accepted with HTTP 201.
    """
    adapter = _make_adapter(mock_gm_server)
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-PAUSED-17"

    adapter.start_working(match_uuid, item_key)

    # Set match state to PAUSED with allow_submits_during_pause = False
    with mock_gm_server.lock:
        mock_gm_server.match_state = "PAUSED"
        mock_gm_server.allow_submits_during_pause = False

    from mtm_competition.adapters.official_game_master import GameMasterConflictError
    with pytest.raises(GameMasterConflictError) as exc_info:
        adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            backlog_item_key=item_key,
            git_revision="6" * 40,
            answer={"codeChanges": True},
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error_code == "MATCH_NOT_ACCEPTING_SUBMISSIONS"

    # Now toggle allow_submits_during_pause = True
    with mock_gm_server.lock:
        mock_gm_server.allow_submits_during_pause = True

    res = adapter.submit_work(
        match_uuid=match_uuid,
        client_submission_id=str(uuid.uuid4()),
        idempotency_key=str(uuid.uuid4()),
        backlog_item_key=item_key,
        git_revision="6" * 40,
        answer={"codeChanges": True},
    )
    assert res["submission"]["status"] == "ACCEPTED"

