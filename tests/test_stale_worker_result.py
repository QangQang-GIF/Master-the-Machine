"""CWQNKAPC: STALE-INHERITED-WORKER-RESULT — a worker_result.json committed by one item must not
make a freshly started worker on another item look finished."""
import json
from pathlib import Path

from tests.test_lane_scheduler import NOW, git, item, make, project, run


def _result_events(c):
    return [e for e in c.poll_events() if e.get("type") == "worker_result_file"]


def test_inherited_result_file_is_removed_and_raises_no_event(tmp_path):
    c = make(tmp_path)
    repo = tmp_path / "checkout"
    # An earlier worker committed its own control files and they were integrated into the base checkout.
    (repo / "source" / "worker_result.json").write_text(json.dumps({"task_id": "BUG-04", "status": "PARTIAL"}))
    (repo / "source" / ".mtm_stopped.json").write_text(json.dumps({"instructionId": "stop:BUG-04:1"}))
    git(repo, "add", "-f", ".")
    git(repo, "commit", "-m", "integrated BUG-04 incl. control files")
    project(c, item("CR-03"))
    env = run(c, "CR-03")["taskEnvelope"]
    match = Path(env["metadata"]["match_root"])
    assert not (match / "worker_result.json").exists()
    assert not (match / ".mtm_stopped.json").exists()
    assert _result_events(c) == []


def test_foreign_result_file_written_later_is_ignored_but_own_result_fires(tmp_path):
    c = make(tmp_path)
    project(c, item("CR-03"))
    match = Path(run(c, "CR-03")["taskEnvelope"]["metadata"]["match_root"])
    (match / "worker_result.json").write_text(json.dumps({"task_id": "BUG-04"}))
    assert _result_events(c) == []
    (match / "worker_result.json").write_text(json.dumps({"task_id": "CR-03"}))
    events = _result_events(c)
    assert [e["backlogItemKey"] for e in events] == ["CR-03"]


def test_unreadable_result_file_is_not_a_finish_signal(tmp_path):
    c = make(tmp_path)
    project(c, item("CR-03"))
    match = Path(run(c, "CR-03")["taskEnvelope"]["metadata"]["match_root"])
    (match / "worker_result.json").write_text("{half written")
    assert _result_events(c) == []
