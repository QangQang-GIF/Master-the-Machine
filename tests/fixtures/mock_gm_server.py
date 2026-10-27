"""
Mock Game Master HTTP Server Fixture.
Uses Python standard library http.server.ThreadingHTTPServer bound to 127.0.0.1:0.
Provides a real TCP socket server simulating the official Digital Cuder Game Master wire contract.
Zero external network calls, zero uninstalled third-party mock dependencies.
"""

from __future__ import annotations

import hashlib
import json
import re
import socket
import threading
import urllib.parse
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

import pytest

UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
IDEMPOTENCY_KEY_REGEX = re.compile(r"^[A-Za-z0-9._~-]{8,128}$")


class MockGameMasterHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler simulating the Digital Cuder Game Master API wire protocol."""

    server: "MockGameMasterHTTPServer"

    def log_message(self, format: str, *args: Any) -> None:
        """Suppress standard stderr request logging during test runs."""
        pass

    def _send_json(
        self,
        status_code: int,
        data: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        payload = json.dumps(data).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        self.send_header("X-Request-Id", req_id)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def _send_error_envelope(
        self,
        status_code: int,
        code: str,
        message: str,
        details: Optional[List[Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        body: Dict[str, Any] = {
            "error": {
                "code": code,
                "message": message,
                "requestId": req_id,
            }
        }
        if details is not None:
            body["error"]["details"] = details
        self._send_json(status_code, body, headers=headers)

    def _send_html(
        self,
        status_code: int,
        html_content: str,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        payload = html_content.encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        req_id = f"req_{uuid.uuid4().hex[:12]}"
        self.send_header("X-Request-Id", req_id)
        if headers:
            for k, v in headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        clean_path = urllib.parse.urlparse(self.path).path.rstrip("/")

        with self.server.lock:
            # 1. GET /match/state
            if clean_path in ("/match/state", "/api/v1/match/state"):
                self._send_json(200, {
                    "status": self.server.match_state,
                    "matchUuid": self.server.match_uuid,
                    "matchCode": self.server.match_code,
                    "server_epoch": 1729000000,
                    "server_calibrated": True,
                })
                return

            # 2. GET /match/<match_code_or_uuid> or /api/v1/matches/<uuid>
            match_view = re.match(r"^/(?:api/v1/matches|match)/([^/]+)$", clean_path)
            if match_view:
                ident = match_view.group(1)
                if ident.lower() in (self.server.match_code.lower(), self.server.match_uuid.lower()):
                    # Session / Bearer authentication check if required
                    if self.server.require_session_auth:
                        cookie_header = self.headers.get("Cookie", "")
                        session_match = re.search(r"session_token=([A-Za-z0-9-]+)", cookie_header)
                        has_valid_cookie = bool(session_match and session_match.group(1) in self.server.active_sessions)
                        auth_header = self.headers.get("Authorization", "")
                        has_valid_bearer = bool(auth_header == f"Bearer {self.server.auth_token}")
                        if not (has_valid_cookie or has_valid_bearer):
                            self._send_error_envelope(401, "UNAUTHORIZED", "Missing or expired session authentication")
                            return

                    # Content negotiation: Next.js spectator HTML vs JSON projection
                    accept_header = self.headers.get("Accept", "")
                    wants_html = ("text/html" in accept_header) or self.server.render_html_mode

                    if wants_html:
                        if getattr(self.server, "malformed_next_data", False):
                            html_body = (
                                f"<!DOCTYPE html><html><head><title>Match {self.server.match_code}</title></head>"
                                f"<body><script id=\"__NEXT_DATA__\">{{malformed json</script></body></html>"
                            )
                        else:
                            page_props = {
                                "team": {
                                    "id": self.server.team_id,
                                    "name": "Team MTM",
                                },
                                "matchUuid": self.server.match_uuid,
                                "matchCode": self.server.match_code,
                                "status": self.server.match_state,
                                "wave": self.server.current_wave,
                                "waveTimestamps": {
                                    "wave1": 0,
                                    "wave2": 840,
                                    "wave3": 1740,
                                },
                                "backlogItems": list(self.server.backlog_items.values()),
                            }
                            next_data = {
                                "props": {
                                    "pageProps": {
                                        **page_props,
                                        "match": page_props,
                                    }
                                },
                                "page": "/match/[matchId]",
                                "query": {"matchId": self.server.match_code},
                                "buildId": "mock-build-id-123",
                            }
                            json_str = json.dumps(next_data, indent=2)
                            html_body = (
                                f"<!DOCTYPE html>\n<html>\n<head><title>Match {self.server.match_code}</title></head>\n"
                                f"<body>\n  <div id=\"__next\">\n    <main>Spectator view for match {self.server.match_code}</main>\n  </div>\n"
                                f"  <script id=\"__NEXT_DATA__\" type=\"application/json\">\n{json_str}\n  </script>\n</body>\n</html>\n"
                            )
                        self._send_html(200, html_body)
                        return

                    # JSON projection
                    self._send_json(200, {
                        "status": self.server.match_state,
                        "matchUuid": self.server.match_uuid,
                        "matchCode": self.server.match_code,
                        "team": {
                            "id": self.server.team_id,
                            "name": "Team MTM",
                        },
                        "wave": self.server.current_wave,
                        "waveTimestamps": {
                            "wave1": 0,
                            "wave2": 840,
                            "wave3": 1740,
                        },
                        "backlogItems": list(self.server.backlog_items.values()),
                    })
                    return

            # 3. GET /backlog
            if clean_path in ("/backlog", "/api/v1/backlog"):
                self._send_json(200, {
                    "tasks": list(self.server.backlog_items.values()),
                })
                return

        self._send_error_envelope(404, "NOT_FOUND", f"Endpoint {self.path} not found")

    def do_POST(self) -> None:
        # Check network timeout injection
        if self.server.check_and_consume_network_timeout():
            try:
                self.request.close()
            except Exception:
                pass
            return

        # Record raw request details
        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length) if content_length > 0 else b""
        req_record = {
            "method": "POST",
            "path": self.path,
            "headers": dict(self.headers),
            "body": raw_body,
        }
        with self.server.lock:
            self.server.requests_log.append(req_record)

        # Check rate limit injection
        rate_limit = self.server.check_and_consume_rate_limit()
        if rate_limit is not None:
            retry_after = rate_limit
            self._send_error_envelope(
                429,
                "RATE_LIMITED",
                "Rate limit exceeded, retry after delay",
                headers={"Retry-After": str(retry_after)},
            )
            return

        # Check validation error injection
        validation_error = self.server.check_and_consume_validation_error()
        if validation_error is not None:
            self._send_error_envelope(
                422,
                "VALIDATION_FAILED",
                "Validation error injected",
                details=validation_error,
            )
            return

        # Check session login route before bearer token check
        clean_path = urllib.parse.urlparse(self.path).path.rstrip("/")
        if clean_path in ("/login", "/api/v1/auth/login"):
            self._handle_login(raw_body)
            return

        # Authentication check
        auth_header = self.headers.get("Authorization", "")
        expected_token = f"Bearer {self.server.auth_token}"
        if not auth_header or auth_header != expected_token:
            self._send_error_envelope(401, "UNAUTHORIZED", "Missing or invalid authorization token")
            return

        # Route matching: POST /api/v1/matches/{MATCH_UUID}/backlogs/start
        start_match = re.match(r"^/api/v1/matches/([^/]+)/backlogs/start$", self.path)
        if start_match:
            match_uuid_param = start_match.group(1)
            self._handle_start_work(match_uuid_param, raw_body)
            return

        # Route matching: POST /api/v1/matches/{MATCH_UUID}/submissions
        submit_match = re.match(r"^/api/v1/matches/([^/]+)/submissions$", self.path)
        if submit_match:
            match_uuid_param = submit_match.group(1)
            self._handle_submission(match_uuid_param, raw_body)
            return

        # Default 404
        self._send_error_envelope(404, "NOT_FOUND", f"Endpoint {self.path} not found")

    def _handle_login(self, raw_body: bytes) -> None:
        username = None
        password = None
        content_type = self.headers.get("Content-Type", "")
        if "application/json" in content_type:
            try:
                body_dict = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                username = body_dict.get("username")
                password = body_dict.get("password")
            except Exception:
                self._send_error_envelope(400, "INVALID_JSON", "Request body is not valid JSON")
                return
        else:
            try:
                qs = urllib.parse.parse_qs(raw_body.decode("utf-8"))
                username = qs.get("username", [None])[0]
                password = qs.get("password", [None])[0]
            except Exception:
                pass
            if username is None:
                try:
                    body_dict = json.loads(raw_body.decode("utf-8")) if raw_body else {}
                    username = body_dict.get("username")
                    password = body_dict.get("password")
                except Exception:
                    pass

        if not username or not password:
            self._send_error_envelope(401, "UNAUTHORIZED", "Missing username or password")
            return

        valid = False
        with self.server.lock:
            creds = self.server.valid_credentials
            if isinstance(creds, dict):
                if "username" in creds and "password" in creds:
                    valid = (username == creds["username"] and password == creds["password"])
                elif creds.get(username) == password:
                    valid = True
                elif username == "example-account" and password == "fake-password-for-example":
                    valid = True
                elif username == "user" and password == "pass123":
                    valid = True

        if not valid:
            self._send_error_envelope(401, "UNAUTHORIZED", "Invalid username or password")
            return

        token = str(uuid.uuid4())
        with self.server.lock:
            self.server.active_sessions.add(token)

        cookie_hdr = f"session_token={token}; Path=/; HttpOnly; SameSite=Lax"
        self._send_json(
            200,
            {
                "status": "AUTHENTICATED",
                "sessionToken": token,
                "username": username,
            },
            headers={"Set-Cookie": cookie_hdr},
        )

    def _handle_start_work(self, match_uuid: str, raw_body: bytes) -> None:
        if not UUID_REGEX.match(match_uuid):
            self._send_error_envelope(404, "INVALID_MATCH_UUID", "Match identifier must be a valid 36-char UUID")
            return

        if match_uuid.lower() != self.server.match_uuid.lower():
            self._send_error_envelope(404, "MATCH_NOT_FOUND", "Match not found")
            return

        try:
            body_dict = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            self._send_error_envelope(400, "INVALID_JSON", "Request body is not valid JSON")
            return

        backlog_item_key = body_dict.get("backlogItemKey", "").strip()
        if not backlog_item_key:
            self._send_error_envelope(422, "VALIDATION_FAILED", "Missing backlogItemKey")
            return

        with self.server.lock:
            # Check match state (e.g. FINISHED or PREPARING rejection)
            if self.server.match_state == "FINISHED":
                self._send_error_envelope(409, "MATCH_NOT_ACCEPTING_SUBMISSIONS", "Match is finished")
                return
            if self.server.match_state == "PREPARING":
                self._send_error_envelope(409, "MATCH_NOT_IN_PROGRESS", "Match is still preparing")
                return

            if backlog_item_key in self.server.closed_items:
                self._send_error_envelope(409, "ITEM_CLOSED", f"Item {backlog_item_key} is already closed")
                return

            already_started = backlog_item_key in self.server.started_items

            # Return MVS and submitTemplate
            submit_template = {
                "codeChanges": True,
                "summary": f"Solution for {backlog_item_key}",
                "evidence": ["evidence/test_run.log"],
            }
            mvs = [
                {
                    "name": "smoke",
                    "command": "pytest -k smoke",
                    "expected": "PASS",
                }
            ]
            files = ["src/app.py"]

            if already_started:
                # Replay returns 200 OK
                data = {
                    "backlogId": backlog_item_key,
                    "backlogType": "feature",
                    "startInstruction": f"Instructions for {backlog_item_key}",
                    "submitTemplate": submit_template,
                    "minimumVerificationScenarios": mvs,
                    "files": files,
                    "replay": True,
                }
                self._send_json(200, data)
            else:
                # First start returns 201 Created
                self.server.started_items[backlog_item_key] = {
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "submit_template": submit_template,
                    "mvs": mvs,
                    "files": files,
                }
                data = {
                    "backlogId": backlog_item_key,
                    "backlogType": "feature",
                    "startInstruction": f"Instructions for {backlog_item_key}",
                    "submitTemplate": submit_template,
                    "minimumVerificationScenarios": mvs,
                    "files": files,
                    "replay": False,
                }
                self._send_json(201, data)

    def _handle_submission(self, match_uuid: str, raw_body: bytes) -> None:
        if getattr(self.server, "force_submission_error", None):
            code, err_code, msg = self.server.force_submission_error
            self._send_error_envelope(code, err_code, msg)
            return

        if not UUID_REGEX.match(match_uuid):
            self._send_error_envelope(404, "INVALID_MATCH_UUID", "Match identifier must be a valid 36-char UUID")
            return

        if match_uuid.lower() != self.server.match_uuid.lower():
            self._send_error_envelope(404, "MATCH_NOT_FOUND", "Match not found")
            return

        idempotency_key = self.headers.get("Idempotency-Key", "").strip()
        if not idempotency_key or not IDEMPOTENCY_KEY_REGEX.match(idempotency_key):
            self._send_error_envelope(422, "VALIDATION_FAILED", "Missing or invalid Idempotency-Key header")
            return

        if len(raw_body) > 524288:
            self._send_error_envelope(413, "PAYLOAD_TOO_LARGE", "Submission payload exceeds 512KB limit")
            return

        try:
            body_dict = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            self._send_error_envelope(400, "INVALID_JSON", "Request body is not valid JSON")
            return

        backlog_item_key = body_dict.get("backlogItemKey", "").strip()
        client_submission_id = body_dict.get("clientSubmissionId", "").strip()
        git_revision = body_dict.get("gitRevision", "").strip()
        answer = body_dict.get("answer")

        if not backlog_item_key or not client_submission_id or not git_revision or answer is None:
            self._send_error_envelope(422, "VALIDATION_FAILED", "Missing required submission fields (backlogItemKey, clientSubmissionId, gitRevision, answer)")
            return

        # Check match state (pause submit check)
        with self.server.lock:
            if self.server.match_state == "PAUSED" and not self.server.allow_submits_during_pause:
                self._send_error_envelope(409, "MATCH_NOT_ACCEPTING_SUBMISSIONS", "Match is currently paused")
                return

            body_sha256 = hashlib.sha256(raw_body).hexdigest()

            # Idempotency check:
            if idempotency_key in self.server.idempotency_store:
                saved_sha256, saved_response, saved_status = self.server.idempotency_store[idempotency_key]
                if saved_sha256 == body_sha256:
                    # Deterministic replay! Returns saved response with header Idempotent-Replay: true
                    self._send_json(200, saved_response, headers={"Idempotent-Replay": "true"})
                    return
                else:
                    # Same key with differing body payload -> 409 IDEMPOTENCY_CONFLICT
                    self._send_error_envelope(409, "IDEMPOTENCY_CONFLICT", "Payload mismatch for idempotency key")
                    return

            # Key is new: Check if backlog item was already accepted under another key
            if backlog_item_key in self.server.accepted_items:
                self._send_error_envelope(409, "ALREADY_SUBMITTED", f"Backlog item {backlog_item_key} has already been accepted")
                return

            # Check if backlog item was started
            if backlog_item_key not in self.server.started_items:
                self._send_error_envelope(409, "BACKLOG_NOT_STARTED", f"Backlog item {backlog_item_key} was not started before submit")
                return

            # Success: Accept submission
            sub_id = f"sub_{uuid.uuid4().hex[:12]}"
            response_data = {
                "submission": {
                    "id": sub_id,
                    "matchId": match_uuid,
                    "teamId": "team-mtm",
                    "backlogItemKey": backlog_item_key,
                    "backlogRevisionNo": 1,
                    "attemptNo": 1,
                    "receivedAt": datetime.now(timezone.utc).isoformat(),
                    "status": "ACCEPTED",
                    "scoreStatus": "PENDING",
                }
            }

            self.server.idempotency_store[idempotency_key] = (body_sha256, response_data, 201)
            self.server.accepted_items.add(backlog_item_key)
            self.server.submissions[client_submission_id] = response_data

            self._send_json(201, response_data)


class MockGameMasterHTTPServer(ThreadingHTTPServer):
    """Threading HTTP Server with mock state and fault injection capabilities."""

    def __init__(self, server_address: Tuple[str, int], RequestHandlerClass: Any):
        super().__init__(server_address, RequestHandlerClass)
        self.lock = threading.Lock()
        self.match_uuid: str = "8c0e1b22-1111-4222-8333-444444444444"
        self.match_code: str = "KWO5GPMP"
        self.team_id: str = "T01"
        self.auth_token: str = "secret-token-123"
        self.match_state: str = "IN_PROGRESS"
        self.current_wave: int = 1
        self.allow_submits_during_pause: bool = False
        self.require_session_auth: bool = False
        self.render_html_mode: bool = False
        self.malformed_next_data: bool = False

        self.active_sessions: set[str] = set()
        self.valid_credentials: Dict[str, str] = {
            "example-account": "fake-password-for-example",
            "player-1": "player-pass-123",
            "user": "pass123",
            "admin": "admin123",
        }
        self.force_submission_error: Optional[Tuple[int, str, str]] = None

        self.started_items: Dict[str, Dict[str, Any]] = {}
        self.accepted_items: set[str] = set()
        self.closed_items: set[str] = set()
        self.backlog_items: Dict[str, Dict[str, Any]] = {}
        self.submissions: Dict[str, Dict[str, Any]] = {}
        self.idempotency_store: Dict[str, Tuple[str, Dict[str, Any], int]] = {}
        self.requests_log: List[Dict[str, Any]] = []

        # Fault injections
        self._rate_limit_count: int = 0
        self._rate_limit_retry_after: int = 1
        self._network_timeout_triggered: bool = False
        self._validation_error_details: Optional[List[Any]] = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_port}"

    def reset(self) -> None:
        """Reset state between test cases."""
        with self.lock:
            self.started_items.clear()
            self.accepted_items.clear()
            self.closed_items.clear()
            self.backlog_items.clear()
            self.submissions.clear()
            self.idempotency_store.clear()
            self.requests_log.clear()
            self.match_state = "IN_PROGRESS"
            self.allow_submits_during_pause = False
            self.team_id = "T01"
            self.current_wave = 1
            self.require_session_auth = False
            self.render_html_mode = False
            self.malformed_next_data = False
            self.active_sessions.clear()
            self.force_submission_error = None
            self.valid_credentials = {
                "example-account": "fake-password-for-example",
                "player-1": "player-pass-123",
                "user": "pass123",
                "admin": "admin123",
            }
            self._rate_limit_count = 0
            self._rate_limit_retry_after = 1
            self._network_timeout_triggered = False
            self._validation_error_details = None

    def set_wave(self, wave_number: int) -> None:
        with self.lock:
            self.current_wave = wave_number

    def add_wave_items(self, wave_number: int, items: List[Dict[str, Any]]) -> None:
        with self.lock:
            self.current_wave = wave_number
            for item in items:
                k = item.get("id") or item.get("key") or item.get("backlogItemKey")
                if k:
                    self.backlog_items[str(k)] = item

    def simulate_wave_2(self) -> None:
        """Populate standard Wave 2 backlog items."""
        wave_2_items = [
            {
                "id": "TASK-WAVE2-01",
                "key": "TASK-WAVE2-01",
                "name": "Wave 2 Feature 1",
                "title": "Wave 2 Feature 1",
                "wave": 2,
                "status": "OPEN",
                "points": 100,
            },
            {
                "id": "TASK-WAVE2-02",
                "key": "TASK-WAVE2-02",
                "name": "Wave 2 Feature 2",
                "title": "Wave 2 Feature 2",
                "wave": 2,
                "status": "OPEN",
                "points": 150,
            },
        ]
        self.add_wave_items(2, wave_2_items)

    def simulate_wave_3(self) -> None:
        """Populate standard Wave 3 backlog items."""
        wave_3_items = [
            {
                "id": "TASK-WAVE3-01",
                "key": "TASK-WAVE3-01",
                "name": "Wave 3 Feature 1",
                "title": "Wave 3 Feature 1",
                "wave": 3,
                "status": "OPEN",
                "points": 200,
            },
        ]
        self.add_wave_items(3, wave_3_items)

    def inject_rate_limit(self, count: int = 1, retry_after: int = 1) -> None:
        with self.lock:
            self._rate_limit_count = count
            self._rate_limit_retry_after = retry_after

    def check_and_consume_rate_limit(self) -> Optional[int]:
        with self.lock:
            if self._rate_limit_count > 0:
                self._rate_limit_count -= 1
                return self._rate_limit_retry_after
            return None

    def inject_network_timeout(self) -> None:
        with self.lock:
            self._network_timeout_triggered = True

    def check_and_consume_network_timeout(self) -> bool:
        with self.lock:
            if self._network_timeout_triggered:
                self._network_timeout_triggered = False
                return True
            return False

    def inject_validation_error(self, details: Optional[List[Any]] = None) -> None:
        with self.lock:
            self._validation_error_details = details if details is not None else []

    def check_and_consume_validation_error(self) -> Optional[List[Any]]:
        with self.lock:
            if self._validation_error_details is not None:
                details = self._validation_error_details
                self._validation_error_details = None
                return details
            return None


@pytest.fixture
def mock_gm_server():
    """Pytest fixture running the MockGameMasterHTTPServer on an ephemeral port."""
    server = MockGameMasterHTTPServer(("127.0.0.1", 0), MockGameMasterHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2.0)
