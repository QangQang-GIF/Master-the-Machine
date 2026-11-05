"""DONE-first: an answer that is not DONE, or that the local gate rejects, goes back to a worker while
there is time; nothing reaches Game Master until it is DONE (or the deadline/match-end path fires)."""
import json
from pathlib import Path

import pytest

from tests.test_dispatch_finalize import build, git, item, submitted_answer, worker_writes


@pytest.fixture(autouse=True)
def _done_first(monkeypatch):
    monkeypatch.delenv("MTM_DONE_FIRST", raising=False)
    monkeypatch.delenv("MTM_DONE_FIRST_RETRIES", raising=False)
    monkeypatch.delenv("MTM_FIX_MIN_SECONDS", raising=False)


def test_partial_with_time_left_is_sent_back_and_resumed_then_submitted_done(tmp_path):
    s = build(tmp_path, [item("TASK-01")], remaining=1200)
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",))              # tc-02.png missing -> PARTIAL
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["status"] == "SENT_BACK_FOR_DONE" and out["fixAttempt"] == 1
    assert "TC-02" in out["reason"]
    assert s.adapter.calls == []                                 # nothing submitted
    assert s.native.task_details("TASK-01")["phase"] == "QUEUED"

    again = s.dispatch_ready()["spawn"]
    assert [e["backlogItemKey"] for e in again] == ["TASK-01"]
    env = again[0]
    fix = env["taskEnvelope"]["fix_request"]
    match = Path(env["matchRoot"])
    # The new attempt starts from the previous attempt's commit, so earlier work is kept.
    assert (match / "README.md").read_text() == "After\n"
    assert (match / "answers" / "TASK-01" / "evidence" / "check.txt").is_file()
    assert fix["fixAttempt"] == 1 and fix["notRun"] == ["TC-02"] and fix["resumeFrom"]

    worker_writes(env)                                           # now both tests pass with evidence
    done = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert done["status"] == "SUBMITTED" and done["answerStatus"] == "DONE"
    assert submitted_answer(s)["status"] == "DONE" and len(s.adapter.calls) == 1


def test_deadline_forced_finalize_still_submits_partial(tmp_path):
    s = build(tmp_path, [item("TASK-01")], remaining=1200)
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",))
    out = s.finalize_item("TASK-01", force_partial=True, confirmed_stopped=True)
    assert out["status"] == "SUBMITTED" and out["answerStatus"] == "PARTIAL"


def test_no_time_left_submits_honest_partial(tmp_path):
    s = build(tmp_path, [item("TASK-01")], remaining=400)        # 400 - 240 margin < 300s fix window
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",))
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["status"] == "SUBMITTED" and out["answerStatus"] == "PARTIAL"


def test_retry_cap_then_partial_is_submitted(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_DONE_FIRST_RETRIES", "1")
    s = build(tmp_path, [item("TASK-01")], remaining=1200)
    worker_writes(s.dispatch_ready()["spawn"][0], evidence=("check.txt",))
    assert s.finalize_item("TASK-01", confirmed_stopped=True)["status"] == "SENT_BACK_FOR_DONE"
    worker_writes(s.dispatch_ready()["spawn"][0], evidence=("check.txt",))
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["status"] == "SUBMITTED" and out["answerStatus"] == "PARTIAL"


def test_pi_partial_is_not_held_back(tmp_path):
    s = build(tmp_path, [item("PI-01")], remaining=1200)
    worker_writes(s.dispatch_ready()["spawn"][0], evidence=("check.txt",))
    out = s.finalize_item("PI-01", confirmed_stopped=True)
    assert out["status"] == "SUBMITTED"


def test_disabled_flag_restores_immediate_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_DONE_FIRST", "0")
    s = build(tmp_path, [item("TASK-01")], remaining=1200)
    worker_writes(s.dispatch_ready()["spawn"][0], evidence=("check.txt",))
    assert s.finalize_item("TASK-01", confirmed_stopped=True)["status"] == "SUBMITTED"


def test_gate_rejection_is_sent_back_with_the_gate_error(tmp_path, monkeypatch):
    from mtm_competition.integration.git_guard import SubmissionGateError
    s = build(tmp_path, [item("TASK-01")], remaining=1200)
    worker_writes(s.dispatch_ready()["spawn"][0])                # a DONE answer ...
    def reject(**_):
        raise SubmissionGateError("Missing required evidence file 'tc-03.png' for task TASK-01")
    monkeypatch.setattr(s.git_guard, "validate_pre_submission", reject)   # ... that the local gate rejects
    out = s.finalize_item("TASK-01", confirmed_stopped=True)
    assert out["status"] == "SENT_BACK_FOR_DONE" and "tc-03.png" in out["reason"]
    assert s.adapter.calls == []
    fix = s.dispatch_ready()["spawn"][0]["taskEnvelope"]["fix_request"]
    assert "tc-03.png" in fix["gateError"]
    with s.native._db() as db:
        assert s.native._meta(db, "integration_owner") is None
