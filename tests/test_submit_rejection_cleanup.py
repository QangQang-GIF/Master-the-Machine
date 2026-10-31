import pytest
from pathlib import Path
from unittest.mock import MagicMock
from mtm_competition.scheduling.native import NativeTaskCoordinator


def test_reconcile_worker_integrating_phase_to_closed(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")

    import subprocess
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH")
    c.register_projection({
        "status": "RUNNING",
        "server_epoch": 1000,
        "remaining_seconds": 2400,
        "items": [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100}],
    })
    c.register_started("BUG-02", {})
    claim = c.claim_task("BUG-02", "worker-1")
    assert c.task_details("BUG-02")["phase"] == "RUNNING"

    # Record worker result to move to READY_TO_INTEGRATE
    w_res = {
        "task_id": "BUG-02",
        "worker_id": "worker-1",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker"],
        "changed_files": ["main.py"],
        "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}],
        "evidence_paths": [],
        "elapsed_seconds": 10,
    }
    c.record_worker_result("BUG-02", w_res)
    assert c.task_details("BUG-02")["phase"] == "READY_TO_INTEGRATE"

    # Begin integration
    c.begin_integration("BUG-02")
    assert c.task_details("BUG-02")["phase"] == "INTEGRATING"
    with c._db() as db:
        assert c._meta(db, "integration_owner") == "BUG-02"

    # Reconcile with requeue=False (terminal rejection)
    res = c.reconcile_worker("BUG-02", confirmed_stopped=True, reason="GM_TERMINAL_REJECTED_409: Closed", requeue=False)
    assert res["phase"] == "CLOSED"
    assert c.task_details("BUG-02")["phase"] == "CLOSED"

    # Verify integration owner was released
    with c._db() as db:
        assert c._meta(db, "integration_owner") is None

    # Verify availableSlots is restored (BUG-02 is not active)
    plan = c.plan_tasks()
    assert not any(a["key"] == "BUG-02" for a in plan["active"])
    assert plan["availableSlots"] == 4


def test_mcp_server_submit_work_terminal_rejection_cleanup(tmp_path):
    from runner.mcp_server import MCPHelperServer

    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")

    import subprocess
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "branch", "-M", "main"], check=True, capture_output=True)

    server = MCPHelperServer()
    server.workspace_root = tmp_path
    server.checkout_root = repo
    server.match_root = repo / "source"
    server.match_uuid = "test-match-uuid"
    server.player_session_id = "test-session"
    server.assigned_branch = "main"

    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH")
    c.register_projection({
        "status": "RUNNING",
        "server_epoch": 1000,
        "remaining_seconds": 2400,
        "items": [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100}],
    })
    c.register_started("BUG-02", {})
    c.claim_task("BUG-02", "worker-1")

    w_res = {
        "task_id": "BUG-02",
        "worker_id": "worker-1",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker"],
        "changed_files": ["main.py"],
        "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}],
        "evidence_paths": [],
        "elapsed_seconds": 10,
    }
    c.record_worker_result("BUG-02", w_res)

    server.native = c
    server.adapter = MagicMock()
    server.adapter.api_base_url = "http://fake-gm"
    import time
    server.adapter.server_epoch = 1000
    server.adapter.server_clock_monotonic = time.monotonic()
    server.adapter.fetch_public_match.return_value = {
        "status": "RUNNING",
        "remainingSeconds": 2400,
        "items": [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100}],
    }
    server.git_guard = MagicMock()
    server.journal = MagicMock()
    server.telemetry = MagicMock()

    # Configure adapter to raise 409 BACKLOG_ITEM_CLOSED
    class GMTerminalError(Exception):
        status_code = 409
        code = "BACKLOG_ITEM_CLOSED"

    server.adapter.submit_work.side_effect = GMTerminalError("409: [BACKLOG_ITEM_CLOSED] Item deadline elapsed")

    answer = {"status": "PARTIAL"}
    commit_sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()

    with pytest.raises(GMTerminalError):
        server.submit_work(
            client_submission_id="sub-01",
            backlog_item_key="BUG-02",
            git_revision=commit_sha,
            answer=answer,
        )

    # Assert coordinator cleaned up BUG-02
    assert c.task_details("BUG-02")["phase"] == "CLOSED"
    with c._db() as db:
        assert c._meta(db, "integration_owner") is None

    # Assert telemetry emitted CLOSED
    server.telemetry.emit_backlog_status.assert_called_with(
        backlog_id="BUG-02",
        title=server._get_backlog_title("BUG-02"),
        status="CLOSED",
        summary=server.telemetry.emit_backlog_status.call_args.kwargs["summary"],
    )
