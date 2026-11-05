"""V2 Phase 4: deadline escalation, dynamic code freeze, evidence-first submit policy."""
import subprocess

import pytest

from mtm_competition.scheduling.deadline_policy import (
    RemainingWork, build_control_instruction, deadline_state, required_remaining_time,
    should_freeze_code, state_at_least, submit_decision, warning_marks_crossed)
from mtm_competition.scheduling.native import NativeTaskCoordinator


def test_required_remaining_time_is_the_sum_of_all_terms():
    w = RemainingWork(verification_seconds=40, evidence_seconds=60, commit_seconds=10,
                      integration_seconds=20, submission_seconds=15, safety_margin_seconds=25)
    assert required_remaining_time(w) == 170


def test_freeze_is_dynamic_not_a_fixed_time():
    light = RemainingWork(evidence_seconds=0, verification_seconds=0)
    heavy = RemainingWork(evidence_seconds=200, verification_seconds=60)
    assert should_freeze_code(120, light) is False   # 120s is plenty for a light item
    assert should_freeze_code(120, heavy) is True    # the same 120s is too little for evidence-heavy work
    assert should_freeze_code(required_remaining_time(heavy), heavy) is True  # boundary: equal -> freeze


def test_fixed_warning_marks():
    assert warning_marks_crossed(400) == []
    assert warning_marks_crossed(250) == [300]
    assert warning_marks_crossed(100) == [300, 180]
    assert warning_marks_crossed(60) == [300, 180, 90]
    assert warning_marks_crossed(0) == []


def test_state_progression():
    w = RemainingWork(evidence_seconds=60, verification_seconds=30)  # needs 155s
    assert deadline_state(1000, w) == "NORMAL"
    assert deadline_state(250, w) == "DEADLINE_WARNING"
    assert deadline_state(150, w) == "EVIDENCE_PRIORITY"
    assert deadline_state(150, RemainingWork()) == "DEADLINE_WARNING"
    assert deadline_state(0, w) == "HANDOFF"
    assert deadline_state(500, w, finalizing=True) == "FINALIZATION"
    assert state_at_least("HANDOFF", "CODE_FREEZE") and not state_at_least("NORMAL", "CODE_FREEZE")


def test_control_instruction_is_identified_and_actionable():
    c = build_control_instruction("BUG-04", 123.0, "EVIDENCE_PRIORITY", "ctl:BUG-04:1:EVIDENCE_PRIORITY")
    assert c["type"] == "CODE_FREEZE_REQUESTED" and c["instructionId"] and c["serverDeadlineEpoch"] == 123.0
    assert "stop nonessential coding" in c["requiredActions"] and c["priority"] == "EVIDENCE"
    with pytest.raises(ValueError):
        build_control_instruction("BUG-04", 1.0, "NORMAL", "x")


@pytest.mark.parametrize("otype", ["CR", "BUG"])
def test_cr_bug_never_submit_partial(otype):
    d = submit_decision(otype, all_required_verified=False, required_evidence_present=True, contract_allows_partial=True)
    assert d["allow"] is False and "PARTIAL" in d["reason"]


def test_task_pi_follow_contract_and_complete_work_is_allowed():
    assert submit_decision("PI", False, False, contract_allows_partial=True)["allow"] is True
    assert submit_decision("TASK", False, False, contract_allows_partial=False)["allow"] is False
    assert submit_decision("BUG", True, True)["allow"] is True


def test_never_submit_when_closed_or_already_accepted():
    assert submit_decision("PI", True, True, match_open=False)["allow"] is False
    assert submit_decision("PI", True, True, already_accepted=True)["allow"] is False


def _coord(tmp_path, **kw):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    return NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH",
                                 max_workers=4, normal_workers=3, **kw)


def test_poll_events_emits_durable_code_freeze_once_when_flag_on(tmp_path):
    items = [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 80, "deadline_epoch": 1_000_900}]
    c = _coord(tmp_path, deadline_control=True)
    c.register_projection({"status": "RUNNING", "server_epoch": 1_000_000, "remaining_seconds": 2400, "items": items})
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    assert not [e for e in c.poll_events() if e["type"] == "deadline_control"]  # far from deadline
    c.register_projection({"status": "RUNNING", "server_epoch": 1_000_800, "remaining_seconds": 1600, "items": items})
    ev = [e for e in c.poll_events() if e["type"] == "deadline_control"]
    assert len(ev) == 1
    assert ev[0]["backlogItemKey"] == "BUG-01"
    assert ev[0]["state"] == "EVIDENCE_PRIORITY"  # 100s left < ~140s needed for verification+evidence+handoff
    assert ev[0]["controlType"] == "CODE_FREEZE_REQUESTED"
    assert not [e for e in c.poll_events() if e["type"] == "deadline_control" and e["state"] == ev[0]["state"]]
    assert ev[0]["eventId"] in [e["eventId"] for e in c.unacked_events()]  # durable until acknowledged
    assert c.ack_event(ev[0]["eventId"])


def test_poll_events_has_no_control_events_when_flag_off(tmp_path):
    items = [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 80, "deadline_epoch": 1_000_900}]
    c = _coord(tmp_path)
    c.register_projection({"status": "RUNNING", "server_epoch": 1_000_000, "remaining_seconds": 2400, "items": items})
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    c.register_projection({"status": "RUNNING", "server_epoch": 1_000_800, "remaining_seconds": 1600, "items": items})
    assert not [e for e in c.poll_events() if e["type"] == "deadline_control"]
