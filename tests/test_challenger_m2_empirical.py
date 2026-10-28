"""Empirical Challenger Test Suite for Milestone 2 (Wire Contract & Error Propagation).

Stress-tests:
1. Immediate NotImplementedError on calls to legacy Game Master endpoints.
2. Rejection of legacy submit fallback (never calls POST /tasks/{id}/submit under any args or errors).
3. Strict session authentication (POST /api/v1/auth/login only; zero probing of /login or /api/auth/login).
4. SubmissionGuard zero-argument-stripping on TypeError and payload integrity preservation.
5. SubmissionGuard HTTP 409 ALREADY_SUBMITTED lock retention.
"""
from __future__ import annotations

import http.server
import json
import threading
import uuid
from typing import Any, Dict, List, Optional
import pytest

from mtm_competition.adapters.official_game_master import (
    GameMasterAlreadySubmittedError,
    GameMasterAPIError,
    GameMasterAuthError,
    GameMasterConflictError,
    GameMasterConnectionError,
    GameMasterError,
    GameMasterValidationError,
    OfficialGameMasterAdapter,
)
from mtm_competition.integration.git_guard import (
    SubmissionGateError,
    VerifiedRevision,
)
from mtm_competition.integration.submission_guard import (
    SubmissionGuard,
    SubmissionResult,
)
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.tasks.store import TaskStore
from mtm_core.contracts import (
    TaskEnvelope,
    TaskState,
    WorkerResult,
    WorkerStatus,
)


# ==============================================================================
# Helper Mock HTTP Servers for Live Network Verification
# ==============================================================================

class _RequestTrackingHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        pass  # Suppress console logging

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length) if content_length > 0 else b""
        self.server.recorded_requests.append({
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": body,
        })

        # Route matching
        if self.path in self.server.post_routes:
            handler = self.server.post_routes[self.path]
            status, resp_headers, resp_body = handler(self.headers, body)
            self.send_response(status)
            for k, v in resp_headers.items():
                self.send_header(k, v)
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)
            return

        # Default 404 for unconfigured routes
        self.send_response(404)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"error": {"code": "NOT_FOUND", "message": "Route not found"}}')

    def do_GET(self) -> None:
        self.server.recorded_requests.append({
            "method": "GET",
            "path": self.path,
            "headers": dict(self.headers),
            "body": b"",
        })

        if self.path in self.server.get_routes:
            handler = self.server.get_routes[self.path]
            status, resp_headers, resp_body = handler(self.headers)
            self.send_response(status)
            for k, v in resp_headers.items():
                self.send_header(k, v)
            self.end_headers()
            if resp_body:
                self.wfile.write(resp_body)
            return

        for route_prefix, handler in self.server.prefix_get_routes.items():
            if self.path.startswith(route_prefix):
                status, resp_headers, resp_body = handler(self.path, self.headers)
                self.send_response(status)
                for k, v in resp_headers.items():
                    self.send_header(k, v)
                self.end_headers()
                if resp_body:
                    self.wfile.write(resp_body)
                return

        self.send_response(404)
        self.end_headers()


class _LiveTrackingServer(http.server.HTTPServer):
    def __init__(self, server_address: tuple[str, int]):
        super().__init__(server_address, _RequestTrackingHandler)
        self.recorded_requests: list[dict[str, Any]] = []
        self.post_routes: dict[str, Any] = {}
        self.get_routes: dict[str, Any] = {}
        self.prefix_get_routes: dict[str, Any] = {}


@pytest.fixture
def tracking_server():
    server = _LiveTrackingServer(("127.0.0.1", 0))
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    server.server_close()


# ==============================================================================
# 1. Immediate NotImplementedError on Legacy Endpoints
# ==============================================================================

def test_legacy_endpoints_raise_not_implemented_immediately():
    """Verify that all deprecated GameMaster endpoints raise NotImplementedError immediately
    without making network calls or accepting legacy usage.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url="http://127.0.0.1:9",  # Non-existent port; must fail before network
        api_key="test-key",
    )

    # 1. get_match_state
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.get_match_state()
    assert "Digital Cuder match state is accessed via get_match_projection" in str(exc_info.value)

    # 2. list_backlog
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.list_backlog()
    assert "Digital Cuder does not provide /backlog" in str(exc_info.value)

    # 3. get_task
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.get_task("TASK-001")
    assert "Digital Cuder does not provide /tasks/{id}" in str(exc_info.value)

    # 4. get_mvs
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.get_mvs("TASK-001")
    assert "Digital Cuder does not provide /tasks/{id}/mvs" in str(exc_info.value)

    # 5. get_submission_status
    with pytest.raises(NotImplementedError) as exc_info:
        adapter.get_submission_status("TASK-001")
    assert "Digital Cuder does not provide /tasks/{id}/submission" in str(exc_info.value)

    # 6. get_match_projection without match identifier calls get_match_state and raises
    with pytest.raises(NotImplementedError):
        adapter.get_match_projection(None)


# ==============================================================================
# 2. Rejection of Legacy submit() Fallback
# ==============================================================================

def test_submit_rejects_missing_contract_parameters_without_legacy_fallback():
    """Verify submit() strictly rejects missing match_uuid or missing answer with ValueError
    and does not fall back to POST /tasks/{id}/submit.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url="http://127.0.0.1:9",
        api_key="test-key",
    )

    # Missing both match_uuid and answer
    with pytest.raises(ValueError) as exc:
        adapter.submit(task_id="TASK-01", git_revision="a" * 40)
    assert "Legacy endpoint POST /tasks/{id}/submit has been eliminated" in str(exc.value)

    # Missing answer only
    with pytest.raises(ValueError) as exc:
        adapter.submit(
            task_id="TASK-01",
            git_revision="a" * 40,
            match_uuid="11111111-1111-1111-1111-111111111111",
            answer=None,
        )
    assert "Legacy endpoint POST /tasks/{id}/submit has been eliminated" in str(exc.value)

    # Missing match_uuid only
    with pytest.raises(ValueError) as exc:
        adapter.submit(
            task_id="TASK-01",
            git_revision="a" * 40,
            match_uuid=None,
            answer={"solution": 42},
        )
    assert "Legacy endpoint POST /tasks/{id}/submit has been eliminated" in str(exc.value)


def test_submit_never_dispatches_to_legacy_route_on_server_errors(tracking_server):
    """Verify that submit() with valid wire arguments sends strictly to
    POST /api/v1/matches/{MATCH_UUID}/submissions, and NEVER attempts
    to dispatch to POST /tasks/{id}/submit even when the server errors.
    """
    port = tracking_server.server_address[1]
    match_uuid = "22222222-2222-2222-2222-222222222222"
    base_url = f"http://127.0.0.1:{port}"

    adapter = OfficialGameMasterAdapter(
        api_base_url=f"{base_url}/api/v1",
        api_key="valid-bearer-token",
        match_uuid=match_uuid,
    )

    # Case A: Server returns HTTP 422 Unprocessable Entity
    tracking_server.post_routes[f"/api/v1/matches/{match_uuid}/submissions"] = lambda hdrs, body: (
        422,
        {"Content-Type": "application/json"},
        json.dumps({
            "error": {"code": "VALIDATION_FAILED", "message": "Missing required field in answer"}
        }).encode("utf-8"),
    )

    with pytest.raises(GameMasterValidationError) as exc_info:
        adapter.submit(
            task_id="TASK-01",
            git_revision="b" * 40,
            match_uuid=match_uuid,
            answer={"invalid": True},
        )
    assert exc_info.value.status_code == 422

    # Assert exactly 1 request occurred, targeting the official submissions endpoint
    assert len(tracking_server.recorded_requests) == 1
    req = tracking_server.recorded_requests[0]
    assert req["path"] == f"/api/v1/matches/{match_uuid}/submissions"
    assert "/tasks/" not in req["path"]

    # Case B: Server returns HTTP 409 Conflict
    tracking_server.recorded_requests.clear()
    tracking_server.post_routes[f"/api/v1/matches/{match_uuid}/submissions"] = lambda hdrs, body: (
        409,
        {"Content-Type": "application/json"},
        json.dumps({
            "error": {"code": "ALREADY_SUBMITTED", "message": "Item already submitted"}
        }).encode("utf-8"),
    )

    with pytest.raises(GameMasterAlreadySubmittedError) as exc_info:
        adapter.submit(
            task_id="TASK-01",
            git_revision="b" * 40,
            match_uuid=match_uuid,
            answer={"solution": True},
        )
    assert exc_info.value.status_code == 409
    assert len(tracking_server.recorded_requests) == 1
    assert tracking_server.recorded_requests[0]["path"] == f"/api/v1/matches/{match_uuid}/submissions"
    assert "/tasks/" not in tracking_server.recorded_requests[0]["path"]

    # Case C: Server returns HTTP 500 Internal Server Error
    tracking_server.recorded_requests.clear()
    tracking_server.post_routes[f"/api/v1/matches/{match_uuid}/submissions"] = lambda hdrs, body: (
        500,
        {"Content-Type": "application/json"},
        json.dumps({
            "error": {"code": "INTERNAL_ERROR", "message": "Server failure"}
        }).encode("utf-8"),
    )

    with pytest.raises(GameMasterAPIError) as exc_info:
        adapter.submit(
            task_id="TASK-01",
            git_revision="b" * 40,
            match_uuid=match_uuid,
            answer={"solution": True},
        )
    assert exc_info.value.status_code == 500
    assert len(tracking_server.recorded_requests) == 1
    assert tracking_server.recorded_requests[0]["path"] == f"/api/v1/matches/{match_uuid}/submissions"
    assert "/tasks/" not in tracking_server.recorded_requests[0]["path"]


# ==============================================================================
# 3. Strict Session Authentication (POST /api/v1/auth/login Only)
# ==============================================================================

def test_login_session_strictly_calls_official_auth_endpoint(tracking_server):
    """Verify that login_session calls strictly POST /api/v1/auth/login,
    and NEVER probes /login or /api/auth/login on success or failure.
    """
    port = tracking_server.server_address[1]
    match_code = "KWO5GPMP"
    match_uuid = "33333333-3333-3333-3333-333333333333"
    origin = f"http://127.0.0.1:{port}"
    page_url = f"{origin}/match/{match_code}"

    adapter = OfficialGameMasterAdapter(
        api_base_url=f"{origin}/api/v1",
        api_key="",
    )

    # 1. Success Flow: /api/v1/auth/login returns token & cookie, /match/{code} returns Next.js
    tracking_server.post_routes["/api/v1/auth/login"] = lambda hdrs, body: (
        200,
        {
            "Content-Type": "application/json",
            "Set-Cookie": "session_token=test_sess_tok; Path=/; HttpOnly",
        },
        json.dumps({"token": "test_sess_tok", "status": "OK"}).encode("utf-8"),
    )

    nextjs_html = f"""<!DOCTYPE html><html><body>
    <script id="__NEXT_DATA__" type="application/json">
    {{
      "props": {{
        "pageProps": {{
          "match": {{
            "matchUuid": "{match_uuid}",
            "matchCode": "{match_code}",
            "status": "IN_PROGRESS",
            "backlogItems": [{{"id": "TASK-1", "title": "First"}}]
          }},
          "team": {{"teamId": "BAN", "teamName": "Ban Team"}}
        }}
      }}
    }}
    </script></body></html>"""

    tracking_server.get_routes[f"/match/{match_code}"] = lambda hdrs: (
        200,
        {"Content-Type": "text/html; charset=utf-8"},
        nextjs_html.encode("utf-8"),
    )

    res = adapter.login_session(
        username="test_user",
        password="test_password",
        match_page_url=page_url,
        allowed_origins=[origin],
        match_id=match_code,
    )

    assert res["status"] == "AUTHENTICATED"
    assert res["session_token"] == "test_sess_tok"
    assert res["projection"]["matchCode"] == match_code

    paths = [r["path"] for r in tracking_server.recorded_requests]
    assert paths == ["/api/v1/auth/login", f"/match/{match_code}"]
    assert "/login" not in paths
    assert "/api/auth/login" not in paths

    # 2. Failure Flow: /api/v1/auth/login returns 404 (simulating missing endpoint on non-standard server)
    tracking_server.recorded_requests.clear()
    tracking_server.post_routes["/api/v1/auth/login"] = lambda hdrs, body: (
        404,
        {"Content-Type": "application/json"},
        b'{"error": "Not Found"}',
    )

    with pytest.raises(GameMasterAPIError) as exc_info:
        adapter.login_session(
            username="test_user",
            password="test_password",
            match_page_url=page_url,
            allowed_origins=[origin],
            match_id=match_code,
        )
    assert exc_info.value.status_code == 404

    # Crucial check: verify NO probing of alternative login paths occurred
    probed_paths = [r["path"] for r in tracking_server.recorded_requests]
    assert probed_paths == ["/api/v1/auth/login"]
    assert "/login" not in probed_paths
    assert "/api/auth/login" not in probed_paths

    # 3. Failure Flow: /api/v1/auth/login returns 401 Unauthorized
    tracking_server.recorded_requests.clear()
    tracking_server.post_routes["/api/v1/auth/login"] = lambda hdrs, body: (
        401,
        {"Content-Type": "application/json"},
        b'{"error": {"code": "INVALID_CREDENTIALS", "message": "Bad password"}}',
    )

    with pytest.raises(GameMasterAuthError) as exc_info:
        adapter.login_session(
            username="test_user",
            password="bad_password",
            match_page_url=page_url,
            allowed_origins=[origin],
            match_id=match_code,
        )
    assert exc_info.value.status_code == 401

    probed_paths = [r["path"] for r in tracking_server.recorded_requests]
    assert probed_paths == ["/api/v1/auth/login"]
    assert "/login" not in probed_paths
    assert "/api/auth/login" not in probed_paths


# ==============================================================================
# 4. SubmissionGuard Zero Argument Stripping & Payload Preservation
# ==============================================================================

def test_submission_guard_zero_argument_stripping_on_type_error(tmp_path):
    """Verify that SubmissionGuard NEVER strips keyword arguments upon TypeError
    and preserves fail-closed behavior without retry loops.
    """
    db_path = str(tmp_path / "test_journal.db")
    journal = SubmissionJournal(db_path=db_path)
    store = TaskStore(":memory:")

    task_id = "TASK-STRESS-01"
    match_uuid = "44444444-4444-4444-4444-444444444444"
    env = TaskEnvelope(
        task_id=task_id,
        task_type="BUG",
        title="Test Task",
        description="desc",
        repository_path=str(tmp_path),
        points=100,
    )
    store.add_task(env)
    store.update_task_status(task_id, TaskState.READY_TO_SUBMIT)

    worker_result = WorkerResult(
        task_id=task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=["evidence.log"],
    )
    rev = VerifiedRevision(commit_sha="e" * 40, branch="main", is_clean=True)

    call_count = 0
    received_call_kwargs: list[dict[str, Any]] = []

    def mock_submit_fn_raising_type_error(**kwargs):
        nonlocal call_count
        call_count += 1
        received_call_kwargs.append(dict(kwargs))
        # Simulate a signature mismatch raising TypeError
        raise TypeError("Unexpected keyword argument 'answer'")

    guard = SubmissionGuard(
        task_store=store,
        journal=journal,
    )

    res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_result,
        revision=rev,
        game_master_submit_fn=mock_submit_fn_raising_type_error,
        match_uuid=match_uuid,
        answer_payload={"answer_key": "valid_value"},
        strict_git_gate=False,
    )

    # In legacy code, TypeError was caught and retried with stripped arguments.
    # In refactored Milestone 2 code, it must NOT strip arguments, and call_count must be exactly 1!
    assert call_count == 1, f"Expected exactly 1 call without argument-stripping retry, got {call_count}"
    assert not res.success
    assert "Unexpected keyword argument 'answer'" in (res.error_message or "")
    assert res.status == "SUBMIT_IN_FLIGHT"


def test_submission_guard_preserves_full_wire_payload(tmp_path):
    """Verify that SubmissionGuard passes the complete, uncorrupted wire payload
    (match_uuid, client_submission_id, idempotency_key, backlog_item_key,
     git_revision, answer, artifact_urls, serialized_body_bytes)
    and synchronizes it with SubmissionJournal.
    """
    db_path = str(tmp_path / "test_journal_wire.db")
    journal = SubmissionJournal(db_path=db_path)
    store = TaskStore(":memory:")

    task_id = "TASK-WIRE-01"
    match_uuid = "55555555-5555-5555-5555-555555555555"
    commit_sha = "c" * 40
    env = TaskEnvelope(
        task_id=task_id,
        task_type="FEATURE",
        title="Wire Task",
        description="desc",
        repository_path=str(tmp_path),
        points=100,
    )
    store.add_task(env)
    store.update_task_status(task_id, TaskState.READY_TO_SUBMIT)

    expected_answer = {"computed_sum": 4242, "status": "verified"}
    worker_result = WorkerResult(
        task_id=task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=["test.log"],
    )
    rev = VerifiedRevision(commit_sha=commit_sha, branch="main", is_clean=True)

    dispatched_args: dict[str, Any] = {}

    def mock_official_submit_fn(
        match_uuid: str,
        client_submission_id: str,
        idempotency_key: str,
        backlog_item_key: str,
        git_revision: str,
        answer: dict[str, Any],
        artifact_urls: Optional[list[str]] = None,
        serialized_body_bytes: Optional[bytes] = None,
    ):
        dispatched_args.update({
            "match_uuid": match_uuid,
            "client_submission_id": client_submission_id,
            "idempotency_key": idempotency_key,
            "backlog_item_key": backlog_item_key,
            "git_revision": git_revision,
            "answer": answer,
            "artifact_urls": artifact_urls,
            "serialized_body_bytes": serialized_body_bytes,
        })
        return {
            "submission": {
                "id": "official-receipt-uuid-999",
                "matchId": match_uuid,
                "backlogItemKey": backlog_item_key,
                "status": "ACCEPTED",
                "scoreStatus": "PENDING",
            }
        }

    guard = SubmissionGuard(
        task_store=store,
        journal=journal,
    )

    res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_result,
        revision=rev,
        game_master_submit_fn=mock_official_submit_fn,
        match_uuid=match_uuid,
        answer_payload=expected_answer,
        artifact_urls=["https://artifacts.org/test.tar.gz"],
        strict_git_gate=False,
    )

    assert res.success is True
    assert res.status == "ACCEPTED"
    assert res.submission_id == "official-receipt-uuid-999"

    # Verify all wire arguments
    assert dispatched_args["match_uuid"] == match_uuid
    assert dispatched_args["backlog_item_key"] == task_id
    assert dispatched_args["git_revision"] == commit_sha
    assert dispatched_args["answer"] == expected_answer
    assert dispatched_args["artifact_urls"] == ["https://artifacts.org/test.tar.gz"]

    # Verify serialized_body_bytes canonical JSON format
    raw_bytes = dispatched_args["serialized_body_bytes"]
    assert raw_bytes is not None
    decoded = json.loads(raw_bytes.decode("utf-8"))
    assert decoded["clientSubmissionId"] == dispatched_args["client_submission_id"]
    assert decoded["backlogItemKey"] == task_id
    assert decoded["gitRevision"] == commit_sha
    assert decoded["answer"] == expected_answer
    assert decoded["artifactUrls"] == ["https://artifacts.org/test.tar.gz"]

    # Verify SubmissionJournal recorded receipt
    entry = journal.get_entry(match_uuid, task_id)
    assert entry is not None
    assert entry["state"] == "ACCEPTED"
    assert entry["receipt_id"] == "official-receipt-uuid-999"


# ==============================================================================
# 5. SubmissionGuard HTTP 409 ALREADY_SUBMITTED Lock Retention
# ==============================================================================

def test_submission_guard_409_already_submitted_retains_lock(tmp_path):
    """Verify that when a 409 ALREADY_SUBMITTED error occurs, SubmissionGuard routes
    it to journal.record_already_submitted(), retaining the lock in the journal
    rather than marking it REJECTED or clearing the in-flight lock.
    """
    db_path = str(tmp_path / "test_journal_409.db")
    journal = SubmissionJournal(db_path=db_path)
    store = TaskStore(":memory:")

    task_id = "TASK-409-LOCK"
    match_uuid = "66666666-6666-6666-6666-666666666666"
    env = TaskEnvelope(
        task_id=task_id,
        task_type="FEATURE",
        title="Lock Task",
        description="desc",
        repository_path=str(tmp_path),
        points=100,
    )
    store.add_task(env)
    store.update_task_status(task_id, TaskState.READY_TO_SUBMIT)

    worker_result = WorkerResult(
        task_id=task_id,
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[],
        evidence_paths=["test.log"],
    )
    rev = VerifiedRevision(commit_sha="f" * 40, branch="main", is_clean=True)

    def mock_submit_409(**kwargs):
        raise GameMasterAlreadySubmittedError(
            status_code=409,
            message="Conflict: item has already been submitted",
            payload={"error": {"code": "ALREADY_SUBMITTED", "message": "Item already submitted"}},
            error_code="ALREADY_SUBMITTED",
        )

    guard = SubmissionGuard(
        task_store=store,
        journal=journal,
    )

    res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_result,
        revision=rev,
        game_master_submit_fn=mock_submit_409,
        match_uuid=match_uuid,
        answer_payload={"solution": "lock"},
        strict_git_gate=False,
    )

    assert res.success is False
    assert res.status == "REJECTED"

    # Verify journal entry status is ALREADY_SUBMITTED (NOT generic REJECTED)
    entry = journal.get_entry(match_uuid, task_id)
    assert entry is not None
    assert entry["state"] == "ALREADY_SUBMITTED"
    assert entry["last_error_code"] == "ALREADY_SUBMITTED"


# ==============================================================================
# 6. Deep Adversarial Security & Parameter Boundary Stress Tests
# ==============================================================================

def test_sec01_prohibited_origins_and_paths_blocked_before_network():
    """Verify SEC-01 security rules reject prohibited origins and sensitive paths
    synchronously before any network dispatch.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url="http://127.0.0.1:8000/api/v1",
        api_key="valid-key",
    )

    # Prohibited hosts
    bad_hosts = [
        "http://169.254.169.254/api/v1",
        "http://metadata.google.internal/api/v1",
        "http://instance-data/api/v1",
        "http://evil-attacker.com/api/v1",
    ]
    for bad_url in bad_hosts:
        with pytest.raises(Exception) as exc_info:
            OfficialGameMasterAdapter(api_base_url=bad_url, api_key="key")
        assert "SEC-01" in str(exc_info.value)

    # Prohibited paths
    bad_paths = [
        "/admin/system",
        "/grader/evaluate",
        "/internal/metrics",
        "/private/keys",
        "/superadmin/reset",
        "/meta-data/secrets",
        "/latest/meta-data",
    ]
    for path in bad_paths:
        with pytest.raises(Exception) as exc_info:
            adapter._build_url(path)
        assert "SEC-01" in str(exc_info.value)


def test_uuid_and_idempotency_key_validation():
    """Verify that match_uuid, client_submission_id, and idempotency_key
    are strictly validated before network calls.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url="http://127.0.0.1:9",
        api_key="valid-key",
    )

    # 1. 8-char match code rejected in start_working and submit_work
    with pytest.raises(ValueError, match="8-character Match Code cannot be used"):
        adapter.start_working("KWO5GPMP", "TASK-01")

    with pytest.raises(ValueError, match="8-character Match Code cannot be used"):
        adapter.submit_work(
            match_uuid="KWO5GPMP",
            client_submission_id=str(uuid.uuid4()),
            idempotency_key=str(uuid.uuid4()),
            backlog_item_key="TASK-01",
            git_revision="a" * 40,
            answer={"solution": 1},
        )

    # 2. Malformed client_submission_id (non-UUID)
    with pytest.raises(ValueError, match="must be a 36-char UUID"):
        adapter.submit_work(
            match_uuid=str(uuid.uuid4()),
            client_submission_id="not-a-valid-uuid",
            idempotency_key=str(uuid.uuid4()),
            backlog_item_key="TASK-01",
            git_revision="a" * 40,
            answer={"solution": 1},
        )

    # 3. Invalid idempotency key format (spaces or invalid symbols)
    with pytest.raises(ValueError, match="must match"):
        adapter.submit_work(
            match_uuid=str(uuid.uuid4()),
            client_submission_id=str(uuid.uuid4()),
            idempotency_key="bad key with spaces!",
            backlog_item_key="TASK-01",
            git_revision="a" * 40,
            answer={"solution": 1},
        )


def test_forbidden_x_team_id_header_stripped(tracking_server):
    """Verify that X-Team-ID header is stripped from outbound requests
    to conform to Digital Cuder token-only authentication.
    """
    port = tracking_server.server_address[1]
    match_uuid = "77777777-7777-7777-7777-777777777777"
    adapter = OfficialGameMasterAdapter(
        api_base_url=f"http://127.0.0.1:{port}/api/v1",
        api_key="valid-bearer-token",
        team_id="FORBIDDEN_TEAM",
    )

    tracking_server.post_routes[f"/api/v1/matches/{match_uuid}/backlogs/start"] = lambda hdrs, body: (
        200,
        {"Content-Type": "application/json"},
        json.dumps({"backlogId": "TASK-01", "minimumVerificationScenarios": None}).encode("utf-8"),
    )

    # Dispatch start_working, explicitly attempting to pass X-Team-ID in headers
    adapter._request(
        "POST",
        f"/api/v1/matches/{match_uuid}/backlogs/start",
        data={"backlogItemKey": "TASK-01"},
        headers={"X-Team-ID": "FORBIDDEN_TEAM", "X-Custom-Trace": "trace-123"},
    )

    assert len(tracking_server.recorded_requests) == 1
    hdrs = tracking_server.recorded_requests[0]["headers"]
    # Verify X-Team-ID is completely absent
    assert "x-team-id" not in {k.lower() for k in hdrs.keys()}
    assert "X-Team-ID" not in hdrs
    # Verify custom trace header survived
    assert hdrs.get("X-Custom-Trace") == "trace-123"


def test_submission_journal_tampering_rejection(tmp_path):
    """Verify that SubmissionJournal detects and blocks any payload alteration
    while a submission is in-flight.
    """
    db_path = str(tmp_path / "tamper_test.db")
    journal = SubmissionJournal(db_path=db_path)
    match_uuid = str(uuid.uuid4())
    task_id = "TASK-TAMPER-01"
    sub_id = str(uuid.uuid4())
    idemp_key = str(uuid.uuid4())

    body1 = json.dumps({"answer": "original"}).encode("utf-8")
    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=task_id,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=body1,
        git_revision="a" * 40,
        remote_ref="refs/heads/main",
    )

    # Attempt to replay with altered body must raise ValueError
    altered_body = json.dumps({"answer": "tampered"}).encode("utf-8")
    with pytest.raises(ValueError, match="Submission payload conflict"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=task_id,
            client_submission_id=sub_id,
            idempotency_key=idemp_key,
            request_body_bytes=altered_body,
            git_revision="a" * 40,
            remote_ref="refs/heads/main",
        )
