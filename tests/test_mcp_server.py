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
