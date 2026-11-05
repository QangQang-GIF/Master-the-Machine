"""V2 safe-stop protocol: request -> confirm -> only then snapshot/integrate."""
import json
import subprocess

import pytest

from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator


def _coord(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=5, normal_workers=4, reserved_workers=1)
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400,
                           "items": [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10, "deadline_epoch": 3000}]})
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    return c


def test_request_stop_is_identified_durable_and_idempotent(tmp_path):
    c = _coord(tmp_path)
    r = c.request_stop("BUG-01", "deadline")
    assert r["alreadyRequested"] is False and r["confirmed"] is False
    assert r["instruction"]["state"] == "HANDOFF" and r["instructionId"] == "stop:BUG-01:1"
    assert c.request_stop("BUG-01", "again")["alreadyRequested"] is True
    assert "stop:BUG-01:1" in [e["eventId"] for e in c.unacked_events()]  # replayable after a crash


def test_slot_stays_occupied_until_stop_is_confirmed(tmp_path):
    c = _coord(tmp_path)
    c.request_stop("BUG-01", "deadline")
    assert [a["key"] for a in c.plan_tasks()["active"]] == ["BUG-01"]
    assert c.is_quiesced("BUG-01") is False


def test_worker_ack_requires_a_request_and_confirmation_acks_the_event(tmp_path):
    c = _coord(tmp_path)
    with pytest.raises(NativeCoordinationError, match="prior stop request"):
        c.confirm_stopped("BUG-01", "worker_ack")
    c.request_stop("BUG-01", "deadline")
    assert c.confirm_stopped("BUG-01", "worker_ack")["confirmed"] is True
    assert c.is_quiesced("BUG-01") is True
    assert "stop:BUG-01:1" not in [e["eventId"] for e in c.unacked_events()]


def test_unknown_source_and_non_running_are_rejected(tmp_path):
    c = _coord(tmp_path)
    with pytest.raises(NativeCoordinationError):
        c.confirm_stopped("BUG-01", "result_file")  # a result file alone is never a confirmation
    c.confirm_stopped("BUG-01", "root_attested")
    c.record_worker_result("BUG-01", {"task_id": "BUG-01", "status": "READY_FOR_REVIEW", "skill_ids": ["x"], "changed_files": [],
                                      "test_results": [], "evidence_paths": [], "elapsed_seconds": 1})
    with pytest.raises(NativeCoordinationError):
        c.request_stop("BUG-01", "late")


def test_finalize_refuses_to_snapshot_a_running_worker_until_stop_confirmed(tmp_path):
    from tests.test_dispatch_finalize import build, item, worker_writes
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry)
    first = s.finalize_item("TASK-01")
    assert first["status"] == "STOP_REQUIRED" and first["stopWorker"] is True
    assert s.adapter.calls == [] and s.native.task_details("TASK-01")["phase"] == "RUNNING"
    wt = s.native.task_details("TASK-01")["worktreeRoot"]
    assert not (s.match_root / "README.md").read_text() == "After\n"  # nothing was cherry-picked
    second = s.finalize_item("TASK-01")
    assert second["status"] == "STOP_PENDING"
    # Worker acknowledges the specific instruction by writing the marker file.
    marker = __import__("pathlib").Path(wt) / s.native.repository_folder / ".mtm_stopped.json"
    marker.write_text(json.dumps({"instructionId": first["control"]["instructionId"]}))
    done = s.finalize_item("TASK-01")
    assert done["status"] == "SUBMITTED"
    assert s.native.stop_state("TASK-01")["source"] == "worker_ack"


def test_ack_with_wrong_instruction_id_is_ignored(tmp_path):
    from pathlib import Path
    from tests.test_dispatch_finalize import build, item, worker_writes
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry)
    s.finalize_item("TASK-01")
    wt = Path(s.native.task_details("TASK-01")["worktreeRoot"])
    (wt / s.native.repository_folder / ".mtm_stopped.json").write_text(json.dumps({"instructionId": "stop:OTHER:1"}))
    assert s.finalize_item("TASK-01")["status"] == "STOP_PENDING"
    assert s.adapter.calls == []
