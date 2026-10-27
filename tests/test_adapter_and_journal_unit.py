"""Unit tests for Milestone M-ADAPTER-JOURNAL:
- Session cookie authentication & origin validation in OfficialGameMasterAdapter
- Strict match projection schema validation
- Next.js <script id="__NEXT_DATA__"> pageProps extraction & preservation
- SubmissionJournal 409 ALREADY_SUBMITTED lock retention & safe rejection handling
"""
from __future__ import annotations

import http.server
import json
import threading
import uuid
import pytest

from mtm_competition.adapters.base import (
    validate_match_projection_schema,
    validate_match_uuid,
)
from mtm_competition.adapters.official_game_master import (
    GameMasterAlreadySubmittedError,
    GameMasterAuthError,
    GameMasterError,
    GameMasterSecurityError,
    GameMasterValidationError,
    OfficialGameMasterAdapter,
)
from mtm_competition.tasks.journal import SubmissionJournal


# ==============================================================================
# 1. Match Projection Schema Validation Tests
# ==============================================================================

def test_validate_match_projection_schema_valid():
    valid_with_uuid = {
        "matchUuid": "12345678-1234-1234-1234-123456789abc",
        "matchCode": "MATCH001",
        "backlogItems": [{"id": "TASK-1"}],
    }
    assert validate_match_projection_schema(valid_with_uuid) == valid_with_uuid

    valid_with_id = {
        "id": "12345678-1234-1234-1234-123456789abc",
        "matchCode": "MATCH001",
        "backlogItems": [],
    }
    assert validate_match_projection_schema(valid_with_id) == valid_with_id


def test_validate_match_projection_schema_invalid():
    # Empty dict
    with pytest.raises(ValueError, match="non-empty"):
        validate_match_projection_schema({})

    # Non-dict
    with pytest.raises(ValueError, match="non-empty"):
        validate_match_projection_schema(["not", "a", "dict"])

    # Missing matchUuid / id
    with pytest.raises(ValueError, match="'matchUuid' or 'id'"):
        validate_match_projection_schema({"matchCode": "ABC", "backlogItems": []})

    # Empty matchUuid
    with pytest.raises(ValueError, match="'matchUuid' or 'id'"):
        validate_match_projection_schema({"matchUuid": "   ", "matchCode": "ABC", "backlogItems": []})

    # Missing matchCode
    with pytest.raises(ValueError, match="'matchCode'"):
        validate_match_projection_schema({"matchUuid": "1234-uuid", "backlogItems": []})

    # Missing backlogItems
    with pytest.raises(ValueError, match="'backlogItems' as a list"):
        validate_match_projection_schema({"matchUuid": "1234-uuid", "matchCode": "CODE"})

    # backlogItems not a list
    with pytest.raises(ValueError, match="'backlogItems' as a list"):
        validate_match_projection_schema({
            "matchUuid": "1234-uuid",
            "matchCode": "CODE",
            "backlogItems": "not-a-list",
        })


# ==============================================================================
# 2. Session Cookie Authentication & Origin Validation Tests
# ==============================================================================

def test_login_session_sec01_origin_whitelist():
    adapter = OfficialGameMasterAdapter(api_base_url="http://localhost:8000/api/v1")
    # Disallowed origin must raise GameMasterSecurityError
    with pytest.raises(GameMasterSecurityError, match="SEC-01"):
        adapter.login_session(
            username="team1",
            password="pwd",
            match_page_url="http://attacker.com/match/123",
            allowed_origins=["http://localhost:8000", "https://official.tournament.org"],
        )


def test_login_session_missing_credentials():
    adapter = OfficialGameMasterAdapter(api_base_url="http://localhost:8000/api/v1")
    with pytest.raises(GameMasterAuthError, match="Username is required"):
        adapter.login_session(username="", password="pwd", match_page_url="http://localhost:8000/match/123")

    with pytest.raises(GameMasterAuthError, match="Password is required"):
        adapter.login_session(username="team", password="", match_page_url="http://localhost:8000/match/123")


class _MockSessionAuthServer(http.server.HTTPServer):
    def __init__(self, addr, handler):
        super().__init__(addr, handler)
        self.received_cookies = []
        self.auth_success = True


class _MockSessionHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Quiet logging

    def do_POST(self):
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len > 0 else b""
        data = json.loads(body.decode("utf-8")) if body else {}

        if self.path in ("/login", "/api/v1/auth/login", "/api/auth/login"):
            if not self.server.auth_success or data.get("password") == "wrong":
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"code": "AUTH_FAILED", "message": "Invalid credentials"}}).encode("utf-8"))
                return

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session_token=secret_cookie_token_999; Path=/; HttpOnly")
            self.end_headers()
            self.wfile.write(json.dumps({"token": "secret_cookie_token_999", "status": "OK"}).encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        self.server.received_cookies.append(cookie)

        if self.path.startswith("/match/"):
            # Check authenticated cookie
            if "session_token=secret_cookie_token_999" not in cookie:
                self.send_response(401)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"error": {"code": "UNAUTHENTICATED", "message": "Missing session cookie"}}).encode("utf-8"))
                return

            # Return Next.js HTML spectator page
            html_content = """<!DOCTYPE html>
<html>
<head><title>Match Spectator</title></head>
<body>
<script id="__NEXT_DATA__" type="application/json">
{
  "props": {
    "pageProps": {
      "match": {
        "matchUuid": "33333333-3333-3333-3333-333333333333",
        "matchCode": "SPECT888",
        "status": "RUNNING",
        "backlogItems": [{"id": "ITEM-1", "title": "First Item"}],
        "waveTimestamps": {"wave2": 840, "wave3": 1740}
      },
      "team": {
        "teamId": "TEAM-ALPHA",
        "teamName": "Alpha Squad"
      },
      "repository": {
        "url": "https://git.tournament.org/match-888.git",
        "branch": "main",
        "folder": "SPECT888"
      }
    }
  }
}
</script>
</body>
</html>"""
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html_content.encode("utf-8"))
            return

        self.send_response(404)
        self.end_headers()


@pytest.fixture
def mock_session_server():
    server = _MockSessionAuthServer(("127.0.0.1", 0), _MockSessionHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    server.server_close()


def test_login_session_authenticates_captures_cookies_and_parses_nextjs(mock_session_server):
    port = mock_session_server.server_port
    base_url = f"http://127.0.0.1:{port}"
    match_url = f"{base_url}/match/SPECT888"

    adapter = OfficialGameMasterAdapter(api_base_url=base_url)
    res = adapter.login_session(
        username="team_alpha",
        password="correct_password",
        match_page_url=match_url,
        allowed_origins=[base_url],
    )

    assert res["status"] == "AUTHENTICATED"
    assert res["session_token"] == "secret_cookie_token_999"
    assert "session_token" in adapter.session_cookies
    assert adapter.session_cookies["session_token"] == "secret_cookie_token_999"

    # Verify that Next.js state preserved all top-level pageProps
    proj = res["projection"]
    assert proj["matchUuid"] == "33333333-3333-3333-3333-333333333333"
    assert proj["matchCode"] == "SPECT888"
    assert len(proj["backlogItems"]) == 1
    assert proj["backlogItems"][0]["id"] == "ITEM-1"
    assert proj["team"]["teamId"] == "TEAM-ALPHA"
    assert proj["teamName"] == "Alpha Squad"
    assert proj["waveTimestamps"] == {"wave2": 840, "wave3": 1740}
    assert proj["repository"]["folder"] == "SPECT888"
    assert "pageProps" in proj


def test_login_session_auth_failure_fails_closed(mock_session_server):
    port = mock_session_server.server_port
    base_url = f"http://127.0.0.1:{port}"
    match_url = f"{base_url}/match/SPECT888"

    adapter = OfficialGameMasterAdapter(api_base_url=base_url)
    with pytest.raises(GameMasterAuthError):
        adapter.login_session(
            username="team_alpha",
            password="wrong",
            match_page_url=match_url,
            allowed_origins=[base_url],
        )


# ==============================================================================
# 3. SubmissionJournal 409 ALREADY_SUBMITTED & Rejection Tests
# ==============================================================================

def test_journal_record_already_submitted_retains_lock_and_blocks_resubmission():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-ALREADY-409"
    sub_id = str(uuid.uuid4())
    idemp_key = "idemp-409-test"
    body = b'{"answer": "payload"}'

    # 1. Record in flight
    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=body,
        git_revision="a" * 40,
    )

    # 2. Record 409 ALREADY_SUBMITTED
    journal.record_already_submitted(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        http_code=409,
        error_code="ALREADY_SUBMITTED",
        error_message="Item already submitted",
    )

    entry = journal.get_entry(match_uuid, item_key)
    assert entry is not None
    assert entry["state"] == "ALREADY_SUBMITTED"
    assert entry["last_error_code"] == "ALREADY_SUBMITTED"
    assert entry["last_http_code"] == 409

    # In-flight lock retained: get_in_flight must return entry
    assert journal.get_in_flight(match_uuid, item_key) is not None

    # Blocking record_in_flight: generating new client_submission_id is blocked
    with pytest.raises(ValueError, match="already submitted and locked on server"):
        journal.record_in_flight(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            client_submission_id=str(uuid.uuid4()),
            idempotency_key="new-key",
            request_body_bytes=b'{"new": "body"}',
            git_revision="b" * 40,
        )

    # Downgrade to REJECTED must be blocked!
    with pytest.raises(ValueError, match="Cannot transition.*to REJECTED"):
        journal.record_rejection(
            match_uuid=match_uuid,
            backlog_item_key=item_key,
            http_code=400,
            error_code="BAD_REQUEST",
        )


def test_journal_safe_rejection_on_422_unlocks_for_editing():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-422-REJECT"
    sub_id_1 = str(uuid.uuid4())
    idemp_key_1 = "idemp-422-1"
    body_1 = b'{"answer": "bad"}'

    # Initial in-flight
    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id_1,
        idempotency_key=idemp_key_1,
        request_body_bytes=body_1,
        git_revision="a" * 40,
    )

    # Server rejects with 422 VALIDATION_ERROR
    journal.record_rejection(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        http_code=422,
        error_code="VALIDATION_ERROR",
        error_message="Field missing",
    )

    entry = journal.get_entry(match_uuid, item_key)
    assert entry["state"] == "REJECTED"

    # Verify verify_or_abort allows payload change when state is REJECTED
    body_2 = b'{"answer": "fixed"}'
    assert journal.verify_or_abort(match_uuid, item_key, body_2) is True

    # Safely resubmit with new UUID and fixed payload
    sub_id_2 = str(uuid.uuid4())
    idemp_key_2 = "idemp-422-2"
    sha_2 = journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id_2,
        idempotency_key=idemp_key_2,
        request_body_bytes=body_2,
        git_revision="b" * 40,
    )

    updated = journal.get_entry(match_uuid, item_key)
    assert updated["state"] == "IN_FLIGHT"
    assert updated["client_submission_id"] == sub_id_2
    assert updated["idempotency_key"] == idemp_key_2
    assert updated["request_body_bytes"] == body_2


def test_journal_safe_rejection_on_idempotency_conflict_unlocks_for_editing():
    journal = SubmissionJournal(":memory:")
    match_uuid = str(uuid.uuid4())
    item_key = "TASK-409-IDEMP-CONFLICT"
    sub_id = str(uuid.uuid4())
    idemp_key = "idemp-conflict-1"
    body_1 = b'{"answer": "divergent"}'

    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=sub_id,
        idempotency_key=idemp_key,
        request_body_bytes=body_1,
        git_revision="a" * 40,
    )

    # Server returns 409 IDEMPOTENCY_CONFLICT
    journal.record_rejection(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        http_code=409,
        error_code="IDEMPOTENCY_CONFLICT",
        error_message="Payload differed from original registration",
    )

    entry = journal.get_entry(match_uuid, item_key)
    assert entry["state"] == "REJECTED"

    # Resubmission under new idempotency key and updated payload is unlocked
    body_2 = b'{"answer": "reconciled"}'
    new_sub_id = str(uuid.uuid4())
    new_idemp = "idemp-reconciled-2"
    journal.record_in_flight(
        match_uuid=match_uuid,
        backlog_item_key=item_key,
        client_submission_id=new_sub_id,
        idempotency_key=new_idemp,
        request_body_bytes=body_2,
        git_revision="c" * 40,
    )
    resub = journal.get_entry(match_uuid, item_key)
    assert resub["state"] == "IN_FLIGHT"
    assert resub["client_submission_id"] == new_sub_id
