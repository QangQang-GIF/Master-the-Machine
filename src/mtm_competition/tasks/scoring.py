"""Priority scoring and Expected Value calculation for scheduling."""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

# Empirical default pass rates by task type
DEFAULT_P_PASS = {
    "BUG": 0.85,
    "TASK": 0.90,
    "CR": 0.70,
    "PI": 0.80,
}

# Empirical default execution seconds by task type
DEFAULT_EXPECTED_SECONDS = {
    "PI": 120.0,    # Speed critical
    "BUG": 180.0,
    "TASK": 150.0,
    "CR": 300.0,
}


def calculate_expected_value(
    task: Dict[str, Any],
    match_remaining_seconds: float = 2400.0,
    active_attempts: int = 0,
    current_time: Optional[float] = None,
) -> float:
    """Compute task priority score based on tournament economics with server-time awareness.

    EV = (P(Pass) * Points * UrgencyFactor * DeadlineFactor) / ExpectedTime
    """
    task_type = task.get("task_type", "BUG").upper()
    points = float(task.get("points") or 100)

    p_pass = DEFAULT_P_PASS.get(task_type, 0.75)
    expected_time = DEFAULT_EXPECTED_SECONDS.get(task_type, 180.0)

    # Urgency factor: PI tasks have high urgency because faster finish saves points
    urgency = 2.0 if task_type == "PI" else 1.0

    # Deadline factor: if deadline is imminent, prioritize using calibrated server clock
    deadline_epoch = task.get("deadline_epoch")
    deadline_factor = 1.0
    now = current_time if current_time is not None else time.time()
    if deadline_epoch:
        seconds_left = max(1.0, deadline_epoch - now)
        if seconds_left < expected_time:
            deadline_factor = 0.5  # Risk of expiry, discount EV
        elif seconds_left < expected_time * 2:
            deadline_factor = 1.5  # High priority to catch before deadline

    # Penalty for repeated failed attempts
    attempt_penalty = 1.0 / (1.0 + active_attempts * 0.5)

    # If remaining match time is less than expected time, steep discount
    time_factor = 1.0
    if match_remaining_seconds < expected_time:
        time_factor = 0.2

    score = (p_pass * points * urgency * deadline_factor * attempt_penalty * time_factor) / expected_time
    return round(score, 4)


def expected_score_priority(
    task: Dict[str, Any],
    triage: Optional[Dict[str, Any]] = None,
    match_remaining_seconds: float = 2400.0,
    now: Optional[float] = None,
    integration_queue_seconds: float = 0.0,
) -> Dict[str, Any]:
    """V2 non-PI priority: expected_score / estimated_end_to_end_time, with an explanation.

    expected_score = points * P(pass) * deadline_feasibility. Inputs are forecasts. A task that cannot
    finish (feasibility 0) is returned with priority 0 and a skip reason instead of being hidden.
    """
    otype = str(task.get("type") or task.get("task_type") or "TASK").upper()
    points = float(task.get("points") or 0.0)
    conf = float((triage or {}).get("confidence", 0.6))
    base_p = DEFAULT_P_PASS.get(otype, 0.75)
    p_pass = base_p * (0.85 + 0.15 * conf)  # lower confidence slightly discounts the forecast
    work = float((triage or {}).get("estimatedWorkSeconds", DEFAULT_EXPECTED_SECONDS.get(otype, 180.0)))
    evid = float((triage or {}).get("estimatedEvidenceSeconds", 45.0))
    total = max(10.0, work + evid + max(0.0, integration_queue_seconds))

    t = time.time() if now is None else now
    deadline = task.get("deadline_epoch")
    slack = float(match_remaining_seconds)
    if deadline is not None:
        slack = min(slack, float(deadline) - t)
    reasons = []
    if slack <= 0:
        feas, reasons = 0.0, ["no time left before deadline/match end"]
    elif slack < total:
        feas, reasons = 0.0, [f"needs ~{int(total)}s but only {int(slack)}s left"]
    else:
        feas = min(1.0, 0.5 + 0.5 * (slack - total) / max(total, 1.0))
        reasons.append("fits before deadline")
    expected = points * p_pass * feas
    urgency = 1.0
    if deadline is not None and slack < total * 2:
        urgency = 1.3
        reasons.append("deadline within 2x estimated time")
    priority = (expected / total) * urgency
    return {
        "key": task.get("key"), "expectedScore": round(expected, 3), "priority": round(priority, 5),
        "estimatedEndToEndSeconds": int(total), "deadlineFeasibility": round(feas, 3),
        "pPass": round(p_pass, 3), "reasons": reasons, "estimateKind": "forecast",
    }
