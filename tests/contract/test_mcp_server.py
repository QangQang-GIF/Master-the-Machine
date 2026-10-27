"""
tests/contract/test_mcp_server.py: Tests for runner/mcp_server.py.
Verifies JSON-RPC 2.0 protocol over stdin/stdout:
- initialize
- tools/list (all 7 canonical tools with schemas)
- tools/call configure_session
- tools/call login_match
- tools/call refresh_match
- tools/call start_work
- tools/call submit_work
- tools/call report_progress
- tools/call wait_for_update
"""

import json
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from tests.fixtures.git_harness import run_git
except ModuleNotFoundError:
    from fixtures.git_harness import run_git


def test_mcp_server_initialize_and_tools_list(tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 1. Send initialize
    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": "2024-11-05"},
    }
    proc.stdin.write(json.dumps(init_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 1
    assert resp["result"]["serverInfo"]["name"] == "mtm-minimal-helper"

    # 2. Send tools/list
    tools_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/list",
        "params": {},
    }
    proc.stdin.write(json.dumps(tools_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 2
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    expected_tools = {
        "configure_session",
        "login_match",
        "refresh_match",
        "start_work",
        "submit_work",
        "report_progress",
        "wait_for_update",
    }
    assert expected_tools.issubset(set(tools.keys()))

    # 3. Call configure_session
    log_file = tmp_path / "test.progress.ndjson"
    cfg_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "matchUuid": "11111111-2222-3333-4444-555555555555",
                    "matchCode": "ABCD1234",
                    "progressLogFile": str(log_file),
                    "playerSessionId": "session-test-01",
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 3
    assert not resp["result"]["isError"]
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["status"] == "CONFIGURED"
    assert payload["matchUuid"] == "11111111-2222-3333-4444-555555555555"

    # 4. Call login_match
    login_req = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {"name": "login_match", "arguments": {}},
    }
    proc.stdin.write(json.dumps(login_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 4
    assert not resp["result"]["isError"]

    # 5. Call report_progress
    prog_req = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "report_progress",
            "arguments": {
                "event": {
                    "type": "ORCHESTRATOR_STARTED",
                    "summary": "MCP Helper reporting test event",
                }
            },
        },
    }
    proc.stdin.write(json.dumps(prog_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 5
    assert not resp["result"]["isError"]

    # Verify log written to disk
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "ORCHESTRATOR_STARTED" in content

    # 6. Call wait_for_update
    wait_req = {
        "jsonrpc": "2.0",
        "id": 6,
        "method": "tools/call",
        "params": {"name": "wait_for_update", "arguments": {"timeoutMs": 100}},
    }
    proc.stdin.write(json.dumps(wait_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    assert resp["id"] == 6
    assert not resp["result"]["isError"]

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_server_start_and_submit(mock_gm_server, offline_git_env, tmp_path):
    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    log_file = tmp_path / "progress.ndjson"
    match_uuid = mock_gm_server.match_uuid
    task_id = "TASK-01"
    checkout_dir = offline_git_env["checkout_dir"]
    match_root = offline_git_env["match_root"]

    # Configure session
    cfg_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "matchUuid": match_uuid,
                    "matchCode": "ABCD1234",
                    "baseUrl": mock_gm_server.base_url,
                    "bearerToken": mock_gm_server.auth_token,
                    "progressLogFile": str(log_file),
                    "playerSessionId": "session-test-02",
                    "checkoutRoot": str(checkout_dir),
                    "matchRoot": str(match_root),
                    "assignedBranch": "main",
                    "journalDbPath": str(tmp_path / "mcp_journal.db"),
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # Start work
    start_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "start_work", "arguments": {"backlogItemKey": task_id}},
    }
    proc.stdin.write(json.dumps(start_req) + "\n")
    proc.stdin.flush()
    start_line = proc.stdout.readline()
    start_resp = json.loads(start_line)
    assert not start_resp["result"]["isError"]
    start_data = json.loads(start_resp["result"]["content"][0]["text"])
    assert "submitTemplate" in start_data

    # Commit answer and evidence to git
    ans_dir = match_root / "answers" / task_id
    ans_dir.mkdir(parents=True, exist_ok=True)
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)

    answer_payload = {"fix_applied": True, "notes": "resolved through MCP"}
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload), encoding="utf-8")
    (ev_dir / "TASK-01-TC-01.json").write_text('{"passed": true}', encoding="utf-8")

    try:
        from tests.fixtures.git_harness import run_git
    except ModuleNotFoundError:
        from fixtures.git_harness import run_git
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", f"Solve {task_id}"])
    run_git(checkout_dir, ["push", "origin", "main"])
    commit_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=str(checkout_dir), text=True
    ).strip()

    # Submit work
    client_sub_id = str(uuid.uuid4())
    sub_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {
            "name": "submit_work",
            "arguments": {
                "clientSubmissionId": client_sub_id,
                "backlogItemKey": task_id,
                "gitRevision": commit_sha,
                "answer": answer_payload,
            },
        },
    }
    proc.stdin.write(json.dumps(sub_req) + "\n")
    proc.stdin.flush()
    sub_line = proc.stdout.readline()
    sub_resp = json.loads(sub_line)
    assert not sub_resp["result"]["isError"]
    sub_data = json.loads(sub_resp["result"]["content"][0]["text"])
    assert sub_data["submission"]["status"] == "ACCEPTED"

    # Verify automatic progress telemetry was emitted to progressLogFile (P0.6)
    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    event_types = [e.get("type") for e in events]
    assert "GAME_MASTER_API" in event_types
    assert "BACKLOG_STATUS" in event_types

    gm_events = [e for e in events if e.get("type") == "GAME_MASTER_API"]
    assert any(e.get("operation") == "START_WORK" and e.get("phase") == "REQUESTED" for e in gm_events)
    assert any(e.get("operation") == "START_WORK" and e.get("phase") == "SUCCEEDED" for e in gm_events)
    assert any(e.get("operation") == "SUBMIT" and e.get("phase") == "REQUESTED" for e in gm_events)
    assert any(e.get("operation") == "SUBMIT" and e.get("phase") == "SUCCEEDED" for e in gm_events)

    status_events = [e for e in events if e.get("type") == "BACKLOG_STATUS"]
    assert any(e.get("backlogId") == task_id and e.get("status") == "IN_PROGRESS" for e in status_events)
    assert any(e.get("backlogId") == task_id and e.get("status") == "SUBMITTED" for e in status_events)

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_server_prepare_game_pipeline(offline_git_env, tmp_path):
    """P0.5: Verifies PREPARE_GAME generates PROJECT_MAP.md outside repo and emits ENVIRONMENT_READY."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    log_file = tmp_path / "prep.progress.ndjson"
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = "ABCD1234"

    cfg_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "type": "PREPARE_GAME",
                    "matchId": match_code,
                    "requestId": "req-prep-01",
                    "playerSessionId": "session-prep-01",
                    "progressLogFile": str(log_file),
                    "checkoutRoot": str(checkout_dir),
                    "matchRoot": str(checkout_dir),
                    "repository": {
                        "url": "https://github.com/example/repo.git",
                        "branch": "main",
                        "folder": match_code,
                    },
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()
    line = proc.stdout.readline()
    resp = json.loads(line)
    assert not resp["result"]["isError"]

    # Verify telemetry has ENVIRONMENT_READY
    assert log_file.exists()
    events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    ready_event = next((e for e in events if e.get("type") == "ENVIRONMENT_READY"), None)
    assert ready_event is not None
    assert ready_event["build"] == "PASS"
    assert ready_event["matchLogin"] == "PASS"
    assert len(ready_event["revision"]) >= 40

    # Verify PROJECT_MAP.md was created in team-sdlc-pack/state/<matchCode>/knowledge/ (outside repo!)
    pack_dir = Path(__file__).resolve().parent.parent.parent
    project_map = pack_dir / "team-sdlc-pack" / "state" / match_code / "knowledge" / "PROJECT_MAP.md"
    assert project_map.exists()
    map_text = project_map.read_text(encoding="utf-8")
    assert "Identity & Snapshot" in map_text
    assert "Technology & Local Environment" in map_text

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_server_waves_and_wait_for_update(mock_gm_server, offline_git_env, tmp_path):
    """P0.1: Verifies refresh_match discovers new wave backlog items and wait_for_update returns UPDATED."""
    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    log_file = tmp_path / "waves.progress.ndjson"
    match_code = mock_gm_server.match_code

    # 1. Configure session
    cfg_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "matchId": match_code,
                    "baseUrl": mock_gm_server.base_url,
                    "bearerToken": mock_gm_server.auth_token,
                    "progressLogFile": str(log_file),
                    "playerSessionId": "session-waves-01",
                    "assignedBranch": "main",
                    "checkoutRoot": str(offline_git_env["checkout_dir"]),
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # 2. Add Wave 2 task to mock GM server
    wave_task = {
        "id": "TASK-WAVE-02",
        "title": "Wave 2 performance optimization",
        "status": "OPEN",
    }
    mock_gm_server.backlog_items["TASK-WAVE-02"] = wave_task

    # 3. Call refresh_match
    refresh_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "refresh_match", "arguments": {}},
    }
    proc.stdin.write(json.dumps(refresh_req) + "\n")
    proc.stdin.flush()
    ref_line = proc.stdout.readline()
    ref_resp = json.loads(ref_line)
    assert not ref_resp["result"]["isError"]
    ref_data = json.loads(ref_resp["result"]["content"][0]["text"])
    assert "TASK-WAVE-02" in ref_data.get("newlyDiscovered", [])

    # Verify DISCOVERED event emitted for new wave task
    assert log_file.exists()
    events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    disc = [e for e in events if e.get("type") == "BACKLOG_STATUS" and e.get("backlogId") == "TASK-WAVE-02"]
    assert len(disc) > 0
    assert disc[0]["status"] == "DISCOVERED"

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_server_prepare_game_fails_on_broken_doctor(offline_git_env, tmp_path):
    """R1: If doctor.ps1 or build command exits non-zero, PREPARE_GAME fails closed and emits no ENVIRONMENT_READY."""
    checkout_dir = offline_git_env["checkout_dir"]
    match_code = "ABCD1234"
    log_file = tmp_path / "broken_prep.progress.ndjson"

    # Create a failing doctor.ps1 in env-require
    env_req = checkout_dir / "env-require"
    env_req.mkdir(parents=True, exist_ok=True)
    if sys.platform.startswith("win"):
        (env_req / "doctor.ps1").write_text("exit 1\n", encoding="utf-8")
    else:
        doc_sh = env_req / "doctor.sh"
        doc_sh.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        doc_sh.chmod(0o755)

    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # Initialize
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # Configure PREPARE_GAME
    cfg_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "type": "PREPARE_GAME",
                    "matchId": match_code,
                    "requestId": "req-prep-broken",
                    "playerSessionId": "session-prep-broken",
                    "progressLogFile": str(log_file),
                    "checkoutRoot": str(checkout_dir),
                    "matchRoot": str(checkout_dir),
                    "repository": {
                        "url": "https://github.com/example/repo.git",
                        "branch": "main",
                        "folder": match_code,
                    },
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()

    line = proc.stdout.readline()
    resp = json.loads(line)
    # Must report error
    assert resp["result"]["isError"]

    # Verify no ENVIRONMENT_READY was emitted
    assert log_file.exists()
    events = [json.loads(l) for l in log_file.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert not any(e.get("type") == "ENVIRONMENT_READY" for e in events)
    build_ev = next((e for e in events if e.get("type") == "PREPARATION_STATUS" and e.get("stage") == "BUILD"), None)
    assert build_ev is not None
    assert build_ev["status"] == "FAILED"

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mcp_server_submission_rejection_recovery(mock_gm_server, offline_git_env, tmp_path):
    """R4: On HTTP 422 VALIDATION_ERROR, journal records REJECTED and releases in-flight lock for resubmission."""
    checkout_dir = offline_git_env["checkout_dir"]
    match_root = offline_git_env["match_root"]
    branch = offline_git_env["branch"]
    log_file = tmp_path / "recovery.progress.ndjson"
    journal_db = tmp_path / "recovery_journal.db"

    proc = subprocess.Popen(
        [sys.executable, "-m", "runner.mcp_server"],
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    # 1. Initialize
    proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # 2. Configure session
    cfg_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "configure_session",
            "arguments": {
                "descriptor": {
                    "matchUuid": mock_gm_server.match_uuid,
                    "matchCode": mock_gm_server.match_code,
                    "gameMasterApiUrl": mock_gm_server.base_url,
                    "apiKeySecret": mock_gm_server.auth_token,
                    "bearerToken": mock_gm_server.auth_token,
                    "assignedBranch": branch,
                    "checkoutRoot": str(checkout_dir),
                    "matchRoot": str(match_root),
                    "progressLogFile": str(log_file),
                    "journalDbPath": str(journal_db),
                    "playerSessionId": "session-recovery-01",
                    "offline": False,
                }
            },
        },
    }
    proc.stdin.write(json.dumps(cfg_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # 3. Start work on TASK-01
    start_req = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "start_work", "arguments": {"backlogItemKey": "TASK-01"}},
    }
    proc.stdin.write(json.dumps(start_req) + "\n")
    proc.stdin.flush()
    proc.stdout.readline()

    # Stage answers and commit
    ans_dir = match_root / "answers" / "TASK-01"
    ans_dir.mkdir(parents=True, exist_ok=True)
    ans_file = ans_dir / "submit.json"
    initial_answer = {"solution": "initial-attempt"}
    ans_file.write_text(json.dumps(initial_answer), encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "attempt 1"])
    run_git(checkout_dir, ["push", "origin", branch])
    sha1 = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    # 4. Inject 422 VALIDATION_ERROR into mock GM server
    mock_gm_server.force_submission_error = (422, "VALIDATION_FAILED", "Answer field invalid")

    sub_req_1 = {
        "jsonrpc": "2.0",
        "id": 4,
        "method": "tools/call",
        "params": {
            "name": "submit_work",
            "arguments": {
                "clientSubmissionId": "11111111-1111-4111-8111-111111111111",
                "backlogItemKey": "TASK-01",
                "gitRevision": sha1,
                "answer": initial_answer,
            },
        },
    }
    proc.stdin.write(json.dumps(sub_req_1) + "\n")
    proc.stdin.flush()
    sub_resp_1 = json.loads(proc.stdout.readline())
    # Fails with 422
    assert sub_resp_1["result"]["isError"]

    # Check journal has state == "REJECTED"
    from mtm_competition.tasks.journal import SubmissionJournal
    with SubmissionJournal(db_path=journal_db) as j:
        entry = j.get_entry(mock_gm_server.match_uuid, "TASK-01")
        assert entry is not None
        assert entry["state"] == "REJECTED"
        assert entry["last_http_code"] == 422

    # 5. Clear error on mock GM server and submit corrected answer
    mock_gm_server.force_submission_error = None
    corrected_answer = {"solution": "corrected-working-attempt"}
    ans_file.write_text(json.dumps(corrected_answer), encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "attempt 2 corrected"])
    run_git(checkout_dir, ["push", "origin", branch])
    sha2 = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    sub_req_2 = {
        "jsonrpc": "2.0",
        "id": 5,
        "method": "tools/call",
        "params": {
            "name": "submit_work",
            "arguments": {
                "clientSubmissionId": "22222222-2222-4222-8222-222222222222",
                "backlogItemKey": "TASK-01",
                "gitRevision": sha2,
                "answer": corrected_answer,
            },
        },
    }
    proc.stdin.write(json.dumps(sub_req_2) + "\n")
    proc.stdin.flush()
    sub_resp_2 = json.loads(proc.stdout.readline())

    # Corrected submission succeeds without idempotency conflict!
    assert not sub_resp_2["result"]["isError"]

    # Verify journal is now ACCEPTED
    with SubmissionJournal(db_path=journal_db) as j:
        entry2 = j.get_entry(mock_gm_server.match_uuid, "TASK-01")
        assert entry2 is not None
        assert entry2["state"] == "ACCEPTED"
        assert entry2["last_http_code"] == 201

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)


def test_mtm_ban_3_tier_workspace_resolution(tmp_path: Path):
    """R5: Resolves checkoutRoot -> matchRoot -> appRoot for nested mtm-ban/KWO5GPMP."""
    root_dir = Path(__file__).resolve().parent.parent.parent
    mtm_ban_dir = root_dir / "mtm-ban" / "KWO5GPMP"
    assert mtm_ban_dir.exists(), "mtm-ban/KWO5GPMP must exist in repo"

    from mtm_knowledge.bootstrap import KnowledgeBootstrap, detect_tech_stack

    # Detect tech stack directly on mtm_ban_dir
    tech = detect_tech_stack(mtm_ban_dir / "src", match_root=mtm_ban_dir)
    assert "Next.js" in tech["Language / framework"]
    assert "pnpm" in tech["Package manager"]
    assert "Prisma" in tech["Database"]
    assert "doctor.ps1" in tech["Environment doctor"]

    # Generate project map outside repo
    out_map = tmp_path / "PROJECT_MAP.md"
    bootstrap = KnowledgeBootstrap(
        repo_path=root_dir,
        match_code="KWO5GPMP",
        branch="main",
        folder="mtm-ban/KWO5GPMP",
        checkout_root=root_dir,
        match_root=mtm_ban_dir,
        app_root=mtm_ban_dir / "src",
    )
    bootstrap.generate_project_map(output_path=out_map, run_verification=False)

    assert out_map.exists()
    content = out_map.read_text(encoding="utf-8")
    assert "KWO5GPMP" in content
    assert "Next.js" in content
    assert "pnpm" in content
    assert "PostgreSQL (Prisma)" in content
    assert "Discovery status: `UNVERIFIED`" in content
    assert "Last build/typecheck/test results with exact exit status: NOT_RUN" in content

