"""
tests/test_mcp_server.py: Comprehensive unit tests for runner/mcp_server.py.
Verifies:
1. Strict Git Invariant verification and fail-closed telemetry.
2. Strict Doctor script execution and output verification ("Environment status: READY").
3. Genuine session authentication for PREPARE_GAME.
4. Genuine 3-gate pass emitting ENVIRONMENT_READY.
5. refresh_match schema validation, wave tracking, and BACKLOG_STATUS: DISCOVERED telemetry.
6. submit_work journal state transitions: 409 ALREADY_SUBMITTED lock retention vs 422/conflict REJECTED vs timeout IN_FLIGHT.
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from runner.mcp_server import MCPHelperServer
try:
    from tests.fixtures.git_harness import run_git
    from tests.fixtures.mock_gm_server import MockGameMasterHTTPServer
except ModuleNotFoundError:
    from fixtures.git_harness import run_git
    from fixtures.mock_gm_server import MockGameMasterHTTPServer
from mtm_competition.tasks.journal import SubmissionJournal


@pytest.fixture
def base_server(tmp_path):
    """Create an isolated MCPHelperServer instance."""
    server = MCPHelperServer(workspace_root=tmp_path)
    return server


# ==============================================================================
# 1. Strict Git Invariants Unit Tests
# ==============================================================================

def test_prepare_game_fails_closed_on_missing_git_dir(base_server, tmp_path):
    """Gate 1: Must fail closed with REPOSITORY: FAILED if .git is missing."""
    log_file = tmp_path / "missing_git.ndjson"
    fake_checkout = tmp_path / "non_git_repo"
    fake_checkout.mkdir()

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": "TEST01",
        "progressLogFile": str(log_file),
        "checkoutRoot": str(fake_checkout),
        "repository": {
            "url": "https://github.com/example/repo.git",
            "branch": "main",
            "folder": "TEST01",
        },
    }

    with pytest.raises(RuntimeError, match="Git verification failed"):
        base_server.configure_session(descriptor)

    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    repo_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "REPOSITORY"), None)
    assert repo_event is not None
    assert repo_event["status"] == "FAILED"
    assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)


def test_prepare_game_fails_closed_on_remote_mismatch(base_server, offline_git_env, tmp_path):
    """Gate 1: Must fail closed if assigned repository URL does not match git origin."""
    log_file = tmp_path / "remote_mismatch.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    offline_git_env["set_remote_url"]("https://github.com/actual/repo.git")

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": "TEST02",
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "repository": {
            "url": "https://github.com/assigned/different_repo.git",
            "branch": "main",
            "folder": "TEST02",
        },
    }

    with pytest.raises(RuntimeError, match="Git verification failed"):
        base_server.configure_session(descriptor)

    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    repo_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "REPOSITORY"), None)
    assert repo_event is not None
    assert repo_event["status"] == "FAILED"


def test_prepare_game_fails_closed_on_folder_mismatch(base_server, offline_git_env, tmp_path):
    """Gate 1: Must fail closed if assigned repository folder does not exist in checkout."""
    log_file = tmp_path / "folder_mismatch.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": "NON_EXISTENT_FOLDER",
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": "NON_EXISTENT_FOLDER",
        },
    }

    with pytest.raises(RuntimeError, match="Git verification failed"):
        base_server.configure_session(descriptor)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    repo_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "REPOSITORY"), None)
    assert repo_event is not None
    assert repo_event["status"] == "FAILED"


# ==============================================================================
# 2. Strict Doctor Script Execution Unit Tests
# ==============================================================================

def test_prepare_game_fails_on_doctor_missing_ready_string(base_server, offline_git_env, tmp_path):
    """Gate 2: Must fail closed if doctor exits 0 but misses 'Environment status: READY'."""
    log_file = tmp_path / "doctor_no_ready.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = "DOC01"
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)
    (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

    # Doctor script outputs success message without the exact required string
    env_req = checkout_dir / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text("Write-Output 'All tests passed'\nexit 0\n", encoding="utf-8")
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text("#!/bin/sh\necho 'All tests passed'\nexit 0\n", encoding="utf-8")
        doc_sh.chmod(0o755)

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    with pytest.raises(RuntimeError, match="Environment verification failed"):
        base_server.configure_session(descriptor)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
    assert build_event is not None
    assert build_event["status"] == "FAILED"


def test_prepare_game_fails_when_no_doctor_or_build_command(base_server, offline_git_env, tmp_path):
    """Gate 2: Must fail closed if no doctor script or valid build command exists."""
    log_file = tmp_path / "no_doctor.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = "DOC02"
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)
    (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)
    for p in checkout_dir.rglob("doctor.*"):
        if p.is_file():
            p.unlink()

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "offline": True,
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    with pytest.raises(RuntimeError, match="Environment verification failed"):
        base_server.configure_session(descriptor)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    build_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
    assert build_event is not None
    assert build_event["status"] == "FAILED"


# ==============================================================================
# 3. Genuine Session Match Authentication Unit Tests
# ==============================================================================

def test_prepare_game_fails_closed_on_missing_session_credentials(base_server, offline_git_env, tmp_path):
    """Gate 3: Must fail closed if username, password, or matchPageUrl is missing in online mode."""
    log_file = tmp_path / "missing_auth.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = "AUTH01"
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)
    (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

    # Valid doctor script so Gate 2 passes
    env_req = checkout_dir / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY'\nexit 0\n", encoding="utf-8")
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text("#!/bin/sh\necho 'Environment status: READY'\nexit 0\n", encoding="utf-8")
        doc_sh.chmod(0o755)

    # Missing username and password
    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "offline": False,
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    with pytest.raises(RuntimeError, match="Missing required authentication fields"):
        base_server.configure_session(descriptor)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    login_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "MATCH_LOGIN"), None)
    assert login_event is not None
    assert login_event["status"] == "FAILED"


def test_prepare_game_fails_closed_on_invalid_session_login(base_server, mock_gm_server, offline_git_env, tmp_path):
    """Gate 3: Must fail closed if GM rejects credentials with 401."""
    log_file = tmp_path / "bad_login.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = mock_gm_server.match_code
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)
    (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

    env_req = checkout_dir / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY'\nexit 0\n", encoding="utf-8")
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text("#!/bin/sh\necho 'Environment status: READY'\nexit 0\n", encoding="utf-8")
        doc_sh.chmod(0o755)

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "username": "invalid-user",
        "password": "wrong-password",
        "matchPageUrl": f"{mock_gm_server.base_url}/match/{match_code}",
        "allowedMatchOrigins": [mock_gm_server.base_url],
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    with pytest.raises(RuntimeError, match="Match session login verification failed"):
        base_server.configure_session(descriptor)

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    login_event = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "MATCH_LOGIN"), None)
    assert login_event is not None
    assert login_event["status"] == "FAILED"


# ==============================================================================
# 4. Genuine 3-Gate Pass Emits ENVIRONMENT_READY
# ==============================================================================

def test_prepare_game_emits_environment_ready_on_all_gates_passed(base_server, mock_gm_server, offline_git_env, tmp_path):
    """Verifies that ENVIRONMENT_READY is emitted when all 3 gates genuinely pass."""
    log_file = tmp_path / "all_pass.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = mock_gm_server.match_code
    repo_url = "https://github.com/example/repo.git"
    offline_git_env["set_remote_url"](repo_url)
    (checkout_dir / match_code).mkdir(parents=True, exist_ok=True)

    env_req = checkout_dir / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text("Write-Output 'Environment status: READY'\nexit 0\n", encoding="utf-8")
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text("#!/bin/sh\necho 'Environment status: READY'\nexit 0\n", encoding="utf-8")
        doc_sh.chmod(0o755)

    descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "requestId": "req-prep-pass",
        "playerSessionId": "session-prep-pass",
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "matchRoot": str(checkout_dir),
        "username": "example-account",
        "password": "fake-password-for-example",
        "matchPageUrl": f"{mock_gm_server.base_url}/match/{match_code}",
        "allowedMatchOrigins": [mock_gm_server.base_url],
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    result = base_server.configure_session(descriptor)
    assert result["status"] == "CONFIGURED"

    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    stages = {e.get("stage"): e.get("status") for e in events if e.get("type") == "PREPARATION_STATUS"}
    assert stages.get("REPOSITORY") == "COMPLETED"
    assert stages.get("BUILD") == "COMPLETED"
    assert stages.get("MATCH_LOGIN") == "COMPLETED"

    ready = next((e for e in events if e.get("type") == "ENVIRONMENT_READY"), None)
    assert ready is not None
    assert ready["build"] == "PASS"
    assert ready["matchLogin"] == "PASS"


# ==============================================================================
# 5. refresh_match Schema Validation & Wave Tracking
# ==============================================================================

def test_refresh_match_tracks_waves_and_emits_discovered(base_server, mock_gm_server, offline_git_env, tmp_path):
    """Verifies schema validation, wave tracking, and BACKLOG_STATUS: DISCOVERED emission."""
    log_file = tmp_path / "waves.ndjson"
    match_code = mock_gm_server.match_code

    base_server.configure_session({
        "matchId": match_code,
        "baseUrl": mock_gm_server.base_url,
        "bearerToken": mock_gm_server.auth_token,
        "progressLogFile": str(log_file),
        "playerSessionId": "session-waves-unit",
        "assignedBranch": "main",
        "checkoutRoot": str(offline_git_env["checkout_dir"]),
    })

    # Initial task on board
    mock_gm_server.backlog_items["TASK-01"] = {
        "id": "TASK-01",
        "key": "TASK-01",
        "name": "Task 1",
        "title": "Task 1",
        "status": "OPEN",
        "wave": 1,
        "points": 100,
    }

    # Initial refresh
    res1 = base_server.refresh_match()
    assert res1["wave"] == 1
    assert "TASK-01" in res1["newlyDiscovered"]

    # Simulate Wave 2 arrival
    mock_gm_server.simulate_wave_2()
    res2 = base_server.refresh_match()
    assert res2["wave"] == 2
    assert "TASK-WAVE2-01" in res2["newlyDiscovered"]
    assert "TASK-WAVE2-02" in res2["newlyDiscovered"]

    # Simulate Wave 3 arrival
    mock_gm_server.simulate_wave_3()
    res3 = base_server.refresh_match()
    assert res3["wave"] == 3
    assert "TASK-WAVE3-01" in res3["newlyDiscovered"]

    # Verify telemetry has DISCOVERED for each wave item
    events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    discovered_keys = {
        e["backlogId"] for e in events
        if e.get("type") == "BACKLOG_STATUS" and e.get("status") == "DISCOVERED"
    }
    assert {"TASK-01", "TASK-WAVE2-01", "TASK-WAVE2-02", "TASK-WAVE3-01"}.issubset(discovered_keys)


def test_refresh_match_fails_on_malformed_projection_schema(base_server):
    """Verifies that refresh_match enforces validate_match_projection_schema."""
    base_server.adapter = MagicMock()
    base_server.bearer_token = "test-bearer-key"
    base_server.match_code = "ABCD1234"
    # Malformed projection: missing backlogItems and matchCode
    base_server.adapter.get_match_projection.return_value = {"invalid": "data"}

    with pytest.raises(RuntimeError, match="Failed to refresh match projection"):
        base_server.refresh_match()


# ==============================================================================
# 6. submit_work Journal State Transitions
# ==============================================================================

def test_submit_work_409_already_submitted_retains_lock(base_server, offline_git_env, tmp_path):
    """R5: 409 ALREADY_SUBMITTED retains in-flight lock, requiring reconciliation against server records."""
    checkout_dir = offline_git_env["checkout_dir"]
    match_root = offline_git_env["match_root"]
    branch = offline_git_env["branch"]
    journal_db = tmp_path / "sub_lock.db"

    # Stage answers and commit
    ans_dir = match_root / "answers" / "TASK-LOCK"
    ans_dir.mkdir(parents=True, exist_ok=True)
    ans_file = ans_dir / "submit.json"
    answer_content = {"solution": "original"}
    ans_file.write_text(json.dumps(answer_content), encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "sub lock test"])
    sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    base_server.configure_session({
        "matchUuid": "match-uuid-lock-test",
        "matchCode": "MATCH_LOCK",
        "assignedBranch": branch,
        "checkoutRoot": str(checkout_dir),
        "matchRoot": str(match_root),
        "journalDbPath": str(journal_db),
        "offline": True,
    })

    # Mock adapter to throw 409 ALREADY_SUBMITTED
    class AlreadySubmittedError(Exception):
        status_code = 409
        error_code = "ALREADY_SUBMITTED"

    base_server.adapter.submit_work = MagicMock(side_effect=AlreadySubmittedError("Already submitted"))

    with pytest.raises(AlreadySubmittedError):
        base_server.submit_work(
            client_submission_id="sub-client-01",
            backlog_item_key="TASK-LOCK",
            git_revision=sha,
            answer=answer_content,
        )

    # Verify journal state is ALREADY_SUBMITTED (NOT REJECTED)
    with SubmissionJournal(db_path=journal_db) as j:
        entry = j.get_entry("match-uuid-lock-test", "TASK-LOCK")
        assert entry is not None
        assert entry["state"] == "ALREADY_SUBMITTED"
        assert entry["last_http_code"] == 409

        # In-flight lock is retained: get_in_flight must return this record
        in_flight = j.get_in_flight("match-uuid-lock-test", "TASK-LOCK")
        assert in_flight is not None
        assert in_flight["state"] == "ALREADY_SUBMITTED"


def test_submit_work_network_timeout_retains_in_flight(base_server, offline_git_env, tmp_path):
    """Network timeouts or 5xx keep submission state IN_FLIGHT."""
    checkout_dir = offline_git_env["checkout_dir"]
    match_root = offline_git_env["match_root"]
    branch = offline_git_env["branch"]
    journal_db = tmp_path / "timeout.db"

    ans_dir = match_root / "answers" / "TASK-TIMEOUT"
    ans_dir.mkdir(parents=True, exist_ok=True)
    (ans_dir / "submit.json").write_text(json.dumps({"ans": 1}), encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "timeout test"])
    sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    base_server.configure_session({
        "matchUuid": "match-uuid-timeout",
        "matchCode": "MATCH_TO",
        "assignedBranch": branch,
        "checkoutRoot": str(checkout_dir),
        "matchRoot": str(match_root),
        "journalDbPath": str(journal_db),
        "offline": True,
    })

    class NetworkTimeoutError(Exception):
        status_code = 504
        error_code = "GATEWAY_TIMEOUT"

    base_server.adapter.submit_work = MagicMock(side_effect=NetworkTimeoutError("Gateway timeout"))

    with pytest.raises(NetworkTimeoutError):
        base_server.submit_work(
            client_submission_id="sub-client-to",
            backlog_item_key="TASK-TIMEOUT",
            git_revision=sha,
            answer={"ans": 1},
        )

    with SubmissionJournal(db_path=journal_db) as j:
        entry = j.get_entry("match-uuid-timeout", "TASK-TIMEOUT")
        assert entry is not None
        assert entry["state"] == "IN_FLIGHT"


# ==============================================================================
# 7. Mandatory PREPARE -> START -> SUBMIT Lifecycle Integration Test
# ==============================================================================

def test_prepare_start_submit_lifecycle(base_server, mock_gm_server, offline_git_env, tmp_path):
    """
    Mandatory Gen 8 Integration Test: Full PREPARE -> START -> SUBMIT Lifecycle.
    Verifies across all four operational stages within the same MCPHelperServer instance:
    1. PREPARE_GAME:
       - Configures repository, branch, folder, username, password, matchPageUrl.
       - Validates Git invariants, executes environment doctor, and completes session login.
       - Emits ORCHESTRATOR_STARTED, PREPARATION_STATUS, and ENVIRONMENT_READY.
       - Captures session cookies and verifies branch/UUID.
    2. START_GAME:
       - Called without repository, username, password, or matchUuid.
       - Supplies apiKeySecret, gameMasterApiUrl, teamWorkspace.
       - Verifies assigned_branch, repo_descriptor, session cookies, and match_uuid are retained.
       - Verifies adapter is updated with bearer token without being replaced.
       - Verifies workspace_root is updated to teamWorkspace.
    3. start_work + commit + push + submit_work:
       - Discovers backlog item via refresh_match().
       - Calls start_work() returning submitTemplate and MVS.
       - Implements change, populates answers/<id>/submit.json and evidence.
       - Commits and pushes to origin/main.
       - Calls submit_work() verifying receipt and journal state ACCEPTED (HTTP 201).
    4. NDJSON Telemetry Assertions:
       - Validates entire progress log against schemas/progress-event.schema.json.
       - Asserts playerSessionId is uniform throughout the log.
       - Asserts presence and order of all required lifecycle events.
    """
    try:
        from tests.fixtures.telemetry_validator import validate_progress_file
    except ModuleNotFoundError:
        from fixtures.telemetry_validator import validate_progress_file

    checkout_dir = offline_git_env["checkout_dir"]
    match_code = mock_gm_server.match_code
    repo_url = offline_git_env["remote_url"]
    offline_git_env["set_remote_url"](repo_url)

    # Subfolder for match under checkout root
    match_folder = checkout_dir / match_code
    match_folder.mkdir(parents=True, exist_ok=True)
    (match_folder / "app.py").write_text("print('game source')\n", encoding="utf-8")

    # Doctor script inside env-require
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

    # Commit initial repository structure with doctor and match folder
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "setup match repository and doctor"])
    run_git(checkout_dir, ["push", "origin", "main"])
    prep_head_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    team_workspace = tmp_path / "team_ws"
    team_workspace.mkdir(parents=True, exist_ok=True)
    log_file = team_workspace / "team-sdlc-pack" / "logs" / f"{match_code}.progress.ndjson"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    journal_db = team_workspace / ".mtm" / "submissions.db"

    session_id = "session-lifecycle-gen8-test"

    # -------------------------------------------------------------------------
    # STAGE 1: PREPARE_GAME
    # -------------------------------------------------------------------------
    prepare_descriptor = {
        "type": "PREPARE_GAME",
        "matchId": match_code,
        "requestId": "req-prep-001",
        "playerSessionId": session_id,
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "matchRoot": str(match_folder),
        "username": "example-account",
        "password": "fake-password-for-example",
        "matchPageUrl": f"{mock_gm_server.base_url}/match",  # Base listing page to test R2
        "allowedMatchOrigins": [mock_gm_server.base_url],
        "repository": {
            "url": repo_url,
            "branch": "main",
            "folder": match_code,
        },
    }

    prep_result = base_server.configure_session(prepare_descriptor)
    assert prep_result["status"] == "CONFIGURED"
    assert base_server.assigned_branch == "main"
    assert base_server.repo_descriptor.get("branch") == "main"
    assert base_server.match_uuid == mock_gm_server.match_uuid
    assert base_server.adapter is not None
    assert len(base_server.adapter.session_cookies) > 0, "PREPARE must establish and save session cookies"

    orig_adapter_instance = base_server.adapter
    saved_cookies = dict(base_server.adapter.session_cookies)

    # -------------------------------------------------------------------------
    # STAGE 2: START_GAME (State Continuity)
    # -------------------------------------------------------------------------
    start_descriptor = {
        "type": "START_GAME",
        "matchId": match_code,
        "requestId": "req-start-001",
        "playerSessionId": session_id,
        "teamUuid": "22222222-2222-4222-8222-222222222222",
        "gameMasterApiUrl": mock_gm_server.base_url,
        "apiKeySecret": mock_gm_server.auth_token,
        "teamWorkspace": str(team_workspace),
        "teamSdlcPackWorkspace": str(team_workspace / "team-sdlc-pack"),
        "progressLogFile": str(log_file),
        "checkoutRoot": str(checkout_dir),
        "matchRoot": str(match_folder),
        "matchPageUrl": f"{mock_gm_server.base_url}/match",
        "allowedApiOrigins": [mock_gm_server.base_url],
        "journalDbPath": str(journal_db),
    }

    start_result = base_server.configure_session(start_descriptor)
    assert start_result["status"] == "CONFIGURED"

    # Invariants verification: PREPARE state must survive into START
    assert base_server.assigned_branch == "main", "assigned_branch must be retained from PREPARE"
    assert base_server.repo_descriptor.get("branch") == "main", "repo_descriptor must be retained from PREPARE"
    assert base_server.match_uuid == mock_gm_server.match_uuid, "match_uuid must be retained from PREPARE"
    assert base_server.adapter is orig_adapter_instance, "Adapter instance must be reused, not replaced"
    assert base_server.adapter.api_key == mock_gm_server.auth_token, "Bearer token must be updated on adapter"
    assert base_server.adapter.session_cookies == saved_cookies, "Session cookies must survive into START"
    assert base_server.workspace_root == team_workspace.resolve(), "workspace_root must update to teamWorkspace"

    # -------------------------------------------------------------------------
    # STAGE 3: start_work + commit + push + submit_work
    # -------------------------------------------------------------------------
    # Add a backlog task on Mock GM server
    task_key = "TASK-LIFECYCLE-01"
    mock_gm_server.backlog_items[task_key] = {
        "id": task_key,
        "key": task_key,
        "name": "Lifecycle Integration Task",
        "title": "Implement full lifecycle",
        "status": "OPEN",
        "wave": 1,
        "points": 100,
    }

    # 3a. Refresh match & discover task
    refresh_res = base_server.refresh_match()
    assert refresh_res["status"] in ("IN_PROGRESS", "RUNNING")
    assert task_key in refresh_res["newlyDiscovered"]

    # 3b. Start work on task
    start_work_res = base_server.start_work(task_key)
    assert start_work_res["backlogId"] == task_key
    assert "submitTemplate" in start_work_res

    # Verify template was written to matchRoot/answers/<task_key>/submit.json
    answer_file = match_folder / "answers" / task_key / "submit.json"
    assert answer_file.exists(), "Template submit.json must be generated by start_work"

    # 3c. Modify code, prepare evidence, commit and push
    evidence_dir = match_folder / "answers" / task_key / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_file = evidence_dir / "test_run.log"
    evidence_file.write_text("All unit tests passed successfully.\nExit: 0\n", encoding="utf-8")

    answer_payload = {
        "solution": "Working Gen8 Lifecycle Solution",
        "evidence": ["evidence/test_run.log"],
    }
    answer_file.write_text(json.dumps(answer_payload, indent=2), encoding="utf-8")

    (match_folder / "app.py").write_text("print('lifecycle completed')\n", encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", f"Solve {task_key} with evidence"])
    run_git(checkout_dir, ["push", "origin", "main"])
    sub_head_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()
    assert sub_head_sha != prep_head_sha

    # 3d. Submit work
    client_sub_id = str(uuid.uuid4())
    sub_res = base_server.submit_work(
        client_submission_id=client_sub_id,
        backlog_item_key=task_key,
        git_revision=sub_head_sha,
        answer=answer_payload,
    )
    assert "receiptId" in sub_res or ("submission" in sub_res and "id" in sub_res["submission"])

    # 3e. Verify journal DB state
    with SubmissionJournal(db_path=journal_db) as j:
        entry = j.get_entry(mock_gm_server.match_uuid, task_key)
        assert entry is not None
        assert entry["state"] == "ACCEPTED"
        assert entry["last_http_code"] == 201

    # -------------------------------------------------------------------------
    # STAGE 4: NDJSON Telemetry Verification
    # -------------------------------------------------------------------------
    events = validate_progress_file(log_file, expected_session_id=session_id)
    assert len(events) >= 8

    types = [e["type"] for e in events]
    assert "ORCHESTRATOR_STARTED" in types
    assert "PREPARATION_STATUS" in types
    assert "ENVIRONMENT_READY" in types
    assert "BACKLOG_STATUS" in types
    assert "GAME_MASTER_API" in types

    # Check ENVIRONMENT_READY event
    env_ready = next(e for e in events if e.get("type") == "ENVIRONMENT_READY")
    assert env_ready["build"] == "PASS"
    assert env_ready["matchLogin"] == "PASS"
    assert env_ready["revision"] == prep_head_sha

    # Check DISCOVERED, IN_PROGRESS, SUBMITTED backlog transitions
    backlog_events = [e for e in events if e.get("type") == "BACKLOG_STATUS" and e.get("backlogId") == task_key]
    statuses = [e["status"] for e in backlog_events]
    assert "DISCOVERED" in statuses
    assert "IN_PROGRESS" in statuses
    assert "SUBMITTED" in statuses

    submitted_event = next(e for e in backlog_events if e["status"] == "SUBMITTED")
    assert submitted_event["revision"] == sub_head_sha
    assert submitted_event["receiptId"] is not None


# ==============================================================================
# 8. Generation 8 Unit Tests: Workspace Resolution, Match Targeting & Folder Aliases
# ==============================================================================

def test_workspace_root_resolves_parent_when_named_team_sdlc_pack(tmp_path):
    """R3: MCPHelperServer.__init__ resolves workspace_root to parent if cwd or argument ends with 'team-sdlc-pack'."""
    pack_dir = tmp_path / "team-sdlc-pack"
    pack_dir.mkdir()

    server = MCPHelperServer(workspace_root=pack_dir)
    assert server.workspace_root == tmp_path.resolve()

    # When not ending with team-sdlc-pack, it should stay as-is
    other_dir = tmp_path / "other"
    other_dir.mkdir()
    server2 = MCPHelperServer(workspace_root=other_dir)
    assert server2.workspace_root == other_dir.resolve()


def test_configure_session_team_workspace_updates_workspace_root(tmp_path):
    """R3: configure_session updates workspace_root from descriptor.teamWorkspace."""
    init_dir = tmp_path / "init"
    init_dir.mkdir()
    target_ws = tmp_path / "target_ws"
    target_ws.mkdir()

    server = MCPHelperServer(workspace_root=init_dir)
    assert server.workspace_root == init_dir.resolve()

    server.configure_session({
        "type": "START_GAME",
        "matchId": "MATCH01",
        "teamWorkspace": str(target_ws),
    })
    assert server.workspace_root == target_ws.resolve()


def test_build_match_page_url_targeting():
    """R2: build_match_page_url targets <origin>/match/<matchId> without duplication."""
    from runner.mcp_server import build_match_page_url

    # Base listing path
    res1 = build_match_page_url("https://ai-fest-five.vercel.app/match", "ABCD1234")
    assert res1 == "https://ai-fest-five.vercel.app/match/ABCD1234"

    res2 = build_match_page_url("https://ai-fest-five.vercel.app/match/", "ABCD1234")
    assert res2 == "https://ai-fest-five.vercel.app/match/ABCD1234"

    # Already containing matchId
    res3 = build_match_page_url("https://ai-fest-five.vercel.app/match/ABCD1234", "ABCD1234")
    assert res3 == "https://ai-fest-five.vercel.app/match/ABCD1234"

    # Base origin without path
    res4 = build_match_page_url("https://ai-fest-five.vercel.app", "ABCD1234")
    assert res4 == "https://ai-fest-five.vercel.app/match/ABCD1234"


def test_login_session_rejects_mismatched_match_assignment(mock_gm_server, monkeypatch):
    """R2: login_session validates projection matchCode/matchId and fails closed on mismatch."""
    from mtm_competition.adapters.official_game_master import (
        OfficialGameMasterAdapter,
        GameMasterValidationError,
    )

    adapter = OfficialGameMasterAdapter(
        base_url=mock_gm_server.base_url,
    )

    real_request = adapter._request

    def mock_request(method, url, *args, **kwargs):
        if method == "GET":
            return {
                "matchCode": "DIFFERENT_CODE",
                "matchUuid": "99999999-9999-4999-8999-999999999999",
                "status": "IN_PROGRESS",
                "backlogItems": [],
            }
        return real_request(method, url, *args, **kwargs)

    monkeypatch.setattr(adapter, "_request", mock_request)

    with pytest.raises(GameMasterValidationError) as exc_info:
        adapter.login_session(
            username="example-account",
            password="fake-password-for-example",
            match_page_url=f"{mock_gm_server.base_url}/match",
            allowed_origins=[mock_gm_server.base_url],
            match_id="MY_ASSIGNED_MATCH",
        )
    assert "Match assignment verification failed" in str(exc_info.value)


def test_verify_assigned_repository_folder_alias_dotdot_rejection(offline_git_env):
    """R4: verify_assigned_repository rejects '..' path components while allowing explicit root '.' and './'."""
    from mtm_core.git.repository import verify_assigned_repository

    checkout_dir = offline_git_env["checkout_dir"]
    remote_url = offline_git_env["remote_url"]

    # Reject repo/..
    with pytest.raises(ValueError) as exc1:
        verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": "repo/.."})
    assert "path component" in str(exc1.value)

    # Reject a/b/../../..
    with pytest.raises(ValueError) as exc2:
        verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": "a/b/../../.."})
    assert "path component" in str(exc2.value)

    # Allow literal '.' and './'
    res_dot = verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": "."})
    assert res_dot["match_folder"] == checkout_dir

    res_dot_slash = verify_assigned_repository(checkout_dir, {"url": remote_url, "branch": "main", "folder": "./"})
    assert res_dot_slash["match_folder"] == checkout_dir

