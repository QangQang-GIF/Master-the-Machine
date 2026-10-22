"""TournamentScheduler: High-level tactical coordinator for task selection and worker allocation."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from mtm_core.contracts import TaskState
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.state_machine import TaskStateMachine
from mtm_competition.scheduling.priority import PriorityQueue
from mtm_competition.scheduling.preemption import PreemptionPolicy


class TournamentScheduler:
    """Selects the next optimal task to execute based on Expected Value, deadline, and concurrency."""

    def __init__(
        self,
        task_store: TaskStore,
        concurrency_limit: int = 1,
        match_duration_seconds: float = 2400.0,
    ):
        self.store = task_store
        self.concurrency_limit = concurrency_limit
        self.match_duration = match_duration_seconds
        self.match_start_time = time.time()
        self.running_tasks: Dict[str, Dict[str, Any]] = {}

    @property
    def match_remaining_seconds(self) -> float:
        elapsed = time.time() - self.match_start_time
        return max(0.0, self.match_duration - elapsed)

    @property
    def active_worker_count(self) -> int:
        return len(self.running_tasks)

    @property
    def has_capacity(self) -> bool:
        return self.active_worker_count < self.concurrency_limit

    def select_next_task(self) -> Optional[Dict[str, Any]]:
        """Select highest EV task from queued backlog."""
        if not self.has_capacity:
            return None

        queued_tasks = self.store.list_tasks(status=TaskState.QUEUED.value)
        if not queued_tasks:
            # Also consider newly DISCOVERED tasks
            discovered = self.store.list_tasks(status=TaskState.DISCOVERED.value)
            for d in discovered:
                if not self.store.has_submitted(d["task_id"]):
                    self.store.update_task_status(d["task_id"], TaskState.QUEUED)
            queued_tasks = self.store.list_tasks(status=TaskState.QUEUED.value)

        # Strictly ignore any task that has already been submitted
        queued_tasks = [t for t in queued_tasks if not self.store.has_submitted(t["task_id"])]
        if not queued_tasks:
            return None

        # Build priority queue
        pq = PriorityQueue()
        rem_sec = self.match_remaining_seconds
        for t in queued_tasks:
            pq.push(t, match_remaining_seconds=rem_sec)

        next_task = pq.pop()
        return next_task

    def assign_task(self, task_id: str, worker_id: str = "worker-01") -> None:
        """Mark task as assigned and running."""
        TaskStateMachine.validate_transition(TaskState.QUEUED, TaskState.ASSIGNED)
        self.store.update_task_status(task_id, TaskState.ASSIGNED, {"worker_id": worker_id})
        TaskStateMachine.validate_transition(TaskState.ASSIGNED, TaskState.RUNNING)
        self.store.update_task_status(task_id, TaskState.RUNNING, {"worker_id": worker_id})

        task = self.store.get_task(task_id)
        if task:
            task["start_time"] = time.time()
            task["worker_id"] = worker_id
            self.running_tasks[task_id] = task

    def complete_task(self, task_id: str, final_state: TaskState | str) -> None:
        """Deregister running task upon completion."""
        self.running_tasks.pop(task_id, None)
        self.store.update_task_status(task_id, final_state)
