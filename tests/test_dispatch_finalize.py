"""dispatch_ready (A), event-driven wait_for_update (B) and finalize_item (C) without a model or live GM."""
import json
import subprocess
import time
from pathlib import Path

import pytest

from runner.mcp_server import MCPHelperServer
from mtm_competition.scheduling.native import NativeTaskCoordinator
from mtm_competition.integration.git_guard import PreSubmissionGitGuard, SubmissionGateError
from mtm_competition.tasks.journal import SubmissionJournal


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Board:
    api_base_url = "http://localhost"
    server_epoch = 1900000000.0

    def __init__(self, items, remaining=1200):
        self.server_clock_monotonic = time.monotonic()
        self.items = items
        self.remaining = remaining
        self.calls = []
        self.started = []
        self.fail_start = set()

    def get_match_projection(self, _):
        return {"matchUuid": "11111111-2222-3333-4444-555555555555", "matchCode": "TEST1234",
                "status": "RUNNING", "remainingSec": self.remaining, "backlogItems": list(self.items)}

    def start_working(self, backlog_item_key=None, **_):
        if backlog_item_key in self.fail_start:
            raise RuntimeError("start refused")
        self.started.append(backlog_item_key)
        return {"submitTemplate": {"status": "DONE",
                                   "testResults": [{"id": "TC-01", "result": "NOT_RUN", "evidence": ["evidence/check.txt"]},
                                                   {"id": "TC-02", "result": "NOT_RUN", "evidence": ["evidence/tc-02.png"]}],
                                   "evidenceFiles": ["evidence/check.txt", "evidence/tc-02.png"]},
                "files": {f"challenge/{backlog_item_key}.md": f"# {backlog_item_key}\nDo it."}}

    def submit_work(self, **kwargs):
        self.calls.append(kwargs)
        return {"submission": {"id": f"receipt-{len(self.calls)}"}}


def item(key, **kw):
    return {"key": key, "type": key.split("-")[0], "status": "OPEN", "points": 10, **kw}


def build(tmp_path, items, lanes=True, remaining=1200):
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Native test")
    (repo / "project").mkdir()
    (repo / "project" / "README.md").write_text("Before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")
    server = MCPHelperServer(workspace_root=tmp_path)
    server.checkout_root, server.match_root = repo, repo / "project"
    server.match_code, server.match_uuid = "TEST1234", "11111111-2222-3333-4444-555555555555"
    server.assigned_branch = "main"
    server.adapter = Board(items, remaining)
    server.git_guard = PreSubmissionGitGuard(repo)
    server.journal = SubmissionJournal(tmp_path / "journal.sqlite3")
    opts = dict(max_workers=5, normal_workers=3, reserved_workers=2) if lanes else {}
    server.native = NativeTaskCoordinator(tmp_path / "native", repo, repo / "project", "project", "TEST1234", **opts)
    return server


# ---------------------------------------------------------------- A: dispatch_ready

def test_dispatch_ready_starts_and_claims_all_selected(tmp_path):
    s = build(tmp_path, [item("BUG-01"), item("CR-01"), item("TASK-01"), item("CR-02"), item("PI-01")])
    out = s.dispatch_ready()
    spawned = {e["backlogItemKey"]: e for e in out["spawn"]}
    assert set(spawned) == {"BUG-01", "CR-01", "TASK-01", "PI-01"}
    assert spawned["PI-01"]["lane"] == "reserved"
    assert {spawned[k]["lane"] for k in ("BUG-01", "CR-01", "TASK-01")} == {"normal"}
    for e in out["spawn"]:
        assert Path(e["matchRoot"]).is_dir()
        assert e["taskEnvelope"]["task_id"] == e["backlogItemKey"]
        assert e["workerId"] == f"w-{e['backlogItemKey']}-1"
    assert out["errors"] == []
    assert out["lanes"]["normal"]["running"] == 3 and out["lanes"]["reserved"]["running"] == 1
    # Nothing new to hand out on a second call.
    assert s.dispatch_ready()["spawn"] == []


def test_dispatch_ready_isolates_start_failures(tmp_path):
    s = build(tmp_path, [item("BUG-01"), item("CR-01")])
    s.adapter.fail_start = {"BUG-01"}
    out = s.dispatch_ready()
    assert [e["backlogItemKey"] for e in out["spawn"]] == ["CR-01"]
    assert out["errors"][0]["backlogItemKey"] == "BUG-01"


def test_dispatch_ready_is_registered_tool():
    from runner.mcp_server import get_tool_definitions
    names = {t["name"] for t in get_tool_definitions()}
    assert {"dispatch_ready", "finalize_item"} <= names


# ---------------------------------------------------------------- helpers for worker output

def worker_writes(entry, *, readme="After\n", evidence=("check.txt", "tc-02.png"), status="DONE",
                  result_file=True, commit=True):
    match = Path(entry["matchRoot"])
    worktree = Path(entry["worktreeRoot"])
    key = entry["backlogItemKey"]
    (match / "README.md").write_text(readme)
    ev_dir = match / "answers" / key / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    for name in evidence:
        (ev_dir / name).write_text(f"real output for {name}\n")
    answer = {"status": status,
              "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/check.txt"]},
                              {"id": "TC-02", "result": "PASS", "evidence": ["evidence/tc-02.png"]}],
              "evidenceFiles": ["evidence/check.txt", "evidence/tc-02.png"]}
    (match / "answers" / key / "submit.json").write_text(json.dumps(answer))
    if commit:
        git(worktree, "add", "-A", "project/README.md", f"project/answers/{key}")
        git(worktree, "commit", "-m", f"{key}: work")
    if result_file:
        (match / "worker_result.json").write_text(json.dumps({
            "task_id": key, "status": "READY_FOR_REVIEW", "skill_ids": ["mtm-task-worker"],
            "changed_files": ["README.md"],
            "test_results": [{"command": "git diff --check", "passed": True, "exit_code": 0}],
            "evidence_paths": [f"answers/{key}/evidence/{n}" for n in evidence], "elapsed_seconds": 3}))
    return match


def submitted_answer(server):
    return server.adapter.calls[-1]["answer"]


# ---------------------------------------------------------------- C: finalize_item

def test_finalize_full_evidence_submits_done(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry)
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["status"] == "SUBMITTED" and out["answerStatus"] == "DONE" and out["notRun"] == []
    assert s.native.task_details("TASK-01")["phase"] == "SUBMITTED"
    assert (s.match_root / "README.md").read_text() == "After\n"
    remote_head = git(s.checkout_root, "ls-remote", "origin", "main").split()[0]
    assert remote_head == git(s.checkout_root, "rev-parse", "HEAD")
    assert s.finalize_item("TASK-01", confirmed_stopped=True)["status"] == "ALREADY_SUBMITTED"
    assert len(s.adapter.calls) == 1


def test_finalize_missing_screenshot_is_not_submitted_fail_closed(tmp_path):
    """V2 (P0): missing required evidence -> NOT_RUN/PARTIAL is honest, but the gate must refuse to submit."""
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",), status="DONE")
    with pytest.raises(SubmissionGateError):
        s.finalize_item("TASK-01", confirmed_stopped=True)
    assert s.adapter.calls == []
    assert s.native.task_details("TASK-01")["phase"] != "SUBMITTED"
    # No placeholder image was fabricated, and the integration lock was released.
    assert not (s.match_root / "answers/TASK-01/evidence/tc-02.png").exists()
    with s.native._db() as db:
        assert s.native._meta(db, "integration_owner") is None


def test_finalize_uncommitted_running_worker_is_snapshotted(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",), commit=False, result_file=False)
    with pytest.raises(SubmissionGateError):
        s.finalize_item("TASK-01", force_partial=True, confirmed_stopped=True)
    assert s.adapter.calls == []
    assert s.native.task_details("TASK-01")["phase"] != "SUBMITTED"
    with s.native._db() as db:
        assert s.native._meta(db, "integration_owner") is None


def test_finalize_conflict_releases_lock_and_keeps_checkout_clean(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, readme="Worker version\n")
    (s.match_root / "README.md").write_text("Root version\n")
    git(s.checkout_root, "commit", "-am", "conflicting root change")
    with pytest.raises(ValueError, match="cherry-pick conflict"):
        s.finalize_item("TASK-01", confirmed_stopped=True)
    assert git(s.checkout_root, "status", "--porcelain", "--untracked-files=no") == ""
    with s.native._db() as db:
        assert s.native._meta(db, "integration_owner") is None
    assert s.adapter.calls == []


def test_finalize_replays_in_flight_submission_exactly(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry)
    original = s.adapter.submit_work

    def flaky(**kwargs):
        s.adapter.submit_work = original
        raise TimeoutError("gateway timeout")
    s.adapter.submit_work = flaky
    with pytest.raises(TimeoutError):
        s.finalize_item("TASK-01", confirmed_stopped=True)
    pending = s.journal.get_in_flight(s.match_uuid, "TASK-01")
    assert pending is not None
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["replayed"] is True
    assert s.adapter.calls[-1]["client_submission_id"] == pending["client_submission_id"]


def test_partial_result_with_missing_required_evidence_is_accepted(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    entry = s.dispatch_ready()["spawn"][0]
    with s.native._db() as db:
        row = db.execute("SELECT data FROM tasks WHERE key='TASK-01'").fetchone()
        data = json.loads(row[0]); data["start_details"]["evidence_required"] = ["answers/TASK-01/evidence/tc-02.png"]
        db.execute("UPDATE tasks SET data=? WHERE key='TASK-01'", (json.dumps(data),))
    res = s.native.record_worker_result("TASK-01", {
        "task_id": "TASK-01", "status": "PARTIAL", "skill_ids": ["x"], "changed_files": [],
        "test_results": [{"command": "playwright capture", "passed": False, "exit_code": 1}],
        "evidence_paths": [], "elapsed_seconds": 1})
    assert res["phase"] == "READY_TO_INTEGRATE"
    assert res["workerResult"]["missing_evidence"] == ["answers/TASK-01/evidence/tc-02.png"]


# ---------------------------------------------------------------- B: event-driven wait_for_update

def test_wait_returns_dispatch_when_slots_free(tmp_path):
    s = build(tmp_path, [item("BUG-01"), item("PI-01")])
    out = s.wait_for_update(5000)
    assert out["status"] == "UPDATED"
    assert {e["backlogItemKey"] for e in out["dispatch"]["spawn"]} == {"BUG-01", "PI-01"}


def test_wait_wakes_on_worker_result_file_once(tmp_path):
    s = build(tmp_path, [item("BUG-01")])
    entry = s.dispatch_ready()["spawn"][0]
    s.wait_for_update(200)  # drain the initial slot_free/new_items events
    worker_writes(entry)
    started = time.time()
    out = s.wait_for_update(10000)
    assert time.time() - started < 3
    assert [e["type"] for e in out["events"]] == ["worker_result_file"]
    assert s.wait_for_update(1500)["status"] == "TIMEOUT"


def test_wait_auto_finalizes_item_near_deadline(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AUTO_FINALIZE_SLACK", "150")
    s = build(tmp_path, [item("TASK-03", remainingSec=140)])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",), commit=False, result_file=False)
    out = s.wait_for_update(5000)
    assert any(e["type"] == "auto_finalize" for e in out["events"])
    fin = out["autoFinalized"][0]
    # V2 (P1): the worker is still running, so nothing is snapshotted; Root must stop it first.
    assert fin["status"] == "STOP_REQUIRED" and fin["stopWorker"] is True
    assert fin["control"]["instructionId"].startswith("stop:TASK-03")
    assert s.adapter.calls == []
    # After Root confirms the stop, the forced-PARTIAL result is still refused by the fail-closed gate.
    with pytest.raises(SubmissionGateError):
        s.finalize_item("TASK-03", force_partial=True, confirmed_stopped=True)
    assert s.adapter.calls == []
