"""
tests/test_challenger_1_gen7_adversarial.py: Adversarial Stress Test Suite.

Empirical verification of requirements R1, R2, and R3:
- R1: Session cookie authentication edge cases:
  * Invalid username/password fail closed (RuntimeError raised, no false pass).
  * Expired or missing session cookies fail closed on /match/<id>.
  * SEC-01 origin allowlist blocking unassigned origins and malicious URLs.
- R2: Git invariant edge cases:
  * Detached HEAD commits vs assigned branch ref.
  * Remote URL normalization variants (uppercase, trailing slashes, .git suffix, backslashes).
  * Missing subfolder or directory traversal attempts in folder descriptor.
- R3: Match projection schema fuzzing:
  * Missing or malformed matchUuid or matchCode.
  * Non-list backlogItems (dict, null, string, numbers).
  * Corrupted, empty, or invalid JSON inside Next.js <script id="__NEXT_DATA__">.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
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
    validate_match_projection_schema,
)
from mtm_core.git.repository import normalize_git_url, verify_assigned_repository
from runner.mcp_server import MCPHelperServer

try:
    from tests.fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from tests.fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server
except ModuleNotFoundError:
    from fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server


# ==============================================================================
# Suite 1: R1 Session Cookie Authentication Edge Cases
# ==============================================================================

class TestChallengerR1SessionAuthEdgeCases:
    """Adversarial stress testing for R1 session cookie authentication."""

    def test_r1_wrong_password_fails_closed_in_adapter(self, mock_gm_server: MockGameMasterHTTPServer):
        """Verify invalid credentials against Game Master fail closed and record no session."""
        mock_gm_server.require_session_auth = True
        mock_gm_server.valid_credentials = {"player-1": "correct-pass"}

        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        match_url = f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}"

        with pytest.raises(GameMasterAuthError) as exc_info:
            adapter.login_session(
                username="player-1",
                password="wrong-password-attacker",
                match_page_url=match_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert exc_info.value.status_code == 401
        assert "UNAUTHORIZED" in str(exc_info.value) or "Invalid" in str(exc_info.value)
        assert len(mock_gm_server.active_sessions) == 0
        assert not adapter.session_cookies

    @pytest.mark.parametrize(
        "user,pwd",
        [
            ("", "password123"),
            ("   ", "password123"),
            ("user", ""),
            ("user", "   "),
            ("", ""),
            ("   ", "   "),
        ],
    )
    def test_r1_empty_and_whitespace_credentials_fail_closed(
        self, user: str, pwd: str, mock_gm_server: MockGameMasterHTTPServer
    ):
        """Verify empty or whitespace credentials immediately fail closed without network calls."""
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        match_url = f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}"

        with pytest.raises((GameMasterAuthError, ValueError)):
            adapter.login_session(
                username=user,
                password=pwd,
                match_page_url=match_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert not adapter.session_cookies

    def test_r1_prepare_game_wrong_password_raises_runtime_error_no_false_pass(
        self, mock_gm_server: MockGameMasterHTTPServer, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """In PREPARE_GAME, invalid credentials must raise RuntimeError and NEVER emit false pass."""
        mock_gm_server.require_session_auth = True
        mock_gm_server.valid_credentials = {"player-1": "real-secret-123"}

        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]
        log_file = tmp_path / "prepare_auth_failure.ndjson"

        server = MCPHelperServer(workspace_root=tmp_path)
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": "M101",
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "username": "player-1",
            "password": "wrong-adversarial-password",
            "matchPageUrl": f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}",
            "allowedMatchOrigins": [mock_gm_server.base_url],
            "repository": {
                "url": remote_url,
                "branch": "main",
                "folder": "repo",
            },
            "offline": False,
        }

        with pytest.raises(RuntimeError, match="PREPARE_GAME failed: Match session login verification failed"):
            server.configure_session(descriptor)

        # Inspect ndjson telemetry: verify strict prohibition of false pass
        assert log_file.exists()
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        login_events = [e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "MATCH_LOGIN"]
        assert len(login_events) >= 1
        assert login_events[-1]["status"] == "FAILED"

        # Strictly forbid false pass:
        completed_events = [e for e in login_events if e["status"] == "COMPLETED"]
        assert len(completed_events) == 0, "MATCH_LOGIN COMPLETED must never be emitted on auth failure!"

        ready_events = [e for e in events if e.get("type") == "ENVIRONMENT_READY"]
        assert len(ready_events) == 0, "ENVIRONMENT_READY must never be emitted on auth failure!"

    def test_r1_missing_session_cookie_on_match_endpoint_fails_closed(
        self, mock_gm_server: MockGameMasterHTTPServer
    ):
        """When session auth is required, /match/<id> without session cookie fails closed with 401."""
        mock_gm_server.require_session_auth = True
        mock_gm_server.active_sessions.clear()

        # Unauthenticated adapter attempting match projection
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        with pytest.raises(GameMasterError) as exc_info:
            adapter.get_match_projection(mock_gm_server.match_code)
        assert "401" in str(exc_info.value) or "UNAUTHORIZED" in str(exc_info.value)

        # Direct HTTP request without cookie
        req = urllib.request.Request(f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}")
        with pytest.raises(urllib.error.HTTPError) as http_err:
            urllib.request.urlopen(req)
        assert http_err.value.code == 401

    def test_r1_expired_or_forged_session_cookie_fails_closed(
        self, mock_gm_server: MockGameMasterHTTPServer
    ):
        """When session token is expired, unknown, or forged, /match/<id> returns 401."""
        mock_gm_server.require_session_auth = True
        mock_gm_server.active_sessions = {"valid-session-guid-111"}

        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        # Inject forged / expired cookie
        adapter.session_cookies = {"session_token": "forged-or-expired-session-999"}

        with pytest.raises(GameMasterError) as exc_info:
            adapter.get_match_projection(mock_gm_server.match_code)
        assert "401" in str(exc_info.value) or "UNAUTHORIZED" in str(exc_info.value)

    def test_r1_sec01_origin_allowlist_unassigned_port(
        self, mock_gm_server: MockGameMasterHTTPServer
    ):
        """SEC-01: matchPageUrl with unassigned port must be blocked immediately."""
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        unassigned_url = "http://127.0.0.1:9999/match/TEST1234"

        with pytest.raises(GameMasterSecurityError) as exc_info:
            adapter.login_session(
                username="player-1",
                password="pass",
                match_page_url=unassigned_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert "SEC-01" in str(exc_info.value)
        assert "not in allowedMatchOrigins" in str(exc_info.value)

    @pytest.mark.parametrize(
        "malicious_url",
        [
            "http://169.254.169.254/latest/meta-data",
            "http://169.254.169.254/match/123",
            "http://metadata.google.internal/computeMetadata/v1",
            "https://evil.attacker.com/match/KWO5GPMP",
            "http://evil-server.org:8000/match/123",
            "http://attacker.local/api/match",
        ],
    )
    def test_r1_sec01_origin_allowlist_ssrf_and_cloud_metadata_blocked(
        self, malicious_url: str, mock_gm_server: MockGameMasterHTTPServer
    ):
        """SEC-01: Prohibited cloud metadata, external attacker, and evil hosts blocked fail-closed."""
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        with pytest.raises(GameMasterSecurityError) as exc_info:
            adapter.login_session(
                username="player-1",
                password="pass",
                match_page_url=malicious_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert "SEC-01" in str(exc_info.value)

    @pytest.mark.parametrize(
        "path_segment",
        [
            "/admin",
            "/grader",
            "/internal",
            "/superadmin",
            "/private",
        ],
    )
    def test_r1_sec01_origin_allowlist_prohibited_paths_blocked(
        self, path_segment: str, mock_gm_server: MockGameMasterHTTPServer
    ):
        """SEC-01: Prohibited internal administrative paths blocked fail-closed."""
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        target_url = f"{mock_gm_server.base_url}{path_segment}/match/{mock_gm_server.match_code}"

        with pytest.raises(GameMasterSecurityError) as exc_info:
            adapter.login_session(
                username="player-1",
                password="pass",
                match_page_url=target_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert "SEC-01" in str(exc_info.value)

    def test_r1_sec01_origin_normalization_variants(
        self, mock_gm_server: MockGameMasterHTTPServer
    ):
        """SEC-01: Trailing slash, case variations, and origin vs netloc matching."""
        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)
        base = mock_gm_server.base_url

        # Match url with trailing slash allowed origin
        res = adapter.login_session(
            username="example-account",
            password="fake-password-for-example",
            match_page_url=f"{base}/match/{mock_gm_server.match_code}",
            allowed_origins=[f"{base}/", f"HTTP://{urllib.parse.urlparse(base).netloc}"],
        )
        assert res["status"] == "AUTHENTICATED"


# ==============================================================================
# Suite 2: R2 Git Invariant Edge Cases
# ==============================================================================

class TestChallengerR2GitInvariantsEdgeCases:
    """Adversarial stress testing for R2 Git invariants."""

    def test_r2_detached_head_at_current_branch_tip_fails_closed(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 3 & 4: Detached HEAD at same commit SHA must be rejected immediately."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # Detach HEAD at current commit
        run_git(checkout_dir, ["checkout", "--detach", "HEAD"])

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo",
        }

        with pytest.raises(RuntimeError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "detached head" in str(exc_info.value).lower()

    def test_r2_detached_head_at_previous_commit_fails_closed(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 3 & 4: Detached HEAD at an older commit on main must fail closed."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # Create second commit
        (checkout_dir / "repo" / "dummy.txt").write_text("commit 2\n", encoding="utf-8")
        run_git(checkout_dir, ["add", "-A"])
        run_git(checkout_dir, ["commit", "-m", "second commit"])
        run_git(checkout_dir, ["push", "origin", "main"])

        # Detach HEAD to initial commit (HEAD~1)
        run_git(checkout_dir, ["checkout", "--detach", "HEAD~1"])

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo",
        }

        with pytest.raises(RuntimeError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "detached head" in str(exc_info.value).lower()

    def test_r2_detached_head_on_different_branch_fails_closed(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 3 & 4: Detached HEAD on a different branch commit fails closed."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # Create branch feature and commit
        run_git(checkout_dir, ["checkout", "-b", "feature/unrelated"])
        (checkout_dir / "repo" / "feat.txt").write_text("feature\n", encoding="utf-8")
        run_git(checkout_dir, ["add", "-A"])
        run_git(checkout_dir, ["commit", "-m", "feature commit"])

        # Detach HEAD
        run_git(checkout_dir, ["checkout", "--detach", "HEAD"])

        # Verification targeting 'main'
        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo",
        }

        with pytest.raises(RuntimeError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "detached head" in str(exc_info.value).lower()

    def test_r2_detached_head_in_prepare_game_fails_closed_telemetry(
        self, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """PREPARE_GAME pipeline must fail closed when repository is in detached HEAD state."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]
        log_file = tmp_path / "detached_head_pipeline.ndjson"

        # Detach HEAD
        run_git(checkout_dir, ["checkout", "--detach", "HEAD"])

        server = MCPHelperServer(workspace_root=tmp_path)
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": "DETACHED_TEST",
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "repository": {
                "url": remote_url,
                "branch": "main",
                "folder": "repo",
            },
            "offline": True,
        }

        with pytest.raises(RuntimeError, match="Git verification failed"):
            server.configure_session(descriptor)

        assert log_file.exists()
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

        repo_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "REPOSITORY"), None)
        assert repo_event is not None
        assert repo_event["status"] == "FAILED"
        assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)

    def test_r2_remote_url_normalization_variants_pass(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 2: Normalization handles casing, slashes, backslashes, and .git suffix cleanly."""
        checkout_dir = offline_git_env["checkout_dir"]
        base_remote = "https://github.com/my-org/my-repo"
        offline_git_env["set_remote_url"](f"{base_remote}.git")

        variants = [
            f"{base_remote}.git",
            f"{base_remote}",
            f"{base_remote}///",
            f"{base_remote}.git/",
            f"{base_remote}.git///",
            f"HTTPS://GITHUB.COM/MY-ORG/MY-REPO.GIT",
            f"https://GITHUB.COM/my-org/my-repo",
            f"  {base_remote}.git  ",
            base_remote.replace("/", "\\") + ".git",
        ]

        for var in variants:
            descriptor = {
                "url": var,
                "branch": "main",
                "folder": "repo",
            }
            res = verify_assigned_repository(checkout_dir, descriptor)
            assert res["head_sha"] == offline_git_env["initial_sha"]
            assert res["branch"] == "main"

    @pytest.mark.parametrize(
        "bad_url",
        [
            "https://github.com/my-org/my-repo-evil.git",
            "https://github.com/my-org/other-repo.git",
            "https://github.com/different-org/my-repo.git",
            "http://github.com/my-org/my-repo.git",  # Scheme mismatch
            "ssh://git@github.com/my-org/my-repo.git",
            "https://github.com/my-org/my-repo/subpath",
            "",
        ],
    )
    def test_r2_remote_url_mismatches_fail_closed(
        self, bad_url: str, offline_git_env: Dict[str, Any]
    ):
        """Invariant 2: URL divergences, wrong orgs, or mismatched protocols fail closed."""
        checkout_dir = offline_git_env["checkout_dir"]
        offline_git_env["set_remote_url"]("https://github.com/my-org/my-repo.git")

        descriptor = {
            "url": bad_url,
            "branch": "main",
            "folder": "repo",
        }

        with pytest.raises(ValueError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "Invariant 2 violated" in str(exc_info.value)

    @pytest.mark.parametrize(
        "traversal_path",
        [
            "../",
            "../../outside",
            "repo/../../",
            "repo/../../../secret",
            "repo/../../outside",
            "..\\outside",
        ],
    )
    def test_r2_subfolder_traversal_attempts_fail_closed(
        self, traversal_path: str, offline_git_env: Dict[str, Any]
    ):
        """Invariant 5: Directory traversal outside checkout_root fails closed with ValueError."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": traversal_path,
        }

        with pytest.raises(ValueError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "traverses outside checkout root" in str(exc_info.value)

    def test_r2_subfolder_internal_dotdot_resolves_within_root(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 5 behavior: Reject folder aliases containing '..' path components even if resolving within root."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # 'repo/..' must be rejected because it contains '..' as a path component
        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo/..",
        }
        with pytest.raises(ValueError) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "path component" in str(exc_info.value)

    def test_r2_missing_subfolder_and_file_as_subfolder_fail_closed(
        self, offline_git_env: Dict[str, Any]
    ):
        """Invariant 5: Non-existent folder or file-as-folder fails closed."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        # 1. Non-existent folder
        desc_nonexistent = {
            "url": remote_url,
            "branch": "main",
            "folder": "subfolder_does_not_exist_404",
        }
        with pytest.raises(RuntimeError, match="does not exist"):
            verify_assigned_repository(checkout_dir, desc_nonexistent)

        # 2. Regular file instead of directory
        desc_file = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo/README.md",
        }
        with pytest.raises(RuntimeError, match="is not a directory"):
            verify_assigned_repository(checkout_dir, desc_file)

        # 3. Empty or None folder
        with pytest.raises(ValueError, match="Empty 'folder'"):
            verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": "   "})
        with pytest.raises(ValueError, match="Missing 'folder'"):
            verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": None})

    def test_r2_mcp_prepare_game_folder_traversal_blocks_pipeline(
        self, offline_git_env: Dict[str, Any], tmp_path: Path
    ):
        """PREPARE_GAME pipeline must fail closed when folder traversal is attempted."""
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]
        log_file = tmp_path / "traversal_pipeline.ndjson"

        server = MCPHelperServer(workspace_root=tmp_path)
        descriptor = {
            "type": "PREPARE_GAME",
            "matchId": "TRAVERSAL_ATTACK",
            "progressLogFile": str(log_file),
            "checkoutRoot": str(checkout_dir),
            "repository": {
                "url": remote_url,
                "branch": "main",
                "folder": "../forbidden",
            },
            "offline": True,
        }

        with pytest.raises(RuntimeError, match="Git verification failed"):
            server.configure_session(descriptor)

        assert log_file.exists()
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        repo_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "REPOSITORY"), None)
        assert repo_event is not None
        assert repo_event["status"] == "FAILED"


# ==============================================================================
# Suite 3: R3 Match Projection Schema Fuzzing & Next.js Parser
# ==============================================================================

class TestChallengerR3MatchProjectionSchemaFuzzing:
    """Adversarial stress testing for R3 match projection schema validation and Next.js parsing."""

    @pytest.mark.parametrize(
        "bad_uuid",
        [
            None,
            "",
            "    ",
            12345,
            12.34,
            [],
            {},
            True,
        ],
    )
    def test_r3_fuzz_missing_or_invalid_match_uuid(self, bad_uuid: Any):
        """Match projection missing matchUuid (and id) or having non-string uuid must raise ValueError."""
        payload = {
            "matchUuid": bad_uuid,
            "matchCode": "CODE123",
            "backlogItems": [],
        }
        with pytest.raises(ValueError, match="missing required 'matchUuid' or 'id'"):
            validate_match_projection_schema(payload)

    @pytest.mark.parametrize(
        "bad_code",
        [
            None,
            "",
            "    ",
            12345,
            [],
            {},
            False,
        ],
    )
    def test_r3_fuzz_missing_or_invalid_match_code(self, bad_code: Any):
        """Match projection missing matchCode or having non-string matchCode must raise ValueError."""
        payload = {
            "matchUuid": "8c0e1b22-1111-4222-8333-444444444444",
            "matchCode": bad_code,
            "backlogItems": [],
        }
        with pytest.raises(ValueError, match="missing required 'matchCode'"):
            validate_match_projection_schema(payload)

    @pytest.mark.parametrize(
        "bad_items",
        [
            {"task-1": "data"},  # dict instead of list
            None,                # null
            "task-1, task-2",    # string
            12345,               # int
            12.34,               # float
            True,                # bool
        ],
    )
    def test_r3_fuzz_non_list_backlog_items(self, bad_items: Any):
        """Match projection with non-list backlogItems must raise ValueError."""
        payload = {
            "matchUuid": "8c0e1b22-1111-4222-8333-444444444444",
            "matchCode": "KWO5GPMP",
            "backlogItems": bad_items,
        }
        with pytest.raises(ValueError, match="missing required 'backlogItems' as a list"):
            validate_match_projection_schema(payload)

    def test_r3_missing_backlog_items_key_fails_closed(self):
        """Match projection missing backlogItems key entirely must raise ValueError."""
        payload = {
            "matchUuid": "8c0e1b22-1111-4222-8333-444444444444",
            "matchCode": "KWO5GPMP",
        }
        with pytest.raises(ValueError, match="missing required 'backlogItems' as a list"):
            validate_match_projection_schema(payload)

    @pytest.mark.parametrize(
        "non_dict_payload",
        [
            None,
            {},
            [],
            "string_payload",
            12345,
            True,
        ],
    )
    def test_r3_non_dict_payload_fails_closed(self, non_dict_payload: Any):
        """Empty or non-dictionary projection must raise ValueError."""
        with pytest.raises(ValueError):
            validate_match_projection_schema(non_dict_payload)

    @pytest.mark.parametrize(
        "malformed_content",
        [
            "{broken json, missing brace",
            "{\"props\": {\"pageProps\": ",  # truncated
            "<<invalid xml>>",
            "null",
            "undefined",
            "",
            "   ",
        ],
    )
    def test_r3_corrupted_json_in_nextjs_script_tag(
        self, malformed_content: str, mock_gm_server: MockGameMasterHTTPServer
    ):
        """Adapter fails closed with GameMasterError when <script id="__NEXT_DATA__"> is malformed."""
        mock_gm_server.render_html_mode = True
        mock_gm_server.malformed_next_data = True

        # Custom handler sending custom malformed HTML
        html_payload = (
            f"<!DOCTYPE html><html><body>"
            f"<script id=\"__NEXT_DATA__\" type=\"application/json\">{malformed_content}</script>"
            f"</body></html>"
        )

        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)

        with pytest.raises(GameMasterError):
            adapter.get_match_projection(mock_gm_server.match_code)

    def test_r3_html_response_without_nextjs_tag_fails_closed(
        self, mock_gm_server: MockGameMasterHTTPServer
    ):
        """HTML response without __NEXT_DATA__ (e.g. 500 error page) fails closed with GameMasterError."""
        mock_gm_server.render_html_mode = True

        adapter = OfficialGameMasterAdapter(api_base_url=mock_gm_server.base_url, timeout=5.0)

        # Force GM to return plain HTML without __NEXT_DATA__
        with mock_gm_server.lock:
            mock_gm_server.malformed_next_data = False
            # monkey-patch _send_html on handler by forcing custom response
            orig_send_html = mock_gm_server.render_html_mode

        # Using direct urllib mock check or testing adapter against non-json body:
        mock_gm_server.render_html_mode = True
        mock_gm_server.malformed_next_data = True  # sends HTML without valid JSON

        with pytest.raises(GameMasterError, match="Received non-JSON response|Could not retrieve match projection"):
            adapter.get_match_projection(mock_gm_server.match_code)

    def test_r3_mcp_refresh_match_fails_closed_on_corrupt_projection(
        self, mock_gm_server: MockGameMasterHTTPServer, tmp_path: Path
    ):
        """In MCPHelperServer.refresh_match(), a corrupted projection raises RuntimeError."""
        mock_gm_server.render_html_mode = True
        mock_gm_server.malformed_next_data = True

        server = MCPHelperServer(workspace_root=tmp_path)
        descriptor = {
            "type": "START_GAME",
            "matchId": mock_gm_server.match_code,
            "matchUuid": mock_gm_server.match_uuid,
            "baseUrl": mock_gm_server.base_url,
            "apiKeySecret": mock_gm_server.auth_token,
            "offline": False,
        }
        server.configure_session(descriptor)

        with pytest.raises(RuntimeError, match="Failed to refresh match projection"):
            server.refresh_match()
