"""V2 Phase 1 safety regressions (fail-closed submission, claim guards, integration lease)."""
import subprocess
import time
from unittest.mock import MagicMock

import pytest

from mtm_competition.integration.git_guard import SubmissionGateError
from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator


def _repo(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for args in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"],
                 ["add", "."], ["commit", "-m", "init"], ["branch", "-M", "main"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    return repo


def _coordinator(tmp_path, items, **kw):
    repo = _repo(tmp_path)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", **kw)
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400, "items": items})
    return repo, c


def _ready_result(key, worker):
    return {"task_id": key, "worker_id": worker, "status": "READY_FOR_REVIEW", "skill_ids": ["x"],
            "changed_files": ["main.py"], "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}],
            "evidence_paths": [], "elapsed_seconds": 1}


def test_submit_work_is_fail_closed_when_gate_rejects(tmp_path):
    """P0: a gate rejection must raise; nothing is journaled, healed, forced to PARTIAL or sent."""
    from runner.mcp_server import MCPHelperServer

    repo, c = _coordinator(tmp_path, [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100}],
                           max_workers=4, normal_workers=3)
    c.register_started("BUG-02", {})
    c.claim_task("BUG-02", "worker-1")
    c.record_worker_result("BUG-02", _ready_result("BUG-02", "worker-1"))

    server = MCPHelperServer()
    server.workspace_root, server.checkout_root = tmp_path, repo
    server.match_root, server.match_uuid = repo / "source", "uuid"
    server.player_session_id, server.assigned_branch = "s", "main"
    server.native = c
    server.descriptor = {"offline": True}
    server.adapter = MagicMock()
    server.adapter.api_base_url = "http://gm"
    server.adapter.server_epoch = 1000
    server.adapter.server_clock_monotonic = time.monotonic()
    server.adapter.fetch_public_match.return_value = {
        "status": "RUNNING", "remainingSeconds": 2400,
        "items": [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100}]}
    server.journal = MagicMock()
    server.telemetry = MagicMock()
    server.git_guard = MagicMock()
    server.git_guard.validate_pre_submission.side_effect = SubmissionGateError("evidence missing")

    answer = {"status": "DONE"}
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True, capture_output=True, text=True).stdout.strip()
    with pytest.raises(SubmissionGateError):
        server.submit_work(client_submission_id="sub-1", backlog_item_key="BUG-02", git_revision=head, answer=answer)

    assert server.git_guard.validate_pre_submission.call_count == 1  # no ignored second validation
    server.adapter.submit_work.assert_not_called()
    server.journal.record_in_flight.assert_not_called()
    assert answer["status"] == "DONE"  # not silently downgraded to PARTIAL


def test_claim_outside_planner_requires_dependencies(tmp_path):
    items = [
        {"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100},
        {"key": "CR-02", "type": "CR", "status": "OPEN", "points": 100, "dependencies": ["CR-01"]},
    ]
    _, c = _coordinator(tmp_path, items)
    c.register_started("CR-02", {})
    with pytest.raises(NativeCoordinationError, match="not eligible"):
        c.claim_task("CR-02", "worker-1")


def test_claim_rejected_when_clock_is_not_running(tmp_path):
    _, c = _coordinator(tmp_path, [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}])
    c.register_started("BUG-01", {})
    c.register_projection({"status": "PAUSED", "server_epoch": 1000, "remaining_seconds": 2400,
                           "items": [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}]})
    with pytest.raises(NativeCoordinationError):
        c.claim_task("BUG-01", "worker-1")


def test_integration_lock_has_lease_and_is_not_reclaimed_while_submit_inflight(tmp_path):
    _, c = _coordinator(tmp_path, [
        {"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100},
        {"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 80}], max_workers=4, normal_workers=3)
    c.register_started("CR-01", {})
    c.claim_task("CR-01", "w1")
    c.record_worker_result("CR-01", _ready_result("CR-01", "w1"))
    c.begin_integration("CR-01")
    with c._db() as db:
        assert c._meta(db, "integration_acquired_at") is not None  # lease timestamp always written
    c.set_submit_inflight("CR-01", True)
    with c._db() as db:
        c._set_meta(db, "integration_acquired_at", time.monotonic() - 60)  # long past the 8s ceiling
    plan = c.plan_tasks()
    assert plan["integrationOwner"] == "CR-01"  # push/submit in flight -> never stolen

    c.set_submit_inflight("CR-01", False)
    plan = c.plan_tasks()
    assert plan["integrationOwner"] is None  # stalled and not in flight -> recoverable


def test_set_submit_inflight_requires_lock_ownership(tmp_path):
    _, c = _coordinator(tmp_path, [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}])
    with pytest.raises(NativeCoordinationError):
        c.set_submit_inflight("BUG-01", True)


def test_no_blanket_git_add_in_submit_path():
    import inspect
    import runner.mcp_server as m
    src = inspect.getsource(m.MCPHelperServer.submit_work)
    assert '"add", "-A"' not in src
    assert "Auto-heal" not in src
