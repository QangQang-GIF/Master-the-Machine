"""Deterministic State Machine for Competition Tasks."""
from __future__ import annotations

from typing import Dict, List, Set

from mtm_core.contracts import TaskState


class InvalidStateTransitionError(Exception):
    pass


VALID_TRANSITIONS: Dict[TaskState, Set[TaskState]] = {
    TaskState.DISCOVERED: {TaskState.QUEUED, TaskState.ABORTED},
    TaskState.QUEUED: {TaskState.ASSIGNED, TaskState.ABORTED, TaskState.TIMED_OUT},
    TaskState.ASSIGNED: {TaskState.RUNNING, TaskState.QUEUED, TaskState.ABORTED},
    TaskState.RUNNING: {
        TaskState.VERIFYING,
        TaskState.QUEUED,
        TaskState.TIMED_OUT,
        TaskState.BLOCKED,
        TaskState.REJECTED,
        TaskState.ABORTED,
    },
    TaskState.VERIFYING: {
        TaskState.READY_TO_SUBMIT,
        TaskState.RUNNING,  # retry/fix
        TaskState.QUEUED,
        TaskState.REJECTED,
        TaskState.TIMED_OUT,
        TaskState.ABORTED,
    },
    TaskState.READY_TO_SUBMIT: {
        TaskState.SUBMIT_IN_FLIGHT,
        TaskState.SUBMITTED,
        TaskState.QUEUED,
        TaskState.REJECTED,
        TaskState.ABORTED,
    },
    TaskState.SUBMIT_IN_FLIGHT: {
        TaskState.SUBMITTED,
        TaskState.PASSED,
        TaskState.FAILED,
        TaskState.READY_TO_SUBMIT,
        TaskState.REJECTED,
        TaskState.ABORTED,
    },
    TaskState.SUBMITTED: {
        TaskState.PASSED,
        TaskState.FAILED,
        TaskState.REJECTED,
    },
    TaskState.PASSED: set(),  # Terminal state
    TaskState.FAILED: {TaskState.QUEUED, TaskState.ABORTED},
    TaskState.REJECTED: {TaskState.QUEUED, TaskState.ABORTED},
    TaskState.TIMED_OUT: {TaskState.QUEUED, TaskState.ABORTED},
    TaskState.BLOCKED: {TaskState.QUEUED, TaskState.ABORTED},
    TaskState.ABORTED: set(),  # Terminal state
}


class TaskStateMachine:
    """Enforces strict, deterministic lifecycle transitions."""

    @staticmethod
    def validate_transition(from_state: TaskState | str, to_state: TaskState | str) -> None:
        fs = from_state if isinstance(from_state, TaskState) else TaskState(from_state)
        ts = to_state if isinstance(to_state, TaskState) else TaskState(to_state)

        allowed = VALID_TRANSITIONS.get(fs, set())
        if ts not in allowed:
            raise InvalidStateTransitionError(
                f"Invalid task state transition: {fs.value} -> {ts.value}. Allowed: {[s.value for s in allowed]}"
            )

    @staticmethod
    def can_transition(from_state: TaskState | str, to_state: TaskState | str) -> bool:
        try:
            TaskStateMachine.validate_transition(from_state, to_state)
            return True
        except (InvalidStateTransitionError, ValueError):
            return False
