"""Lifecycle events and states for tasks and tournament execution."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, Optional


class TaskState(str, Enum):
    DISCOVERED = "DISCOVERED"
    QUEUED = "QUEUED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    VERIFYING = "VERIFYING"
    READY_TO_SUBMIT = "READY_TO_SUBMIT"
    SUBMIT_IN_FLIGHT = "SUBMIT_IN_FLIGHT"
    SUBMITTED = "SUBMITTED"
    PASSED = "PASSED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    BLOCKED = "BLOCKED"
    TIMED_OUT = "TIMED_OUT"
    ABORTED = "ABORTED"


@dataclass
class TaskEvent:
    task_id: str
    event_type: str
    from_state: Optional[TaskState] = None
    to_state: Optional[TaskState] = None
    timestamp: float = field(default_factory=time.time)
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.from_state:
            d["from_state"] = self.from_state.value
        if self.to_state:
            d["to_state"] = self.to_state.value
        return d
