"""V2 deadline escalation and evidence-first completion policy (Plan V2 section 9).

Pure functions. Behaviour states are *control* states layered over (never replacing) the backlog
lifecycle. Time values are conservative estimates to be calibrated by benchmarks; nothing here
claims verification that did not happen.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

WARNING_MARKS = (300, 180, 90)  # seconds before the official deadline; configurable
STATES = ("NORMAL", "DEADLINE_WARNING", "CODE_FREEZE", "EVIDENCE_PRIORITY", "HANDOFF", "FINALIZATION")
_ORDER = {s: i for i, s in enumerate(STATES)}


@dataclass(frozen=True)
class RemainingWork:
    verification_seconds: float = 0.0   # unfinished required verification
    evidence_seconds: float = 0.0       # unfinished required evidence capture
    commit_seconds: float = 10.0
    integration_seconds: float = 20.0
    submission_seconds: float = 15.0
    safety_margin_seconds: float = 20.0

    def required_remaining_time(self) -> float:
        return (self.verification_seconds + self.evidence_seconds + self.commit_seconds
                + self.integration_seconds + self.submission_seconds + self.safety_margin_seconds)


def required_remaining_time(work: RemainingWork) -> float:
    return work.required_remaining_time()


def should_freeze_code(seconds_left: float, work: RemainingWork) -> bool:
    """Dynamic code freeze: freeze when the time left no longer exceeds what finishing requires."""
    return seconds_left <= work.required_remaining_time()


def warning_marks_crossed(seconds_left: float, marks: Tuple[int, ...] = WARNING_MARKS) -> List[int]:
    """All fixed warning marks already crossed (largest first)."""
    return [m for m in sorted(marks, reverse=True) if 0 < seconds_left <= m]


def deadline_state(seconds_left: float, work: RemainingWork, marks: Tuple[int, ...] = WARNING_MARKS,
                   handoff_started: bool = False, finalizing: bool = False) -> str:
    """Behaviour state for an active item. Never moves backwards within one item's run."""
    if finalizing:
        return "FINALIZATION"
    if handoff_started:
        return "HANDOFF"
    if seconds_left <= 0:
        return "HANDOFF"
    freeze = should_freeze_code(seconds_left, work)
    if freeze and work.evidence_seconds + work.verification_seconds > 0:
        return "EVIDENCE_PRIORITY"
    if freeze:
        return "CODE_FREEZE"
    if warning_marks_crossed(seconds_left, marks):
        return "DEADLINE_WARNING"
    return "NORMAL"


def state_at_least(current: str, minimum: str) -> bool:
    return _ORDER[current] >= _ORDER[minimum]


def build_control_instruction(backlog_item_key: str, server_deadline_epoch: Optional[float], state: str,
                              instruction_id: str) -> Dict[str, Any]:
    """Identified control message Root sends to a worker. Delivery/ack is tracked by the durable events table."""
    if state not in STATES or state == "NORMAL":
        raise ValueError("control instruction requires a non-NORMAL deadline state")
    actions = {
        "DEADLINE_WARNING": ["reassess whether the item can still be completed", "prefer finishing available evidence"],
        "CODE_FREEZE": ["stop nonessential coding", "complete required verification", "commit and hand off"],
        "EVIDENCE_PRIORITY": ["stop nonessential coding", "complete required verification",
                              "finish available evidence", "commit and hand off"],
        "HANDOFF": ["commit what exists", "write honest worker_result", "stop writing files"],
        "FINALIZATION": ["do not write submit.json", "wait for Root"],
    }[state]
    return {
        "type": "CODE_FREEZE_REQUESTED" if state in ("CODE_FREEZE", "EVIDENCE_PRIORITY") else f"DEADLINE_{state}",
        "instructionId": instruction_id, "backlogItemKey": backlog_item_key,
        "serverDeadlineEpoch": server_deadline_epoch, "state": state,
        "priority": "EVIDENCE" if state in ("CODE_FREEZE", "EVIDENCE_PRIORITY") else "NORMAL",
        "requiredActions": actions,
    }


def submit_decision(official_type: str, all_required_verified: bool, required_evidence_present: bool,
                    contract_allows_partial: bool = False, match_open: bool = True,
                    already_accepted: bool = False) -> Dict[str, Any]:
    """Whether Root may submit (Plan 9.5). Only Root calls this; it never grants PASS."""
    otype = str(official_type).upper()
    if already_accepted:
        return {"allow": False, "reason": "item already ACCEPTED; no duplicate submission"}
    if not match_open:
        return {"allow": False, "reason": "Game Master is CLOSED/PAUSED/FINISHED"}
    complete = all_required_verified and required_evidence_present
    if complete:
        return {"allow": True, "reason": "all required verification and evidence present"}
    if otype in ("CR", "BUG"):
        return {"allow": False, "reason": "CR/BUG require full MVS/evidence; no forced PARTIAL",
                "action": "preserve honest artifacts and record blocker"}
    if otype in ("TASK", "PI") and contract_allows_partial:
        return {"allow": True, "partial": True, "reason": "official contract allows a valid partial result"}
    return {"allow": False, "reason": "required items missing and the item's contract does not allow partial submission"}
