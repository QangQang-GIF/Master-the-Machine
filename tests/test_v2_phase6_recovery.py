"""V2 Phase 6: heartbeat and restart reconciliation."""
import subprocess
import time

import pytest

from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator


def _setup(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=4, normal_workers=3)
    items = [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10},
             {"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 10}]
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400, "items": items})
    return repo, c


def _result(key, worker):
    return {"task_id": key, "worker_id": worker, "status": "READY_FOR_REVIEW", "skill_ids": ["x"], "changed_files": ["main.py"],
            "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}], "evidence_paths": [], "elapsed_seconds": 1}


def test_heartbeat_requires_running_task_and_goes_stale(tmp_path):
    _, c = _setup(tmp_path)
    with pytest.raises(NativeCoordinationError):
        c.record_heartbeat("BUG-01", "coding")
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    assert [s["key"] for s in c.stale_heartbeats(60)] == ["BUG-01"]  # never reported -> stale
    c.record_heartbeat("BUG-01", "coding", checkpoint="cp1")
    assert c.stale_heartbeats(60) == []
    time.sleep(0.05)
    assert [s["key"] for s in c.stale_heartbeats(0.01)] == ["BUG-01"]


def test_heartbeat_fields_are_truncated_not_transcripts(tmp_path):
    _, c = _setup(tmp_path)
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    c.record_heartbeat("BUG-01", "x" * 500, blocker="y" * 5000)
    with c._db() as db:
        hb = c._meta(db, "hb:BUG-01")
    assert len(hb["stage"]) <= 64 and len(hb["blocker"]) <= 200


def test_reconcile_report_recommends_without_acting(tmp_path):
    _, c = _setup(tmp_path)
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    rep = c.reconcile_report()
    t = rep["tasks"][0]
    assert t["key"] == "BUG-01" and t["phase"] == "RUNNING" and t["worktreeExists"] is True
    assert t["action"] == "confirm_native_agent_status_before_any_respawn"
    assert c.task_details("BUG-01")["phase"] == "RUNNING"  # nothing was changed or respawned


def test_in_flight_submission_is_replayed_with_original_identity(tmp_path):
    _, c = _setup(tmp_path)
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    c.record_worker_result("BUG-01", _result("BUG-01", "w1"))
    c.begin_integration("BUG-01")
    c.set_submit_inflight("BUG-01", True)
    rep = c.reconcile_report(inflight_submission_keys=["BUG-01"])
    assert rep["tasks"][0]["action"] == "replay_in_flight_submission_with_original_identity"
    assert rep["releasedOrphanLock"] is None  # lock stays with the in-flight owner
    with c._db() as db:
        assert c._meta(db, "integration_owner") == "BUG-01"


def test_orphan_lock_is_released_only_when_safe(tmp_path):
    _, c = _setup(tmp_path)
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    with c._db() as db:  # simulate a crash that left a lock owned by a task that is not integrating
        c._set_meta(db, "integration_owner", "BUG-01")
    rep = c.reconcile_report()
    assert rep["releasedOrphanLock"] == "BUG-01"
    with c._db() as db:
        assert c._meta(db, "integration_owner") is None
    assert c.task_details("BUG-01")["phase"] == "RUNNING"  # the worker is untouched


def test_report_lists_unacked_events_for_replay(tmp_path):
    _, c = _setup(tmp_path)
    events = c.poll_events()
    assert events
    assert c.reconcile_report()["unackedEvents"] == [e["eventId"] for e in events]
