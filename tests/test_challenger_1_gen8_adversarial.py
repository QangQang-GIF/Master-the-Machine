"""
tests/test_challenger_1_gen8_adversarial.py: Adversarial Stress Test Suite for Gen 8.

Empirical verification of requirements R1 and R2:
- R1: State Continuity Stress Testing:
  * Repeated configure_session calls: PREPARE_GAME -> START_GAME -> START_GAME -> START_GAME.
  * Verifies cookies, branch, uuid, and repo descriptor remain intact throughout.
  * Verifies sparse / empty START_GAME descriptors do not clobber PREPARE state.
  * Verifies custom git branches persist across repeated START_GAME calls.
  * Verifies adapter instance reuse and apiKey updates without losing session cookies.
  * Verifies end-to-end work submission succeeds after repeated START_GAME calls.
- R2: Match Page URL Edge Cases & Validation:
  * Comprehensive URL edge case matrix for build_match_page_url (trailing slashes, port, subpaths, queries, case variations).
  * Real HTTP verification of login_session with diverse URL formats against Mock Game Master.
  * Match assignment validation: fails closed with GameMasterValidationError (422) on mismatched matchCode/matchUuid.
  * MCPHelperServer fails closed during PREPARE_GAME when unassigned match projection is returned.
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

# Ensure repository root, src, and tests are on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"
SRC_DIR = ROOT_DIR / "src"
for p in [str(ROOT_DIR), str(TESTS_DIR), str(SRC_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from mtm_competition.adapters.official_game_master import (
    GameMasterAuthError,
    GameMasterError,
    GameMasterSecurityError,
    GameMasterValidationError,
    OfficialGameMasterAdapter,
    build_match_page_url,
    validate_match_projection_schema,
)
from runner.mcp_server import MCPHelperServer
from mtm_competition.tasks.journal import SubmissionJournal
try:
    from tests.fixtures.telemetry_validator import validate_progress_file
except ModuleNotFoundError:
    from fixtures.telemetry_validator import validate_progress_file

try:
    from tests.fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from tests.fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server
except ModuleNotFoundError:
    from fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server


# ==============================================================================
# Helper to set up git repository with doctor script
# ==============================================================================

def setup_repo_with_doctor(checkout_dir: Path, match_code: str, branch: str = "main") -> str:
    match_folder = checkout_dir / match_code
    match_folder.mkdir(parents=True, exist_ok=True)
    (match_folder / "app.py").write_text("print('test harness app')\n", encoding="utf-8")

    env_req = match_folder / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text(
            'Write-Output "Environment status: READY"\nexit 0\n',
            encoding="utf-8",
        )
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text(
            '#!/bin/sh\necho "Environment status: READY"\nexit 0\n',
            encoding="utf-8",
        )
        try:
            doc_sh.chmod(0o755)
        except Exception:
            pass

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", f"setup repo for {match_code} on {branch}"])
    run_git(checkout_dir, ["push", "origin", branch])
    return run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()


# ==============================================================================
# 1. State Continuity Stress Testing (R1)
# ==============================================================================

class TestStateContinuityStress:
    """Empirical verification of R1 state continuity across multiple configure_session invocations."""

    def test_repeated_start_game_calls_preserves_state_and_enables_submit(
        self, offline_git_env, mock_gm_server, tmp_path
    ):
        """PREPARE_GAME -> START_GAME -> START_GAME -> START_GAME retains all state and completes submit_work."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = mock_gm_server.match_code
        repo_url = offline_git_env["remote_url"]
        offline_git_env["set_remote_url"](repo_url)

        prep_head_sha = setup_repo_with_doctor(checkout_dir, match_code, "main")

        team_ws = tmp_path / "team_ws"
        team_ws.mkdir(parents=True, exist_ok=True)
        log_file = team_ws / "team-sdlc-pack" / "logs" / f"{match_code}.progress.ndjson"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        journal_db = team_ws / ".mtm" / "submissions.db"
        session_id = f"session-stress-{uuid.uuid4().hex[:8]}"

        server = MCPHelperServer(workspace_root=tmp_path / "initial_root")

        # 1. PREPARE_GAME
        prepare_desc = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "requestId": "req-prep-001",
            "playerSessionId": session_id,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "matchRoot": str(checkout_dir / match_code),
            "username": "example-account",
            "password": "fake-password-for-example",
            "matchPageUrl": f"{mock_gm_server.base_url}/match",
            "allowedMatchOrigins": [mock_gm_server.base_url],
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        res_prep = server.configure_session(prepare_desc)
        assert res_prep["status"] == "CONFIGURED"
        assert server.assigned_branch == "main"
        assert server.repo_descriptor.get("branch") == "main"
        assert server.match_uuid == mock_gm_server.match_uuid
        assert server.adapter is not None
        assert len(server.adapter.session_cookies) > 0

        orig_adapter = server.adapter
        saved_cookies = dict(server.adapter.session_cookies)

        # 2. START_GAME #1
        start_desc_1 = {
            "type": "START_GAME",
            "matchId": match_code,
            "requestId": "req-start-001",
            "playerSessionId": session_id,
            "gameMasterApiUrl": mock_gm_server.base_url,
            "apiKeySecret": "token-generation-1",
            "teamWorkspace": str(team_ws),
            "teamSdlcPackWorkspace": str(team_ws / "team-sdlc-pack"),
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "matchRoot": str(checkout_dir / match_code),
            "matchPageUrl": f"{mock_gm_server.base_url}/match",
            "allowedApiOrigins": [mock_gm_server.base_url],
            "journalDbPath": str(journal_db),
        }
        res_start_1 = server.configure_session(start_desc_1)
        assert res_start_1["status"] == "CONFIGURED"

        # Assert state after START 1
        assert server.assigned_branch == "main"
        assert server.repo_descriptor.get("branch") == "main"
        assert server.match_uuid == mock_gm_server.match_uuid
        assert server.adapter is orig_adapter
        assert server.adapter.session_cookies == saved_cookies
        assert server.adapter.api_key == "token-generation-1"
        assert server.workspace_root == team_ws.resolve()

        # 3. START_GAME #2 (repeated call with new apiKeySecret, no repository, no credentials)
        start_desc_2 = {
            "type": "START_GAME",
            "matchId": match_code,
            "requestId": "req-start-002",
            "playerSessionId": f"{session_id}-updated",
            "apiKeySecret": mock_gm_server.auth_token,  # Genuine token for submit
        }
        res_start_2 = server.configure_session(start_desc_2)
        assert res_start_2["status"] == "CONFIGURED"

        # Assert state after START 2
        assert server.assigned_branch == "main", "Branch must persist through repeated START_GAME"
        assert server.repo_descriptor.get("branch") == "main", "repo_descriptor must persist"
        assert server.match_uuid == mock_gm_server.match_uuid, "match_uuid must persist"
        assert server.adapter is orig_adapter, "Adapter instance must be reused"
        assert server.adapter.session_cookies == saved_cookies, "Session cookies must survive"
        assert server.adapter.api_key == mock_gm_server.auth_token, "Bearer token must be updated"
        assert server.player_session_id == f"{session_id}-updated"

        # 4. START_GAME #3 (sparse call: empty dict beyond type)
        start_desc_3 = {
            "type": "START_GAME",
        }
        res_start_3 = server.configure_session(start_desc_3)
        assert res_start_3["status"] == "CONFIGURED"

        # Assert state after START 3
        assert server.assigned_branch == "main"
        assert server.repo_descriptor.get("branch") == "main"
        assert server.match_uuid == mock_gm_server.match_uuid
        assert server.adapter is orig_adapter
        assert server.adapter.session_cookies == saved_cookies
        assert server.adapter.api_key == mock_gm_server.auth_token

        # 5. Execute lifecycle after 3x START_GAME calls
        task_key = "TASK-STRESS-CONTINUITY"
        mock_gm_server.backlog_items[task_key] = {
            "id": task_key,
            "key": task_key,
            "name": "Stress Continuity Task",
            "title": "Verify submission after repeated start calls",
            "status": "OPEN",
            "wave": 1,
            "points": 50,
        }

        refresh_res = server.refresh_match()
        assert task_key in refresh_res["newlyDiscovered"]

        start_work_res = server.start_work(task_key)
        assert start_work_res["backlogId"] == task_key

        # Prepare evidence, commit, push, submit
        match_folder = checkout_dir / match_code
        evidence_dir = match_folder / "answers" / task_key / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "stress.log").write_text("stress test passed\n", encoding="utf-8")

        answer_payload = {
            "solution": "Adversarial continuity verified",
            "evidence": ["evidence/stress.log"],
        }
        (match_folder / "answers" / task_key / "submit.json").write_text(
            json.dumps(answer_payload, indent=2), encoding="utf-8"
        )
        (match_folder / "app.py").write_text("print('stress verified')\n", encoding="utf-8")

        run_git(checkout_dir, ["add", "-A"])
        run_git(checkout_dir, ["commit", "-m", f"solve {task_key}"])
        run_git(checkout_dir, ["push", "origin", "main"])
        sub_head_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

        sub_res = server.submit_work(
            client_submission_id=str(uuid.uuid4()),
            backlog_item_key=task_key,
            git_revision=sub_head_sha,
            answer=answer_payload,
        )
        assert "receiptId" in sub_res or ("submission" in sub_res and "id" in sub_res["submission"])

        with SubmissionJournal(db_path=journal_db) as j:
            entry = j.get_entry(mock_gm_server.match_uuid, task_key)
            assert entry is not None
            assert entry["state"] == "ACCEPTED"
            assert entry["last_http_code"] == 201

    def test_state_continuity_preserves_custom_git_branch(
        self, offline_git_env, mock_gm_server, tmp_path
    ):
        """Custom git branch ref (e.g. 'feature/adversarial-continuity') persists through repeated START calls."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = mock_gm_server.match_code
        repo_url = offline_git_env["remote_url"]
        custom_branch = "feature/adversarial-continuity"

        run_git(checkout_dir, ["checkout", "-b", custom_branch])
        setup_repo_with_doctor(checkout_dir, match_code, custom_branch)

        team_ws = tmp_path / "team_ws"
        team_ws.mkdir(parents=True, exist_ok=True)
        log_file = team_ws / f"{match_code}.ndjson"

        server = MCPHelperServer(workspace_root=tmp_path)

        prepare_desc = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "matchRoot": str(checkout_dir / match_code),
            "username": "example-account",
            "password": "fake-password-for-example",
            "matchPageUrl": f"{mock_gm_server.base_url}/match",
            "allowedMatchOrigins": [mock_gm_server.base_url],
            "repository": {
                "url": repo_url,
                "branch": custom_branch,
                "folder": match_code,
            },
        }

        server.configure_session(prepare_desc)
        assert server.assigned_branch == custom_branch
        assert server.repo_descriptor.get("branch") == custom_branch

        # First START_GAME without branch
        server.configure_session({
            "type": "START_GAME",
            "matchId": match_code,
            "apiKeySecret": mock_gm_server.auth_token,
            "teamWorkspace": str(team_ws),
        })
        assert server.assigned_branch == custom_branch
        assert server.repo_descriptor.get("branch") == custom_branch

        # Second START_GAME with empty branch override
        server.configure_session({
            "type": "START_GAME",
            "matchId": match_code,
        })
        assert server.assigned_branch == custom_branch
        assert server.repo_descriptor.get("branch") == custom_branch

    def test_state_continuity_adapter_cookies_isolation(self, mock_gm_server, tmp_path):
        """Verifies session cookies are not overwritten or cleared by START_GAME calls."""
        server = MCPHelperServer(workspace_root=tmp_path)
        server.match_code = mock_gm_server.match_code
        server.match_uuid = mock_gm_server.match_uuid

        # Pre-seed session cookies
        seeded_cookies = {
            "session_token": "token-xyz-12345",
            "csrf_token": "csrf-secret-98765",
            "tracking_id": "trk-0001",
        }
        server.session_cookies = dict(seeded_cookies)

        # First START_GAME call
        server.configure_session({
            "type": "START_GAME",
            "matchId": mock_gm_server.match_code,
            "gameMasterApiUrl": mock_gm_server.base_url,
            "apiKeySecret": "bearer-key-1",
        })

        assert server.adapter is not None
        for k, v in seeded_cookies.items():
            assert server.adapter.session_cookies.get(k) == v

        # Multiple successive configure_session calls
        for i in range(5):
            server.configure_session({
                "type": "START_GAME",
                "matchId": mock_gm_server.match_code,
                "apiKeySecret": f"bearer-key-{i+2}",
            })
            for k, v in seeded_cookies.items():
                assert server.adapter.session_cookies.get(k) == v
            assert server.adapter.api_key == f"bearer-key-{i+2}"


# ==============================================================================
# 2. Match Page URL Edge Cases (R2)
# ==============================================================================

class TestMatchPageUrlEdgeCases:
    """Adversarial testing of build_match_page_url and login_session targeting."""

    @pytest.mark.parametrize(
        "input_url, match_id, expected_url",
        [
            # Trailing slashes
            ("https://example.com/match/", "MATCH01", "https://example.com/match/MATCH01"),
            ("https://example.com/match///", "MATCH01", "https://example.com/match/MATCH01"),
            ("http://localhost:3000/match/", "MATCH01", "http://localhost:3000/match/MATCH01"),

            # Already containing match code (exact and case variants)
            ("https://example.com/match/MATCH01", "MATCH01", "https://example.com/match/MATCH01"),
            ("https://example.com/match/match01", "MATCH01", "https://example.com/match/match01"),
            ("https://example.com/match/MATCH01/", "MATCH01", "https://example.com/match/MATCH01/"),

            # Base URL with port
            ("http://localhost:8080/match", "MATCH01", "http://localhost:8080/match/MATCH01"),
            ("http://localhost:8080/match/", "MATCH01", "http://localhost:8080/match/MATCH01"),
            ("http://localhost:8080", "MATCH01", "http://localhost:8080/match/MATCH01"),
            ("http://localhost:8080/", "MATCH01", "http://localhost:8080/match/MATCH01"),
            ("http://127.0.0.1:9090", "MATCH01", "http://127.0.0.1:9090/match/MATCH01"),

            # Subpath URL
            ("https://example.com/tournaments/t1/match", "MATCH01", "https://example.com/tournaments/t1/match/MATCH01"),
            ("https://example.com/tournaments/t1/match/", "MATCH01", "https://example.com/tournaments/t1/match/MATCH01"),
            ("https://example.com/tournaments/t1", "MATCH01", "https://example.com/tournaments/t1/match/MATCH01"),
            ("https://example.com/tournaments/t1/match/MATCH01", "MATCH01", "https://example.com/tournaments/t1/match/MATCH01"),

            # Query strings and fragments
            ("https://example.com/match?view=grid", "MATCH01", "https://example.com/match/MATCH01?view=grid"),
            ("https://example.com/match/?view=grid", "MATCH01", "https://example.com/match/MATCH01?view=grid"),
            ("https://example.com/match/MATCH01?tab=info", "MATCH01", "https://example.com/match/MATCH01?tab=info"),

            # Empty or relative inputs
            ("/match", "MATCH01", "/match/MATCH01"),
            ("/match/", "MATCH01", "/match/MATCH01"),
            ("", "MATCH01", "/match/MATCH01"),
            ("https://example.com/match", "", "https://example.com/match"),
        ],
    )
    def test_build_match_page_url_matrix(self, input_url: str, match_id: str, expected_url: str):
        """Verifies build_match_page_url produces correct target URL across edge cases."""
        result = build_match_page_url(input_url, match_id)
        assert result == expected_url

    def test_login_session_live_edge_case_urls(self, mock_gm_server):
        """login_session succeeds with diverse URL formats against live Mock Game Master."""
        adapter = OfficialGameMasterAdapter(base_url=mock_gm_server.base_url)
        match_code = mock_gm_server.match_code

        test_urls = [
            f"{mock_gm_server.base_url}/match",
            f"{mock_gm_server.base_url}/match/",
            f"{mock_gm_server.base_url}/match/{match_code}",
            f"{mock_gm_server.base_url}",
        ]

        for url in test_urls:
            res = adapter.login_session(
                username="example-account",
                password="fake-password-for-example",
                match_page_url=url,
                allowed_origins=[mock_gm_server.base_url],
                match_id=match_code,
            )
            assert res["status"] == "AUTHENTICATED"
            assert "session_token" in res or "session_token" in adapter.session_cookies
            proj = res.get("projection") or {}
            assert proj.get("matchCode") == match_code

    def test_login_session_rejects_mismatched_match_code(self, mock_gm_server, monkeypatch):
        """R2: login_session raises GameMasterValidationError(422) if projection returns wrong matchCode."""
        adapter = OfficialGameMasterAdapter(base_url=mock_gm_server.base_url)

        real_req = adapter._request

        def mock_request(method, url, *args, **kwargs):
            if method == "GET":
                return {
                    "matchCode": "MISMATCHED_CODE_99",
                    "matchUuid": "00000000-0000-4000-8000-000000000000",
                    "status": "IN_PROGRESS",
                    "backlogItems": [],
                }
            return real_req(method, url, *args, **kwargs)

        monkeypatch.setattr(adapter, "_request", mock_request)

        with pytest.raises(GameMasterValidationError) as exc_info:
            adapter.login_session(
                username="example-account",
                password="fake-password-for-example",
                match_page_url=f"{mock_gm_server.base_url}/match",
                allowed_origins=[mock_gm_server.base_url],
                match_id="MY_ASSIGNED_MATCH",
            )
        assert exc_info.value.status_code == 422
        assert "Match assignment verification failed" in str(exc_info.value)

    def test_login_session_accepts_case_insensitive_match_id(self, mock_gm_server, monkeypatch):
        """login_session accepts case variations (e.g. lowercase match_id vs uppercase matchCode)."""
        adapter = OfficialGameMasterAdapter(base_url=mock_gm_server.base_url)

        real_req = adapter._request

        def mock_request(method, url, *args, **kwargs):
            if method == "GET":
                return {
                    "matchCode": "MATCH01",
                    "matchUuid": "11111111-1111-4111-8111-111111111111",
                    "status": "IN_PROGRESS",
                    "backlogItems": [],
                }
            return real_req(method, url, *args, **kwargs)

        monkeypatch.setattr(adapter, "_request", mock_request)

        # Lowercase match_id "match01" should match "MATCH01"
        res = adapter.login_session(
            username="example-account",
            password="fake-password-for-example",
            match_page_url=f"{mock_gm_server.base_url}/match",
            allowed_origins=[mock_gm_server.base_url],
            match_id="match01",
        )
        assert res["status"] == "AUTHENTICATED"

    def test_login_session_accepts_uuid_match(self, mock_gm_server, monkeypatch):
        """login_session accepts match when match_id matches matchUuid."""
        adapter = OfficialGameMasterAdapter(base_url=mock_gm_server.base_url)
        uuid_str = "abcdef12-3456-4789-abcd-ef0123456789"

        real_req = adapter._request

        def mock_request(method, url, *args, **kwargs):
            if method == "GET":
                return {
                    "matchCode": "SOME_CODE",
                    "matchUuid": uuid_str,
                    "status": "IN_PROGRESS",
                    "backlogItems": [],
                }
            return real_req(method, url, *args, **kwargs)

        monkeypatch.setattr(adapter, "_request", mock_request)

        res = adapter.login_session(
            username="example-account",
            password="fake-password-for-example",
            match_page_url=f"{mock_gm_server.base_url}/match",
            allowed_origins=[mock_gm_server.base_url],
            match_id=uuid_str.upper(),
        )
        assert res["status"] == "AUTHENTICATED"

    def test_configure_session_fails_closed_when_match_projection_mismatched(
        self, offline_git_env, mock_gm_server, tmp_path, monkeypatch
    ):
        """configure_session (PREPARE_GAME) fails closed, emits MATCH_LOGIN FAILED, and never emits ENVIRONMENT_READY."""
        checkout_dir = offline_git_env["checkout_dir"]
        match_code = "EXPECTED_CODE"
        repo_url = offline_git_env["remote_url"]

        setup_repo_with_doctor(checkout_dir, match_code, "main")

        log_file = tmp_path / "fail_login.ndjson"
        server = MCPHelperServer(workspace_root=tmp_path)

        # Patch adapter login_session to simulate mismatched projection returning wrong code
        def mock_login_session(*args, **kwargs):
            raise GameMasterValidationError(
                422,
                "Match assignment verification failed: projection returned matchCode='WRONG_CODE' (uuid='wrong'), expected 'EXPECTED_CODE'."
            )

        prepare_desc = {
            "type": "PREPARE_GAME",
            "matchId": match_code,
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "matchRoot": str(checkout_dir / match_code),
            "username": "example-account",
            "password": "fake-password-for-example",
            "matchPageUrl": f"{mock_gm_server.base_url}/match",
            "allowedMatchOrigins": [mock_gm_server.base_url],
            "repository": {
                "url": repo_url,
                "branch": "main",
                "folder": match_code,
            },
        }

        # Initialize adapter on server then patch
        server.adapter = OfficialGameMasterAdapter(base_url=mock_gm_server.base_url)
        monkeypatch.setattr(server.adapter, "login_session", mock_login_session)

        with pytest.raises(RuntimeError) as exc_info:
            server.configure_session(prepare_desc)

        assert "Match session login verification failed" in str(exc_info.value)

        # Inspect NDJSON telemetry events
        events = validate_progress_file(log_file, expected_session_id=server.player_session_id)
        types = [e.get("type") for e in events]
        assert "ENVIRONMENT_READY" not in types, "ENVIRONMENT_READY must NEVER be emitted when login fails"

        match_login_events = [
            e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "MATCH_LOGIN"
        ]
        assert len(match_login_events) > 0
        assert match_login_events[-1].get("status") == "FAILED"
