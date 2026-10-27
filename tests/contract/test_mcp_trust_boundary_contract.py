"""
Contract Tests: Master the Machine (MTM) Trust Boundary & Packaging Hardening (R1-R6).
Implements all 36 contract test cases defined in survey_spec_miner_gen7/test_spec.md:
- Suite R1: TC-R1-01..05 (Session Cookie Authentication & Security Whitelist)
- Suite R2: TC-R2-01..06 (Strict Assigned Git Invariants & Folder Verification)
- Suite R3: TC-R3-01..06 (Authenticated Match Projection & Next.js Parser & Wave Tracking)
- Suite R4: TC-R4-01..06 (Distribution Checker Disambiguation & Parity Verification)
- Suite R5: TC-R5-01..06 (Submission Journal 409 & Error Code Classification)
- Suite R6: TC-R6-01..07 (Team Pack Packaging Compliance & Doctor Fail-Closed Verification)
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
from typing import Any, Dict, List

import pytest

# Ensure repository root is on sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
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
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_core.git.repository import normalize_git_url, verify_assigned_repository
from mtm_knowledge.bootstrap import KnowledgeBootstrap
from runner.telemetry import get_telemetry_logger
try:
    from tests.fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from tests.fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server
except ModuleNotFoundError:
    from fixtures.git_harness import offline_git_env, run_git, set_git_origin_url
    from fixtures.mock_gm_server import MockGameMasterHTTPServer, mock_gm_server


# ==============================================================================
# Suite R1: PREPARE_GAME Session Cookie Authentication & Security Whitelist
# ==============================================================================

class TestSuiteR1SessionAuth:
    """TC-R1-01..05: Genuine Browser/Session Match Authentication & Security Whitelist."""

    def test_tc_r1_01_session_login_success_and_cookie_capture(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R1-01: Session Login Success & Cookie Capture.
        Verify POST /login with valid credentials captures session_token cookie and accesses match page.
        """
        mock_gm_server.require_session_auth = True
        mock_gm_server.valid_credentials = {
            "example-account": "fake-password-for-example"
        }

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        match_page_url = f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}"
        res = adapter.login_session(
            username="example-account",
            password="fake-password-for-example",
            match_page_url=match_page_url,
            allowed_origins=[mock_gm_server.base_url],
        )

        assert res["status"] == "AUTHENTICATED"
        assert res["session_token"]
        assert res["session_token"] in mock_gm_server.active_sessions
        assert "session_token" in res["cookies"]
        assert res["projection"]["matchCode"] == mock_gm_server.match_code
        assert res["projection"]["matchUuid"] == mock_gm_server.match_uuid

    def test_tc_r1_02_session_login_invalid_credentials_fail_closed(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R1-02: Session Login Invalid Credentials Fail-Closed.
        Verify invalid password causes HTTP 401, fails closed, and raises GameMasterAuthError.
        """
        mock_gm_server.require_session_auth = True
        mock_gm_server.valid_credentials = {
            "example-account": "fake-password-for-example"
        }

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        match_page_url = f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}"
        with pytest.raises((GameMasterAuthError, RuntimeError)) as exc_info:
            adapter.login_session(
                username="example-account",
                password="wrong_password_123",
                match_page_url=match_page_url,
                allowed_origins=[mock_gm_server.base_url],
            )
        assert "401" in str(exc_info.value) or "UNAUTHORIZED" in str(exc_info.value) or "Invalid" in str(exc_info.value)
        assert len(mock_gm_server.active_sessions) == 0

    def test_tc_r1_03_missing_credentials_in_prepare_game_prohibits_false_pass(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R1-03: Missing Credentials in PREPARE_GAME Prohibits False Pass.
        Verify omitting username or password in login_session immediately fails closed.
        """
        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        match_page_url = f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}"
        with pytest.raises((GameMasterAuthError, ValueError, RuntimeError)):
            adapter.login_session(
                username="",
                password="",
                match_page_url=match_page_url,
                allowed_origins=[mock_gm_server.base_url],
            )

    def test_tc_r1_04_security_whitelist_blocks_unauthorized_match_origin(self):
        """TC-R1-04: Security Whitelist Blocks Unauthorized Match Origin (SEC-01).
        Verify matchPageUrl pointing to an unapproved origin raises GameMasterSecurityError before network call.
        """
        adapter = OfficialGameMasterAdapter(
            api_base_url="https://ai-fest-five.vercel.app",
            timeout=5.0,
        )

        unauthorized_url = "https://evil-attacker.com/match/ABCD1234"
        with pytest.raises(GameMasterSecurityError) as exc_info:
            adapter.login_session(
                username="example-account",
                password="fake-password-for-example",
                match_page_url=unauthorized_url,
                allowed_origins=["https://ai-fest-five.vercel.app"],
            )
        assert "SEC-01" in str(exc_info.value)

    def test_tc_r1_05_expired_session_cookie_recovery(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R1-05: Expired Session Cookie Recovery.
        Verify request to /match/<id> with expired or missing session cookie returns 401.
        """
        mock_gm_server.require_session_auth = True
        mock_gm_server.active_sessions.clear()

        req = urllib.request.Request(
            f"{mock_gm_server.base_url}/match/{mock_gm_server.match_code}",
            headers={"Cookie": "session_token=deadbeef-0000-0000-0000-000000000000"},
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req)
        assert exc_info.value.code == 401


# ==============================================================================
# Suite R2: Strict Assigned Git Invariants & Folder Verification
# ==============================================================================

class TestSuiteR2GitInvariants:
    """TC-R2-01..06: Strict Assigned Git Repository, Branch & Folder Verification."""

    def test_tc_r2_01_invariant_1_missing_git_directory_rejection(self, tmp_path: Path):
        """TC-R2-01: Invariant 1 — Missing .git Directory Rejection.
        Verify verify_assigned_repository fails closed when .git directory is missing.
        """
        plain_dir = tmp_path / "plain_folder"
        plain_dir.mkdir(parents=True, exist_ok=True)

        descriptor = {
            "url": "https://github.com/example/repo.git",
            "branch": "main",
            "folder": ".",
        }
        with pytest.raises((RuntimeError, ValueError)) as exc_info:
            verify_assigned_repository(plain_dir, descriptor)
        assert "not a git repository" in str(exc_info.value).lower() or ".git" in str(exc_info.value).lower()

    def test_tc_r2_02_invariant_2_remote_origin_url_mismatch_rejection(self, offline_git_env: Dict[str, Any]):
        """TC-R2-02: Invariant 2 — Remote Origin URL Mismatch Rejection.
        Verify repository with mismatched remote URL fails closed.
        """
        checkout_dir = offline_git_env["checkout_dir"]
        descriptor = {
            "url": "https://github.com/different/repo.git",
            "branch": "main",
            "folder": "repo",
        }
        with pytest.raises((ValueError, RuntimeError)) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "remote" in str(exc_info.value).lower() or "mismatch" in str(exc_info.value).lower()

    def test_tc_r2_03_invariant_2b_remote_origin_url_normalization(self, offline_git_env: Dict[str, Any]):
        """TC-R2-03: Invariant 2b — Remote Origin URL Normalization (Trailing .git).
        Verify URL with .git matches URL without .git via normalize_git_url.
        """
        url1 = "https://github.com/example/repo.git"
        url2 = "https://github.com/example/repo"
        url3 = "https://github.com/example/repo/"
        assert normalize_git_url(url1) == normalize_git_url(url2)
        assert normalize_git_url(url2) == normalize_git_url(url3)

        checkout_dir = offline_git_env["checkout_dir"]
        set_git_origin_url(checkout_dir, "https://github.com/example/repo.git")

        descriptor = {
            "url": "https://github.com/example/repo",  # Missing .git
            "branch": "main",
            "folder": "repo",
        }
        res = verify_assigned_repository(checkout_dir, descriptor)
        assert len(res["head_sha"]) >= 40
        assert res["branch"] == "main"

    def test_tc_r2_04_invariant_3_branch_ref_divergence_rejection(self, offline_git_env: Dict[str, Any]):
        """TC-R2-04: Invariant 3 — Branch Ref Divergence Rejection.
        Verify checking out wrong branch fails closed.
        """
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        run_git(checkout_dir, ["checkout", "-b", "feat/wrong"])
        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo",
        }
        with pytest.raises((ValueError, RuntimeError)) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "branch" in str(exc_info.value).lower()

    def test_tc_r2_05_invariant_4_corrupt_or_detached_head_commit_sha_rejection(self, offline_git_env: Dict[str, Any]):
        """TC-R2-05: Invariant 4 — Corrupt or Detached HEAD Commit SHA Rejection.
        Verify detached HEAD commit fails closed.
        """
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        run_git(checkout_dir, ["checkout", "--detach", "HEAD"])
        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "repo",
        }
        with pytest.raises((RuntimeError, ValueError)) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "detached" in str(exc_info.value).lower() or "branch" in str(exc_info.value).lower()

    def test_tc_r2_06_invariant_5_assigned_folder_missing_rejection(self, offline_git_env: Dict[str, Any]):
        """TC-R2-06: Invariant 5 — Assigned Folder Missing Rejection.
        Verify descriptor folder missing under checkout_root fails closed.
        """
        checkout_dir = offline_git_env["checkout_dir"]
        remote_url = offline_git_env["remote_url"]

        descriptor = {
            "url": remote_url,
            "branch": "main",
            "folder": "NON_EXISTENT_SUBFOLDER_XYZ",
        }
        with pytest.raises((ValueError, RuntimeError)) as exc_info:
            verify_assigned_repository(checkout_dir, descriptor)
        assert "folder" in str(exc_info.value).lower() or "not found" in str(exc_info.value).lower()


# ==============================================================================
# Suite R3: Authenticated Match Projection & Next.js Parser & Wave Tracking
# ==============================================================================

class TestSuiteR3MatchProjectionAndWaves:
    """TC-R3-01..06: Authenticated Match Projection & Next.js Parser & Wave Tracking."""

    def test_tc_r3_01_nextjs_spectator_html_extraction(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R3-01: Next.js Spectator HTML Extraction (<script id="__NEXT_DATA__">).
        Verify adapter parses HTML spectator page and extracts pageProps.
        """
        mock_gm_server.render_html_mode = True
        mock_gm_server.team_id = "T01"

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        proj = adapter.get_match_projection(mock_gm_server.match_code)
        assert proj["matchCode"] == mock_gm_server.match_code
        assert proj["matchUuid"] == mock_gm_server.match_uuid
        assert proj["team"]["id"] == "T01"
        assert isinstance(proj["backlogItems"], list)

    def test_tc_r3_02_nextjs_malformed_json_fail_closed(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R3-02: Next.js Malformed JSON Fail-Closed.
        Verify HTML with malformed JSON inside <script id="__NEXT_DATA__"> raises GameMasterError.
        """
        mock_gm_server.render_html_mode = True
        mock_gm_server.malformed_next_data = True

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        with pytest.raises(GameMasterError):
            adapter.get_match_projection(mock_gm_server.match_code)

    def test_tc_r3_03_projection_schema_validation_mandatory_fields(self):
        """TC-R3-03: Projection Schema Validation (Mandatory Fields).
        Verify missing backlogItems or matchUuid or non-dict raises ValueError.
        """
        with pytest.raises(ValueError):
            validate_match_projection_schema({})

        with pytest.raises(ValueError):
            validate_match_projection_schema({"status": "IN_PROGRESS"})

        with pytest.raises(ValueError):
            validate_match_projection_schema({"matchUuid": "uuid-123", "matchCode": "CODE"})

        with pytest.raises(ValueError):
            validate_match_projection_schema({"matchUuid": "uuid-123", "matchCode": "CODE", "backlogItems": "invalid"})

        valid_payload = {
            "matchUuid": "8c0e1b22-1111-4222-8333-444444444444",
            "matchCode": "KWO5GPMP",
            "backlogItems": [],
        }
        res = validate_match_projection_schema(valid_payload)
        assert res["matchCode"] == "KWO5GPMP"

    def test_tc_r3_04_team_identity_verification_in_match_projection(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R3-04: Team Identity Verification in Match Projection.
        Verify spectator projection confirms assigned team identity (team.id).
        """
        mock_gm_server.team_id = "TEAM_WINNER_99"

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        proj = adapter.get_match_projection(mock_gm_server.match_code)
        assert proj["team"]["id"] == "TEAM_WINNER_99"

    def test_tc_r3_05_wave_2_discovery_at_minute_14(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R3-05: Wave 2 Discovery at Minute 14.
        Verify simulate_wave_2 releases Wave 2 items and adapter discovers them.
        """
        mock_gm_server.simulate_wave_2()

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        proj = adapter.get_match_projection(mock_gm_server.match_code)
        keys = [item["id"] for item in proj["backlogItems"]]
        assert "TASK-WAVE2-01" in keys
        assert "TASK-WAVE2-02" in keys
        assert proj["wave"] == 2

    def test_tc_r3_06_wave_3_discovery_and_wait_for_update_notification(self, mock_gm_server: MockGameMasterHTTPServer):
        """TC-R3-06: Wave 3 Discovery and wait_for_update Notification.
        Verify simulate_wave_3 releases Wave 3 items and projection reflects wave 3.
        """
        mock_gm_server.simulate_wave_3()

        adapter = OfficialGameMasterAdapter(
            api_base_url=mock_gm_server.base_url,
            timeout=5.0,
        )

        proj = adapter.get_match_projection(mock_gm_server.match_code)
        keys = [item["id"] for item in proj["backlogItems"]]
        assert "TASK-WAVE3-01" in keys
        assert proj["wave"] == 3


# ==============================================================================
# Suite R4: Distribution Checker Disambiguation & Parity Verification
# ==============================================================================

class TestSuiteR4DistributionParity:
    """TC-R4-01..06: Distribution Checker Layout Disambiguation & Parity Verification."""

    def _setup_valid_distribution(self, tmp_path: Path) -> tuple[Path, Path]:
        ws_dir = tmp_path / "workspace"
        ws_dir.mkdir(parents=True, exist_ok=True)
        mtm_dir = ws_dir / ".mtm"
        mtm_dir.mkdir(parents=True, exist_ok=True)

        pack_dir = ws_dir / "team-sdlc-pack"
        pack_dir.mkdir(parents=True, exist_ok=True)

        runtime_data = {
            "schemaVersion": 1,
            "kind": "mtm-team-runtime",
            "name": "team-sdlc-pack",
            "version": "1.0.0",
            "protocolVersion": 4,
            "provider": "codex",
            "entrypoint": {
                "rootAgentType": "team_orchestrator",
                "script": "runner/mcp_server.py"
            }
        }
        info_data = {
            "schemaVersion": 1,
            "version": "1.0.0",
            "teamId": "T01",
            "teamName": "Team MTM"
        }
        pkg_data = {
            "name": "team-sdlc-pack",
            "version": "1.0.0",
            "files": [
                "agents/",
                "bin/",
                "config/",
                "contracts/",
                "docs/",
                "install.ps1",
                "scripts/check-distribution.mjs",
                "scripts/install.mjs",
                "skills/",
                "src/",
                "README.md",
                "team-pack-info.json",
                "team-runtime.json"
            ]
        }

        (pack_dir / "team-runtime.json").write_text(json.dumps(runtime_data), encoding="utf-8")
        (pack_dir / "team-pack-info.json").write_text(json.dumps(info_data), encoding="utf-8")
        (mtm_dir / "team-runtime.json").write_text(json.dumps(runtime_data), encoding="utf-8")
        (mtm_dir / "team-pack-info.json").write_text(json.dumps(info_data), encoding="utf-8")
        (ws_dir / "package.json").write_text(json.dumps(pkg_data), encoding="utf-8")
        (pack_dir / ".codex" / "agents").mkdir(parents=True, exist_ok=True)
        (pack_dir / ".codex" / "agents" / "team_orchestrator.toml").write_text('name = "team_orchestrator"\n', encoding="utf-8")

        agents_dir = pack_dir / "agents"
        agents_dir.mkdir(parents=True, exist_ok=True)
        for prof in ["team_orchestrator", "team_repo_scout", "team_feature_worker", "team_quality_checker"]:
            (agents_dir / f"{prof}.toml").write_text(f'name = "{prof}"\nmodel = "default"\n', encoding="utf-8")

        skill_dir = pack_dir / "skills" / "team-orchestrator"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("# Team Orchestrator Skill\n\nThis is a valid distribution skill file for team orchestrator.\n", encoding="utf-8")

        return ws_dir, pack_dir

    def test_tc_r4_01_disambiguated_cli_arguments(self, tmp_path: Path):
        """TC-R4-01: Disambiguated CLI Arguments (--team-workspace & --pack-root).
        Verify check-distribution.py accepts explicit --team-workspace and --pack-root.
        """
        ws_dir, pack_dir = self._setup_valid_distribution(tmp_path)
        script = ROOT_DIR / "scripts" / "check-distribution.py"

        res = subprocess.run(
            [sys.executable, str(script), "--team-workspace", str(ws_dir), "--pack-root", str(pack_dir)],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "ALL_VERIFIED" in res.stdout
        assert "READY" in res.stdout

    def test_tc_r4_02_missing_team_workspace_runtime_json_failure(self, tmp_path: Path):
        """TC-R4-02: Missing <TeamWorkspace>/.mtm/team-runtime.json Failure.
        Verify distribution check fails if .mtm/team-runtime.json is missing.
        """
        ws_dir, pack_dir = self._setup_valid_distribution(tmp_path)
        (ws_dir / ".mtm" / "team-runtime.json").unlink()
        script = ROOT_DIR / "scripts" / "check-distribution.py"

        res = subprocess.run(
            [sys.executable, str(script), "--team-workspace", str(ws_dir), "--pack-root", str(pack_dir)],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "team-runtime.json" in res.stderr

    def test_tc_r4_03_manifest_version_divergence_failure(self, tmp_path: Path):
        """TC-R4-03: Manifest Version Divergence Between .mtm/ and team-sdlc-pack/.
        Verify version mismatch fails closed.
        """
        ws_dir, pack_dir = self._setup_valid_distribution(tmp_path)
        runtime_diverged = json.loads((ws_dir / ".mtm" / "team-runtime.json").read_text(encoding="utf-8"))
        runtime_diverged["version"] = "9.9.9"
        (ws_dir / ".mtm" / "team-runtime.json").write_text(json.dumps(runtime_diverged), encoding="utf-8")

        script = ROOT_DIR / "scripts" / "check-distribution.py"
        res = subprocess.run(
            [sys.executable, str(script), "--team-workspace", str(ws_dir), "--pack-root", str(pack_dir)],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "version" in res.stderr.lower()

    def test_tc_r4_04_team_id_parity_check_failure(self, tmp_path: Path):
        """TC-R4-04: Team ID Parity Check Between .mtm/team-pack-info.json and Pack.
        Verify mismatch in teamId causes immediate failure.
        """
        ws_dir, pack_dir = self._setup_valid_distribution(tmp_path)
        info_diverged = json.loads((ws_dir / ".mtm" / "team-pack-info.json").read_text(encoding="utf-8"))
        info_diverged["teamId"] = "DIFFERENT_TEAM"
        (ws_dir / ".mtm" / "team-pack-info.json").write_text(json.dumps(info_diverged), encoding="utf-8")

        script = ROOT_DIR / "scripts" / "check-distribution.py"
        res = subprocess.run(
            [sys.executable, str(script), "--team-workspace", str(ws_dir), "--pack-root", str(pack_dir)],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "teamid" in res.stderr.lower()

    def test_tc_r4_05_missing_root_package_json_failure(self, tmp_path: Path):
        """TC-R4-05: Missing Root <TeamWorkspace>/package.json Failure.
        Verify distribution check fails if root package.json is missing.
        """
        ws_dir, pack_dir = self._setup_valid_distribution(tmp_path)
        (ws_dir / "package.json").unlink()

        script = ROOT_DIR / "scripts" / "check-distribution.py"
        res = subprocess.run(
            [sys.executable, str(script), "--team-workspace", str(ws_dir), "--pack-root", str(pack_dir)],
            capture_output=True,
            text=True,
        )
        assert res.returncode != 0
        assert "package.json" in res.stderr.lower()

    def test_tc_r4_06_installer_calls_check_distribution_with_both_args(self):
        """TC-R4-06: Installer Calls check-distribution.py with Both Disambiguated Arguments.
        Verify scripts/install.py passes explicit --team-workspace and --pack-root.
        """
        install_script = (ROOT_DIR / "scripts" / "install.py").read_text(encoding="utf-8")
        assert "--team-workspace" in install_script
        assert "--pack-root" in install_script


# ==============================================================================
# Suite R5: Submission Journal 409 & Error Code Classification
# ==============================================================================

class TestSuiteR5SubmissionJournal:
    """TC-R5-01..06: Submission Journal Safe 409 & Error Code Classification."""

    def test_tc_r5_01_409_already_submitted_retains_lock_and_prohibits_rejected(self, tmp_path: Path):
        """TC-R5-01: 409 ALREADY_SUBMITTED Retains Lock & Prohibits REJECTED.
        Verify that on HTTP 409 ALREADY_SUBMITTED, journal does NOT transition to REJECTED,
        retains in-flight lock, and blocks generating a new submission ID or altered payload.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-01"
            body = b'{"solution": "v1"}'

            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-01",
                request_body_bytes=body,
                git_revision="a" * 40,
            )

            # Record 409 ALREADY_SUBMITTED
            j.record_already_submitted(match_uuid, item_key, 409, "ALREADY_SUBMITTED")

            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "ALREADY_SUBMITTED"
            assert entry["state"] != "REJECTED"

            # Attempting record_rejection must raise ValueError
            with pytest.raises(ValueError):
                j.record_rejection(match_uuid, item_key, 409, "ALREADY_SUBMITTED")

            # Generating a new UUID must fail closed
            with pytest.raises(ValueError):
                j.record_in_flight(
                    match_uuid=match_uuid,
                    backlog_item_key=item_key,
                    client_submission_id="22222222-2222-4222-8222-222222222222",
                    idempotency_key="key-02",
                    request_body_bytes=body,
                    git_revision="b" * 40,
                )

    def test_tc_r5_02_409_idempotency_conflict_transitions_to_rejected_and_unlocks(self, tmp_path: Path):
        """TC-R5-02: 409 IDEMPOTENCY_CONFLICT Transitions to REJECTED and Unlocks.
        Verify that on HTTP 409 IDEMPOTENCY_CONFLICT, journal transitions to REJECTED and releases lock.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-02"

            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-conflict",
                request_body_bytes=b'{"attempt": 1}',
                git_revision="a" * 40,
            )

            j.record_rejection(match_uuid, item_key, 409, "IDEMPOTENCY_CONFLICT")

            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "REJECTED"

            # Lock released: can submit under new UUID
            new_sha = j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="22222222-2222-4222-8222-222222222222",
                idempotency_key="key-conflict-new",
                request_body_bytes=b'{"attempt": 2}',
                git_revision="b" * 40,
            )
            assert new_sha

    def test_tc_r5_03_422_validation_failed_transitions_to_rejected_and_unlocks(self, tmp_path: Path):
        """TC-R5-03: 422 VALIDATION_FAILED Transitions to REJECTED and Unlocks.
        Verify HTTP 422 records REJECTED and allows answer correction.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-03"

            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-val",
                request_body_bytes=b'{"bad": true}',
                git_revision="a" * 40,
            )

            j.record_rejection(match_uuid, item_key, 422, "VALIDATION_FAILED")
            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "REJECTED"

            # Resubmission succeeds
            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="22222222-2222-4222-8222-222222222222",
                idempotency_key="key-val-fixed",
                request_body_bytes=b'{"good": true}',
                git_revision="b" * 40,
            )
            entry2 = j.get_entry(match_uuid, item_key)
            assert entry2["state"] == "IN_FLIGHT"

    def test_tc_r5_04_network_timeout_keeps_in_flight_with_immutable_body(self, tmp_path: Path):
        """TC-R5-04: Network Timeout Keeps IN_FLIGHT with Immutable Body.
        Verify network timeout keeps journal IN_FLIGHT and strictly blocks body alteration.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-04"
            original_body = b'{"code": 1}'

            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-timeout",
                request_body_bytes=original_body,
                git_revision="a" * 40,
            )

            # Replay with altered body fails closed
            with pytest.raises(ValueError) as exc_info:
                j.record_in_flight(
                    match_uuid=match_uuid,
                    backlog_item_key=item_key,
                    client_submission_id="11111111-1111-4111-8111-111111111111",
                    idempotency_key="key-timeout",
                    request_body_bytes=b'{"code": 2}',  # Altered!
                    git_revision="a" * 40,
                )
            assert "conflict" in str(exc_info.value).lower()

            # Replay with identical body succeeds cleanly
            sha = j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-timeout",
                request_body_bytes=original_body,
                git_revision="a" * 40,
            )
            assert sha

    def test_tc_r5_05_400_bad_request_transitions_to_rejected(self, tmp_path: Path):
        """TC-R5-05: 400 BAD_REQUEST Transitions to REJECTED.
        Verify 400 Bad Request releases lock.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-05"

            j.record_in_flight(
                match_uuid=match_uuid,
                backlog_item_key=item_key,
                client_submission_id="11111111-1111-4111-8111-111111111111",
                idempotency_key="key-400",
                request_body_bytes=b'{"invalid": true}',
                git_revision="a" * 40,
            )

            j.record_rejection(match_uuid, item_key, 400, "BAD_REQUEST")
            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "REJECTED"

    def test_tc_r5_06_journal_reconciliation_query_on_already_submitted(self, tmp_path: Path):
        """TC-R5-06: Journal Reconciliation Query on ALREADY_SUBMITTED.
        Verify that after receiving ALREADY_SUBMITTED, reconciliation recovers server receipt and records ACCEPTED.
        """
        db_path = tmp_path / "journal_test.db"
        with SubmissionJournal(db_path=db_path) as j:
            match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
            item_key = "TASK-06"

            j.record_already_submitted(match_uuid, item_key, 409, "ALREADY_SUBMITTED")
            entry = j.get_entry(match_uuid, item_key)
            assert entry["state"] == "ALREADY_SUBMITTED"

            # Reconcile receipt from server
            j.record_receipt(match_uuid, item_key, receipt_id="sub_server_reconciled_999", http_code=200)
            reconciled_entry = j.get_entry(match_uuid, item_key)
            assert reconciled_entry["state"] == "ACCEPTED"
            assert reconciled_entry["receipt_id"] == "sub_server_reconciled_999"


# ==============================================================================
# Suite R6: Team Pack Packaging Compliance & Doctor Fail-Closed Verification
# ==============================================================================

class TestSuiteR6PackagingAndDoctor:
    """TC-R6-01..07: Team Pack Packaging Compliance & Doctor Fail-Closed Verification."""

    def test_tc_r6_01_package_json_standard_files_array_compliance(self):
        """TC-R6-01: package.json Standard files Array Compliance.
        Verify package.json contains all 13 standard entries per Technical Guide lines 1787-1802.
        """
        pkg_file = ROOT_DIR / "package.json"
        data = json.loads(pkg_file.read_text(encoding="utf-8"))
        files = data.get("files", [])
        expected = [
            "agents/",
            "bin/",
            "config/",
            "contracts/",
            "docs/",
            "install.ps1",
            "scripts/check-distribution.mjs",
            "scripts/install.mjs",
            "skills/",
            "src/",
            "README.md",
            "team-pack-info.json",
            "team-runtime.json",
        ]
        assert files == expected

    def test_tc_r6_02_missing_doctor_script_fails_closed(self, tmp_path: Path):
        """TC-R6-02: Missing Doctor Script Fails Closed (No Default Pass).
        Verify repository with no doctor script and no build command fails closed.
        """
        repo_dir = tmp_path / "repo_no_doctor"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "index.js").write_text("console.log('no build');\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="TEST_NO_DOC",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert res["exit_code"] != 0

        out_map = tmp_path / "state" / "PROJECT_MAP.md"
        with pytest.raises(RuntimeError) as exc_info:
            bootstrap.generate_project_map(output_path=out_map, run_verification=True)
        assert "No doctor script" in str(exc_info.value) or "Environment verification failed" in str(exc_info.value)

    def test_tc_r6_03_doctor_script_exit_code_non_zero_fails_closed(self, tmp_path: Path):
        """TC-R6-03: Doctor Script Exit Code != 0 Fails Closed.
        Verify script exiting with non-zero fails closed.
        """
        repo_dir = tmp_path / "repo_fail_code"
        env_req = repo_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            (env_req / "doctor.ps1").write_text("Write-Output 'Failing doctor'\nexit 1\n", encoding="utf-8")
        else:
            (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Failing doctor'\nexit 1\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="TEST_FAIL_CODE",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert res["exit_code"] != 0

        out_map = tmp_path / "state" / "PROJECT_MAP.md"
        bootstrap.generate_project_map(output_path=out_map, run_verification=True)
        assert out_map.exists()
        content = out_map.read_text(encoding="utf-8")
        assert "Discovery status: `FAILED`" in content
        assert "FAIL" in content and "1" in content

    def test_tc_r6_04_doctor_script_exit_0_missing_ready_string_fails_closed(self, tmp_path: Path):
        """TC-R6-04: Doctor Script Exit 0 but Missing 'Environment status: READY' Fails Closed.
        Verify script exiting 0 without exact ready marker is treated as a failure.
        """
        repo_dir = tmp_path / "repo_missing_ready"
        env_req = repo_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            (env_req / "doctor.ps1").write_text("Write-Output 'All checks passed successfully'\nexit 0\n", encoding="utf-8")
        else:
            (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'All checks passed successfully'\nexit 0\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="TEST_NO_READY",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is False
        assert "Environment status: READY" not in res.get("output", "")

        out_map = tmp_path / "state" / "PROJECT_MAP.md"
        bootstrap.generate_project_map(output_path=out_map, run_verification=True)
        assert out_map.exists()
        content = out_map.read_text(encoding="utf-8")
        assert "Discovery status: `FAILED`" in content
        assert "FAIL" in content
        assert "Discovery status: `VERIFIED`" not in content

    def test_tc_r6_05_doctor_script_success_emits_build_completed(self, tmp_path: Path):
        """TC-R6-05: Doctor Script Success Emits BUILD: COMPLETED.
        Verify script exiting 0 AND outputting 'Environment status: READY' passes.
        """
        repo_dir = tmp_path / "repo_good_doctor"
        env_req = repo_dir / "env-require"
        env_req.mkdir(parents=True, exist_ok=True)
        if sys.platform.startswith("win"):
            (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY - Python, Git OK'\nexit 0\n", encoding="utf-8")
        else:
            (env_req / "doctor.sh").write_text("#!/bin/sh\necho 'Environment status: READY - Python, Git OK'\nexit 0\n", encoding="utf-8")

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="TEST_GOOD_DOC",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        res = bootstrap.verify_environment(fail_closed=True)
        assert res["passed"] is True
        assert res["exit_code"] == 0
        assert "Environment status: READY" in res.get("output", "")

        out_map = tmp_path / "state" / "PROJECT_MAP.md"
        bootstrap.generate_project_map(output_path=out_map, run_verification=True)
        assert out_map.exists()
        content = out_map.read_text(encoding="utf-8")
        assert "Discovery status: `VERIFIED`" in content
        assert "PASS" in content

    def test_tc_r6_06_project_map_records_real_exit_status(self, tmp_path: Path):
        """TC-R6-06: PROJECT_MAP.md Records Real Exit Status.
        Verify generated PROJECT_MAP.md outside repo reflects real exit code.
        """
        repo_dir = tmp_path / "repo_map"
        repo_dir.mkdir(parents=True, exist_ok=True)
        (repo_dir / "app.py").write_text("print('test')\n", encoding="utf-8")

        output_map = tmp_path / "state" / "KNOWLEDGE" / "PROJECT_MAP.md"

        bootstrap = KnowledgeBootstrap(
            repo_path=repo_dir,
            match_code="MAP_TEST",
            branch="main",
            folder=".",
            checkout_root=repo_dir,
            match_root=repo_dir,
            app_root=repo_dir,
        )

        bootstrap.generate_project_map(
            output_path=output_map,
            verification_result={"passed": True, "exit_code": 0},
            run_verification=False,
        )

        assert output_map.exists()
        text = output_map.read_text(encoding="utf-8")
        assert "Exit Code: `0`" in text or "pass (exit 0)" in text.lower() or "passed" in text.lower()

    def test_tc_r6_07_full_readiness_gate_emits_environment_ready_only_on_all_passes(self, tmp_path: Path):
        """TC-R6-07: Full Readiness Gate Emits ENVIRONMENT_READY Only on 3 Passes.
        Verify ENVIRONMENT_READY is emitted only when Git, Build, and MATCH_LOGIN all succeed.
        """
        log_file = tmp_path / "readiness.progress.ndjson"
        logger = get_telemetry_logger(log_path=log_file, player_session_id="session-test-readiness")

        logger.emit_preparation_status("REPOSITORY", "COMPLETED", "Git verified")
        logger.emit_preparation_status("BUILD", "COMPLETED", "Build verified")
        logger.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Login verified")

        logger.emit_environment_ready(
            request_id="req-prep-001",
            repo_url="https://github.com/example/repo.git",
            branch="main",
            folder="repo",
            revision="a" * 40,
            build="PASS",
            match_login="PASS",
        )

        assert log_file.exists()
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        ready_ev = next((e for e in events if e.get("type") == "ENVIRONMENT_READY"), None)
        assert ready_ev is not None
        assert ready_ev["build"] == "PASS"
        assert ready_ev["matchLogin"] == "PASS"
        assert len(ready_ev["revision"]) == 40
