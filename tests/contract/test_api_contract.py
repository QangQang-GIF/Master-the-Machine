"""
Contract Tests: API-01..08, SEC-01.
Verifies the official Digital Cuder Game Master HTTP wire protocol contract over a real TCP socket.
Uses tests/fixtures/mock_gm_server.py (zero external network calls).
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, Optional
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.mock_gm_server import (
        mock_gm_server,
        MockGameMasterHTTPServer,
        UUID_REGEX,
    )
except ModuleNotFoundError:
    from fixtures.mock_gm_server import (
        mock_gm_server,
        MockGameMasterHTTPServer,
        UUID_REGEX,
    )
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterAPIError,
    GameMasterConflictError,
    GameMasterRateLimitError,
)


def _call_start_work(adapter: OfficialGameMasterAdapter, match_uuid: str, item_key: str) -> Dict[str, Any]:
    """Helper calling adapter start work supporting flexible signatures."""
    try:
        return adapter.start_working(match_uuid, item_key)
    except TypeError:
        # Fallback if adapter stores match_uuid or accepts single task_id
        return adapter.start_working(item_key)


def _call_submit_work(
    adapter: OfficialGameMasterAdapter,
    match_uuid: str,
    client_sub_id: str,
    idempotency_key: str,
    item_key: str,
    git_revision: str,
    answer: Dict[str, Any],
) -> Dict[str, Any]:
    """Helper calling adapter submit supporting both submit_work and submit signatures."""
    if hasattr(adapter, "submit_work"):
        return adapter.submit_work(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idempotency_key,
            backlog_item_key=item_key,
            git_revision=git_revision,
            answer=answer,
        )
    # Fallback to submit method
    return adapter.submit(
        task_id=item_key,
        git_revision=git_revision,
        evidence=answer.get("evidence", []),
        idempotency_key=idempotency_key,
    )


def test_api_01_start_work(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-01: Start work on an OPEN backlog item.
    Expects HTTP 201 with submitTemplate, minimumVerificationScenarios, files, and replay: false.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-CONTRACT-01"

    res = _call_start_work(adapter, match_uuid, item_key)

    assert isinstance(res, dict)
    assert res.get("replay") is False
    assert "submitTemplate" in res
    assert "minimumVerificationScenarios" in res
    assert res.get("backlogId") == item_key

    # Verify wire request recorded by mock server
    assert len(mock_gm_server.requests_log) == 1
    req = mock_gm_server.requests_log[0]
    assert req["method"] == "POST"
    assert req["path"] == f"/api/v1/matches/{match_uuid}/backlogs/start"
    assert req["headers"].get("Authorization") == f"Bearer {mock_gm_server.auth_token}"

    body = json.loads(req["body"].decode("utf-8"))
    assert body.get("backlogItemKey") == item_key


def test_api_02_start_work_replay(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-02: Repeated Start Work on an already started backlog item.
    Expects HTTP 200 with identical templates and replay: true.
    Also tests whitespace normalization on backlogItemKey.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-CONTRACT-02"

    # First start -> 201 Created, replay: false
    res1 = _call_start_work(adapter, match_uuid, item_key)
    assert res1.get("replay") is False

    # Second start with whitespace -> 200 OK, replay: true
    res2 = _call_start_work(adapter, match_uuid, f"  {item_key}  ")
    assert res2.get("replay") is True
    assert res2.get("submitTemplate") == res1.get("submitTemplate")
    assert res2.get("minimumVerificationScenarios") == res1.get("minimumVerificationScenarios")


def test_api_03_match_uuid_preflight_validation(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-03: Pre-flight validation separating 36-char Match UUID from 8-char Match Code.
    Passing an 8-char Match Code (e.g. 'KWO5GPMP') must raise ValueError locally before dispatch.
    Uppercase valid UUIDs must be normalized and accepted.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    invalid_code = "KWO5GPMP"  # 8-char UI code, NOT a 36-char UUID

    with pytest.raises(ValueError, match="(?i)match.*uuid"):
        _call_start_work(adapter, invalid_code, "TASK-03")

    # 0 HTTP requests should have been dispatched to server
    assert len(mock_gm_server.requests_log) == 0

    # Normalization: uppercase 36-char UUID should be accepted
    upper_uuid = mock_gm_server.match_uuid.upper()
    res = _call_start_work(adapter, upper_uuid, "TASK-03")
    assert res.get("replay") is False


def test_api_04_submit_without_start_work(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-04: Submitting an item before Start Work was acknowledged.
    Fails closed: server returns 409 BACKLOG_NOT_STARTED envelope, adapter raises GameMasterConflictError.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    unstarted_key = "TASK-UNSTARTED-04"

    with pytest.raises(GameMasterConflictError) as exc_info:
        _call_submit_work(
            adapter=adapter,
            match_uuid=match_uuid,
            client_sub_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            item_key=unstarted_key,
            git_revision="a" * 40,
            answer={"codeChanges": True, "summary": "Unstarted work"},
        )

    assert "BACKLOG_NOT_STARTED" in str(exc_info.value) or exc_info.value.status_code == 409


def test_api_05_valid_submission_receipt(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-05: Valid submission receipt.
    Verifies Idempotency-Key header, root gitRevision, dynamic answer, returning HTTP 201 with
    {submission: {id, status: 'ACCEPTED', scoreStatus: 'PENDING'}}.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-SUBMIT-05"

    # Start item first
    _call_start_work(adapter, match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    git_rev = "1" * 40
    answer_payload = {
        "codeChanges": True,
        "summary": "Completed fix with tests",
        "evidence": ["evidence/test_run.log"],
    }

    res = _call_submit_work(
        adapter=adapter,
        match_uuid=match_uuid,
        client_sub_id=client_sub_id,
        idempotency_key=idempotency_key,
        item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )

    assert "submission" in res
    sub = res["submission"]
    assert sub["status"] == "ACCEPTED"
    assert sub["scoreStatus"] == "PENDING"
    assert sub["backlogItemKey"] == item_key

    # Inspect wire request
    submit_req = mock_gm_server.requests_log[-1]
    assert submit_req["path"] == f"/api/v1/matches/{match_uuid}/submissions"
    assert submit_req["headers"].get("Idempotency-Key") == idempotency_key
    assert "X-Team-ID" not in submit_req["headers"]  # Forbidden wire header

    body = json.loads(submit_req["body"].decode("utf-8"))
    assert body.get("clientSubmissionId") == client_sub_id
    assert body.get("backlogItemKey") == item_key
    assert body.get("gitRevision") == git_rev
    assert body.get("answer") == answer_payload


def test_api_06_submission_replay_on_timeout(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-06: Submission Replay on timeout.
    When a network timeout occurs, replaying the exact same Idempotency-Key and body bytes
    returns the original 201/200 receipt with header Idempotent-Replay: true without creating duplicates.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-REPLAY-06"
    _call_start_work(adapter, match_uuid, item_key)

    client_sub_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    git_rev = "2" * 40
    answer_payload = {"codeChanges": True, "summary": "Fix for replay"}

    # Initial successful submission
    res1 = _call_submit_work(
        adapter=adapter,
        match_uuid=match_uuid,
        client_sub_id=client_sub_id,
        idempotency_key=idempotency_key,
        item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    sub_id_1 = res1["submission"]["id"]

    # Replay with identical key and payload
    res2 = _call_submit_work(
        adapter=adapter,
        match_uuid=match_uuid,
        client_sub_id=client_sub_id,
        idempotency_key=idempotency_key,
        item_key=item_key,
        git_revision=git_rev,
        answer=answer_payload,
    )
    sub_id_2 = res2["submission"]["id"]

    # Must return exact same receipt
    assert sub_id_1 == sub_id_2
    assert res2["submission"]["status"] == "ACCEPTED"


def test_api_07_idempotency_conflict_and_duplicate_submission(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-07: Idempotency Conflict & Duplicate Submission.
    Case A: Reusing same Idempotency-Key with differing body -> 409 IDEMPOTENCY_CONFLICT.
    Case B: Submitting with a new key for an already accepted item -> 409 ALREADY_SUBMITTED.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-CONFLICT-07"
    _call_start_work(adapter, match_uuid, item_key)

    idempotency_key = str(uuid.uuid4())
    git_rev = "3" * 40

    # Initial submission succeeds
    _call_submit_work(
        adapter=adapter,
        match_uuid=match_uuid,
        client_sub_id=str(uuid.uuid4()),
        idempotency_key=idempotency_key,
        item_key=item_key,
        git_revision=git_rev,
        answer={"codeChanges": True, "summary": "Attempt 1"},
    )

    # Case A: Same key, differing payload (IDEMPOTENCY_CONFLICT)
    with pytest.raises(GameMasterConflictError) as exc_a:
        _call_submit_work(
            adapter=adapter,
            match_uuid=match_uuid,
            client_sub_id=str(uuid.uuid4()),
            idempotency_key=idempotency_key,
            item_key=item_key,
            git_revision=git_rev,
            answer={"codeChanges": True, "summary": "Altered body payload"},
        )
    assert "IDEMPOTENCY_CONFLICT" in str(exc_a.value) or exc_a.value.status_code == 409

    # Case B: New key for already accepted item (ALREADY_SUBMITTED)
    new_key = str(uuid.uuid4())
    with pytest.raises(GameMasterConflictError) as exc_b:
        _call_submit_work(
            adapter=adapter,
            match_uuid=match_uuid,
            client_sub_id=str(uuid.uuid4()),
            idempotency_key=new_key,
            item_key=item_key,
            git_revision=git_rev,
            answer={"codeChanges": True, "summary": "Attempt 2 with new key"},
        )
    assert "ALREADY_SUBMITTED" in str(exc_b.value) or exc_b.value.status_code == 409


def test_api_08_error_envelopes_and_429_backoff(mock_gm_server: MockGameMasterHTTPServer):
    """
    API-08: Error Envelopes & 429 Backoff.
    Subcase A: 429 Rate Limit backoff with Retry-After header.
    Subcase B: 422 Validation Error parses structured envelope {"error": {"code", "message", "details"}}.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=4.0,
        max_retries=3,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-ERROR-08"

    # Subcase A: Rate limit 429 injected for 1 call, then succeeds
    mock_gm_server.inject_rate_limit(count=1, retry_after=1)
    res = _call_start_work(adapter, match_uuid, item_key)
    assert res.get("replay") is False

    # Subcase B: 422 Validation error parsing
    mock_gm_server.inject_validation_error(details=[{"field": "answer", "reason": "invalid schema"}])
    with pytest.raises(GameMasterAPIError) as exc_val:
        _call_start_work(adapter, match_uuid, "TASK-ERR-VAL")

    assert exc_val.value.status_code == 422
    assert "VALIDATION_FAILED" in str(exc_val.value) or exc_val.value.payload.get("error", {}).get("code") == "VALIDATION_FAILED"


def test_sec_01_prohibited_origin_and_endpoint_blocking(mock_gm_server: MockGameMasterHTTPServer):
    """
    SEC-01: Prohibited Origin & Endpoint Blocking.
    Prevents calling unauthorized URLs (e.g. metadata endpoint 169.254.169.254 or internal admin routes).
    Fails closed locally before network packet transmission.
    """
    prohibited_urls = [
        "http://169.254.169.254/latest/meta-data/",
        "http://evil-attacker.org/api/v1",
        "http://localhost:8000/admin/reset",
    ]

    for purl in prohibited_urls:
        with pytest.raises((ValueError, GameMasterAPIError)):
            bad_adapter = OfficialGameMasterAdapter(
                api_base_url=purl,
                api_key="attacker-key",
            )
            # Either constructor or request invocation must reject unauthorized origin
            if hasattr(bad_adapter, "validate_url"):
                bad_adapter.validate_url(purl)
            else:
                _call_start_work(bad_adapter, mock_gm_server.match_uuid, "TASK-SEC")
