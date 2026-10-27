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

    proc.stdin.close()
    proc.terminate()
    proc.wait(timeout=5)
