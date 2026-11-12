"""10-minute per-item time limit (user decision 2026-11-11).

At the limit the helper submits the worker's last commit. A submit refused by a local gate is not sent: the item
goes back to the same worker with the exact error and a grace period; at the end of the grace the last commit is
submitted with the gates relaxed. Works for any item type, with or without a deadline."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_credit_optimizations import Board, git, native_server  # noqa: F401 (fixture)
from tests.test_39pswt5c_fixes import SCHEMA, publish


def _clock(server, remaining):
    server.adapter.get_match_projection = lambda _: {
        "matchUuid": server.match_uuid, "matchCode": "TEST1234", "status": "RUNNING",
        "remainingSec": remaining, "backlogItems": server.adapter.items}
    server.refresh_match()


def _worker_with_answer(server, answer=None):
    _clock(server, 2400)
    server.start_work("TASK-01")
    claim = server.claim_task("TASK-01", "worker-01")
    match, worker = Path(claim["matchRoot"]), Path(claim["worktreeRoot"])
    (match / "README.md").write_text("After\n")
    ev = match / "answers/TASK-01/evidence/check.txt"
    ev.parent.mkdir(parents=True, exist_ok=True)
    ev.write_text("ok\n")
    (match / "answers/TASK-01/submit.json").write_text(json.dumps(answer or {
        "status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/check.txt"]}],
        "evidenceFiles": ["evidence/check.txt"]}))
    git(worker, "add", "project/README.md", "project/answers")
    git(worker, "commit", "-m", "work")
    return claim, match, worker


def test_no_deadline_item_gets_10_minutes_and_is_submitted_at_the_limit(native_server):
    server = native_server
    claim, match, worker = _worker_with_answer(server, {        # not complete: no early submit at 8 min
        "status": "PARTIAL", "testResults": [{"id": "TC-01", "result": "NOT_RUN", "evidence": []}],
        "evidenceFiles": []})
    assert claim["taskEnvelope"]["time_budget_seconds"] == 600
    _clock(server, 2400 - 590)
    assert "timeLimitFlushed" not in server.native.plan_tasks()
    _clock(server, 2400 - 605)
    plan = server.native.plan_tasks()
    assert [r["key"] for r in plan["timeLimitFlushed"]] == ["TASK-01"] and plan["timeLimitFlushed"][0]["final"] is False
    assert not plan["overdueWorkers"]
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED"
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_gate_rejection_returns_the_item_to_its_worker_then_the_grace_end_submits(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(SCHEMA)})
    claim, match, worker = _worker_with_answer(server)            # no auditSummary: schema-invalid
    _clock(server, 2400 - 610)
    assert server.native.plan_tasks()["timeLimitFlushed"][0]["key"] == "TASK-01"
    with pytest.raises(ValueError, match="RETURNED_TO_WORKER") as info:
        server.integrate_and_submit("TASK-01")
    assert "SUBMIT_SCHEMA_INVALID" in str(info.value) and not server.adapter.calls
    assert server.native.task_details("TASK-01")["phase"] == "RUNNING"
    # within the grace (300 s) nothing is flushed again and the worker is not reported overdue
    _clock(server, 2400 - 800)
    plan = server.native.plan_tasks()
    assert "timeLimitFlushed" not in plan and not plan["overdueWorkers"]
    assert [r["key"] for r in plan["active"]] == ["TASK-01"]
    # the grace ends: final flush, gates relaxed (schema autofilled), submitted once
    _clock(server, 2400 - 920)
    plan = server.native.plan_tasks()
    assert plan["timeLimitFlushed"][0]["final"] is True
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and len(server.adapter.calls) == 1
    assert server.adapter.calls[0]["answer"]["status"] == "PARTIAL"


def test_worker_fixes_within_the_grace_and_its_result_is_submitted(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(SCHEMA)})
    claim, match, worker = _worker_with_answer(server)
    _clock(server, 2400 - 610)
    server.native.plan_tasks()
    with pytest.raises(ValueError, match="RETURNED_TO_WORKER"):
        server.integrate_and_submit("TASK-01")
    answer = json.loads((match / "answers/TASK-01/submit.json").read_text())
    answer["auditSummary"] = {"totalRecords": 3, "anomalies": ["late fee applied twice"]}
    (match / "answers/TASK-01/submit.json").write_text(json.dumps(answer))
    git(worker, "add", "project/answers")
    git(worker, "commit", "-m", "fix schema")
    (match / "worker_result.json").write_text(json.dumps({
        "task_id": "TASK-01", "worker_id": "worker-01", "status": "READY_FOR_REVIEW", "skill_ids": [],
        "changed_files": ["answers/TASK-01/submit.json"], "test_results": [],
        "evidence_paths": ["answers/TASK-01/evidence/check.txt"], "elapsed_seconds": 700}))
    server.record_worker_result("TASK-01")
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED"
    assert server.adapter.calls[0]["answer"]["status"] == "DONE"
    assert server.adapter.calls[0]["answer"]["auditSummary"]["totalRecords"] == 3


def test_worker_without_any_work_keeps_going_past_the_limit(native_server):
    server = native_server
    _clock(server, 2400)
    server.start_work("TASK-01")
    server.claim_task("TASK-01", "worker-01")
    _clock(server, 2400 - 700)
    plan = server.native.plan_tasks()
    assert "timeLimitFlushed" not in plan and not plan["overdueWorkers"]
    assert [r["key"] for r in plan["active"]] == ["TASK-01"]


def test_uncommitted_source_edits_are_not_cut_but_an_answers_only_item_is(native_server):
    server = native_server
    _clock(server, 2400)
    server.start_work("TASK-01")
    claim = server.claim_task("TASK-01", "worker-01")
    match = Path(claim["matchRoot"])
    (match / "answers/TASK-01").mkdir(parents=True, exist_ok=True)
    (match / "answers/TASK-01/submit.json").write_text(json.dumps({"status": "DONE"}))
    (match / "README.md").write_text("half-written\n")
    _clock(server, 2400 - 700)
    assert "timeLimitFlushed" not in server.native.plan_tasks()
    (match / "README.md").write_text("Before\n")                # only the answer is left: it is the work
    plan = server.native.plan_tasks()
    assert [r["key"] for r in plan["timeLimitFlushed"]] == ["TASK-01"]


def test_limit_is_configurable_and_can_be_disabled(native_server, monkeypatch):
    server = native_server
    monkeypatch.setenv("MTM_WORKER_MAX_SECONDS", "0")
    from mtm_competition.scheduling.native import NativeTaskCoordinator
    repo = server.checkout_root
    server.native = NativeTaskCoordinator(repo.parent / "native2", repo, repo / "project", "project", "TEST1234")
    _clock(server, 2400)
    server.start_work("TASK-01")
    env = server.claim_task("TASK-01", "worker-01")["taskEnvelope"]
    assert env["time_budget_seconds"] > 2000 and "time_limit_rule" not in env


def test_deadline_flush_still_wins_when_the_deadline_comes_first(native_server):
    server = native_server
    server.adapter.items[0]["deadlineEpoch"] = Board.server_epoch + 400
    claim, match, worker = _worker_with_answer(server)
    assert "time_limit_rule" not in claim["taskEnvelope"]
    server.adapter.items[0]["deadlineEpoch"] = Board.server_epoch + 140
    _clock(server, 2400 - 260)
    plan = server.native.plan_tasks()
    assert [r["key"] for r in plan.get("deadlineFlushed", [])] == ["TASK-01"] and "timeLimitFlushed" not in plan


# --- Early submit at 8 minutes when the answer is already complete ---------------------------------------

def test_complete_answer_is_submitted_at_8_minutes(native_server):
    server = native_server
    claim, match, worker = _worker_with_answer(server)            # DONE, TC-01 PASS with evidence, committed
    _clock(server, 2400 - 470)
    assert "timeLimitFlushed" not in server.native.plan_tasks()
    _clock(server, 2400 - 485)
    plan = server.native.plan_tasks()
    assert plan["timeLimitFlushed"][0]["key"] == "TASK-01" and plan["timeLimitFlushed"][0]["early"] is True
    assert server.integrate_and_submit("TASK-01")["status"] == "ACCEPTED"


@pytest.mark.parametrize("answer, uncommitted", [
    ({"status": "PARTIAL", "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/check.txt"]}],
      "evidenceFiles": []}, False),
    ({"status": "DONE", "testResults": [{"id": "TC-01", "result": "NOT_RUN", "evidence": []}], "evidenceFiles": []}, False),
    ({"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/missing.png"]}],
      "evidenceFiles": []}, False),
    (None, True),                                               # complete, but the worker is still writing
])
def test_incomplete_answer_keeps_working_until_10_minutes(native_server, answer, uncommitted):
    server = native_server
    claim, match, worker = _worker_with_answer(server, answer)
    if uncommitted:
        (match / "README.md").write_text("more\n")
    _clock(server, 2400 - 540)
    assert "timeLimitFlushed" not in server.native.plan_tasks()
    if uncommitted:
        git(worker, "add", "project/README.md")
        git(worker, "commit", "-m", "more")
    _clock(server, 2400 - 605)
    plan = server.native.plan_tasks()
    assert plan["timeLimitFlushed"][0]["key"] == "TASK-01" and "early" not in plan["timeLimitFlushed"][0]


def test_schema_invalid_done_answer_is_not_submitted_early(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(SCHEMA)})
    _worker_with_answer(server)                                   # no auditSummary
    _clock(server, 2400 - 540)
    assert "timeLimitFlushed" not in server.native.plan_tasks()
