"""Preemption policies for high-urgency tasks (e.g. PI tasks)."""
from __future__ import annotations

from typing import Any, Dict, Optional


class PreemptionPolicy:
    """Evaluates whether an incoming task should preempt currently running tasks."""

    @staticmethod
    def should_preempt(
        incoming_task: Dict[str, Any],
        running_task: Dict[str, Any],
        running_elapsed_seconds: float,
        has_capacity: bool = False,
    ) -> bool:
        # If spare capacity exists (e.g., slot 2 reserved for urgent/PI), run concurrently without preemption!
        if has_capacity:
            return False

        # Only PI (Production Incident) can preempt
        if incoming_task.get("task_type") != "PI":
            return False

        # If running task is also PI, do not preempt
        if running_task.get("task_type") == "PI":
            return False

        # If running task has been executing for over 80% of its budget, let it finish
        running_budget = float(running_task.get("time_budget_seconds") or 300)
        if running_elapsed_seconds > (running_budget * 0.8):
            return False

        return True
