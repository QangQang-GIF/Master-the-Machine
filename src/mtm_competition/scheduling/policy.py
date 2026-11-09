"""Single submission/rework policy and a single priority function (Solution v3, S2 + S9').

Everything that decides *whether* an item is submitted, reworked, held or closed, and *in which order*
items are dispatched/integrated, lives here. The native coordinator, the MCP server and the Root prompt
only reference these results; they never re-implement the rules.

Values that depend on the official rules or on measurements are configurable through the environment and
are marked as hypotheses in the solution document:

- Every ordinary item (anything that is not a PI) follows the same rules; nothing is classified by type,
  points or size. An ordinary item's honest PARTIAL is submitted at the latest useful moment.
- ``MTM_PI_PARTIAL_SUBMIT`` (default ``0``): whether a PI's honest PARTIAL may be submitted.
- ``MTM_MAX_REWORK`` (default 1): extra attempts after a final result that is not good enough.
- ``MTM_MIN_REWORK_SECONDS`` (default 300): a rework is pointless with less time than this left.
- ``MTM_SUBMIT_P95_DEFAULT_SECONDS`` (default 60) / ``MTM_SUBMIT_SAFETY_MARGIN_SECONDS`` (default 30):
  ``submit_budget = measured_submit_p95 + safety_margin``; the default p95 is used until 3 samples exist.
- ``MTM_HOLD_FOR_REVIEW_SECONDS`` (default 60): maximum automatic review hold for a DONE answer that has
  evidence but no executed test command.
- ``MTM_CRITICAL_DEADLINE_SECONDS`` (default 180): a ready deadline item inside this window outranks a PI.
"""
from __future__ import annotations

import math
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, Iterable, Optional

SUBMIT = "SUBMIT"
CONTINUE_WORK = "CONTINUE_WORK"
REWORK = "REWORK"
HOLD_FOR_REVIEW = "HOLD_FOR_REVIEW"
CLOSE = "CLOSE"
DECISIONS = (SUBMIT, CONTINUE_WORK, REWORK, HOLD_FOR_REVIEW, CLOSE)

FINAL_PHASES = ("READY_TO_INTEGRATE", "INTEGRATING", "INTEGRATED", "NEEDS_ATTENTION")
FAILED_STATUSES = ("BLOCKED", "FAILED", "TIMED_OUT", "ESCALATE")


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        return value if math.isfinite(value) else default
    except (TypeError, ValueError):
        return default


def partial_submittable(item_type: str) -> bool:
    """Ordinary items: always. PI (its own flow): only when MTM_PI_PARTIAL_SUBMIT=1."""
    if str(item_type or "").upper() == "PI":
        return os.environ.get("MTM_PI_PARTIAL_SUBMIT", "0") == "1"
    return True


def max_rework() -> int:
    return max(0, int(_env_float("MTM_MAX_REWORK", 1)))


def min_rework_seconds() -> float:
    return max(0.0, _env_float("MTM_MIN_REWORK_SECONDS", 300))


def hold_for_review_seconds() -> float:
    return max(0.0, _env_float("MTM_HOLD_FOR_REVIEW_SECONDS", 60))


def critical_deadline_seconds() -> float:
    return max(0.0, _env_float("MTM_CRITICAL_DEADLINE_SECONDS", 180))


def submit_budget(samples: Iterable[float] | None = None) -> float:
    """measured_submit_p95 + safety_margin. Until 3 measurements exist the configured default p95 is used."""
    values = sorted(float(s) for s in (samples or []) if isinstance(s, (int, float)) and math.isfinite(s) and s >= 0)
    if len(values) >= 3:
        p95 = values[min(len(values) - 1, math.ceil(0.95 * len(values)) - 1)]
    else:
        p95 = _env_float("MTM_SUBMIT_P95_DEFAULT_SECONDS", 60)
    return max(0.0, p95) + max(0.0, _env_float("MTM_SUBMIT_SAFETY_MARGIN_SECONDS", 30))


def announced_test_ids(details: dict[str, Any] | None) -> list[str]:
    """Scenario IDs the answer must report, independent of a worker's answer.

    The submit template rows (and structured MVS rows) are authoritative: when they exist, only their IDs are
    required. Prose is scanned only when no structured rows exist, and a bare ID such as ``TC-01`` is dropped
    when a qualified ID ending in it (``BUG-09-TC-01``) exists. (I5NLYST2: "schedules from TC-01 may stay" in
    the MVS text made the gate demand a TC-01 row that the official schema does not allow.)
    """
    details = details or {}
    structured: set[str] = set()
    prose: set[str] = set()

    def walk(value: Any, rows: bool = False) -> None:
        if isinstance(value, list):
            for row in value:
                walk(row, True)
        elif isinstance(value, dict):
            if rows:
                identifier = value.get("id") or value.get("testCaseId") or value.get("scenarioId")
                if isinstance(identifier, str) and identifier.strip():
                    structured.add(identifier.strip())
            for name in ("testResults", "scenarios", "testCases", "cases", "tests", "minimumVerificationScenarios", "mvs"):
                if name in value:
                    walk(value[name], True)
        elif isinstance(value, str):
            prose.update(re.findall(r"\b(?:[A-Z0-9]+-)*(?:TC|MVS|SCENARIO|TEST)-\d+(?:-[A-Za-z0-9]+)*\b", value))

    walk(details.get("submitTemplate", {}))
    walk(details.get("minimumVerificationScenarios", details.get("mvs")), True)
    if structured:
        return sorted(structured)
    return sorted(i for i in prose if not any(o != i and o.endswith("-" + i) for o in prose))


def mvs_status(required_ids: Iterable[str], answer: dict[str, Any] | None,
               test_results: list[dict[str, Any]] | None,
               diagnostic_results: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Compute mvs_passed from the answer's testResults and the executed commands.

    mvs_passed requires: every required scenario ID has result PASS in the answer, every executed test
    command passed with exit code 0, and no failing diagnostic row unless it is explicitly marked unrelated
    with a baseline log. A missing answer with no required IDs falls back to the executed commands only.
    """
    required = sorted({str(i) for i in required_ids or []})
    rows = answer.get("testResults") if isinstance(answer, dict) else None
    by_id: dict[str, str] = {}
    if isinstance(rows, list):
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str):
                by_id[row["id"]] = str(row.get("result", "")).upper()
    not_passed = [i for i in required if by_id.get(i) != "PASS"]
    answer_failures = [i for i, r in by_id.items() if r != "PASS"]
    commands = [t for t in (test_results or []) if isinstance(t, dict)]
    failed_commands = [t.get("command", "") for t in commands if t.get("passed") is not True or t.get("exit_code") != 0]
    bad_diagnostics = []
    for row in diagnostic_results or []:
        if not isinstance(row, dict) or row.get("passed") is True:
            continue
        if row.get("unrelated") is True and isinstance(row.get("baseline_log"), str) and row["baseline_log"].strip():
            continue
        bad_diagnostics.append(row.get("id") or row.get("command") or "diagnostic")
    executed = bool(commands)
    passed = not not_passed and not answer_failures and not failed_commands and not bad_diagnostics
    if not required and not by_id and not executed:
        passed = False
    return {"mvs_passed": passed, "required_ids": required, "not_passed_ids": not_passed,
            "answer_failures": sorted(answer_failures), "failed_commands": failed_commands,
            "failing_diagnostics": bad_diagnostics, "executed_commands": executed}


@dataclass(frozen=True)
class Decision:
    action: str
    reason: str
    must_finalize: bool
    submit_budget: float
    time_left: Optional[float]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def decide(*, item_type: str, phase: str, item_open: bool, time_left: Optional[float],
           result: dict[str, Any] | None, answer_status: Optional[str], rework_count: int,
           budget: float, ready_age: Optional[float] = None) -> Decision:
    """The single decision for one item. ``time_left`` is min(match remaining, item deadline slack)."""
    item_type = str(item_type or "").upper()
    must_finalize = time_left is not None and time_left <= budget

    def out(action: str, reason: str) -> Decision:
        return Decision(action, reason, must_finalize, budget, time_left)

    if phase == "SUBMITTED":
        return out(CLOSE, "accepted receipt recorded")
    if phase == "CLOSED" or not item_open or (time_left is not None and time_left <= 0):
        return out(CLOSE, "item closed or no time left")
    if phase == "RUNNING":
        return out(CONTINUE_WORK, "finalize requested: checkpoint/finish now" if must_finalize else "worker still running")
    if phase == "QUEUED":
        return out(CONTINUE_WORK, "waiting for dispatch")
    if phase not in FINAL_PHASES:
        return out(CONTINUE_WORK, f"phase {phase}")

    result = result or {}
    status = str(answer_status or result.get("status") or "").upper()
    if status == "READY_FOR_REVIEW":
        status = "DONE"
    can_rework = (rework_count < max_rework() and time_left is not None
                  and time_left > budget + min_rework_seconds())

    if status in FAILED_STATUSES or phase == "NEEDS_ATTENTION":
        return out(REWORK, f"{status or 'failed'} result; new attempt") if can_rework else \
            out(CLOSE, f"{status or 'failed'} result; no rework left")

    mvs_passed = bool(result.get("mvs_passed", result.get("verification_status") == "VERIFIED"))
    if status == "DONE":
        if mvs_passed:
            evidence_only = result.get("verification_status") == "EVIDENCE_ONLY"
            if (evidence_only and item_type != "PI" and not must_finalize
                    and (ready_age is None or ready_age < hold_for_review_seconds())):
                return out(HOLD_FOR_REVIEW, "DONE without an executed test command: automatic review hold")
            return out(SUBMIT, "DONE with MVS passed")
        return out(REWORK, "DONE claimed but MVS not passed") if can_rework else \
            out(CLOSE, "DONE claimed but MVS not passed; answer must be corrected to an honest status")

    if status == "PARTIAL":
        if not partial_submittable(item_type):
            return out(REWORK, f"{item_type} PARTIAL is not submitted by policy") if can_rework else \
                out(CLOSE, f"{item_type} PARTIAL is not submitted by policy")
        if must_finalize or not can_rework:
            return out(SUBMIT, "PARTIAL at the latest useful moment (no better expected outcome)")
        return out(REWORK, "PARTIAL with time left: new attempt from the verified artifacts")

    return out(CLOSE, f"unknown status {status!r}")


def priority_key(*, item_type: str, slack: Optional[float], has_deadline: bool, submit_ready: bool,
                 budget: float, key: str) -> tuple:
    """One ordering for the ready queue, the PI-first integration gate and dispatch.

    Two flows only: PI and ordinary items. Ordinary items are never ranked by type, points or size.
    1. a ready (submit-eligible) item with a deadline inside the critical window that still has submit_budget
       (a time rule that applies to every item alike);
    2. a PI;
    3. every other item, least time left first (deadline slack, else match remaining), then key.
    """
    if (submit_ready and has_deadline and slack is not None
            and budget <= slack <= critical_deadline_seconds()):
        return (0, slack, key)
    if str(item_type or "").upper() == "PI":
        return (1, slack if slack is not None else 0.0, key)
    return (2, slack if slack is not None else 1e9, key)
