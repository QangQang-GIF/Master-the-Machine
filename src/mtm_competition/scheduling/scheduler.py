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
        self.cooldown_until: Dict[str, float] = {}

        # Server clock synchronization
        self.clock_offset: float = 0.0
        self.server_remaining_seconds: Optional[float] = None
        self.last_sync_time: float = time.time()
        self.match_status: str = "RUNNING"
        self.is_paused: bool = False

    def set_cooldown(self, task_id: str, duration_seconds: float = 15.0) -> None:
        """Temporarily deprioritize a task after a transient failure so other ready tasks can run."""
        self.cooldown_until[task_id] = time.monotonic() + max(0.0, duration_seconds)

    def clear_cooldown(self, task_id: str) -> None:
        """Clear any transient cooldown on a task."""
        self.cooldown_until.pop(task_id, None)

    def is_on_cooldown(self, task_id: str) -> bool:
        """Return True if task is currently within a transient retry cooldown window."""
        until = self.cooldown_until.get(task_id)
        if until is None:
            return False
        if time.monotonic() >= until:
            self.cooldown_until.pop(task_id, None)
            return False
        return True

    def sync_with_game_master(self, match_state: Dict[str, Any]) -> None:
        """Calibrate server epoch, clock offset, remaining time, and pause state."""
        server_epoch = match_state.get("server_epoch")
        if server_epoch is not None:
            self.clock_offset = float(server_epoch) - time.time()

        if "remaining_seconds" in match_state:
            self.server_remaining_seconds = float(match_state["remaining_seconds"])
            self.last_sync_time = time.time()

        if "status" in match_state:
            self.match_status = str(match_state["status"]).upper()
            self.is_paused = (self.match_status == "PAUSED")

    @property
    def current_server_time(self) -> float:
        """Current epoch timestamp synchronized with Game Master server clock."""
        return time.time() + self.clock_offset

    @property
    def match_remaining_seconds(self) -> float:
        """Match duration remaining, respecting server clock and pause state."""
        if self.match_status == "FINISHED":
            return 0.0

        if self.server_remaining_seconds is not None:
            if self.is_paused:
                return self.server_remaining_seconds
            elapsed_since_sync = time.time() - self.last_sync_time
            return max(0.0, self.server_remaining_seconds - elapsed_since_sync)

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
        # Never schedule new tasks while match is PAUSED or FINISHED
        if self.is_paused or self.match_status == "FINISHED":
            return None

        if not self.has_capacity:
            return None

        # Promote newly DISCOVERED tasks to QUEUED (never downgrade READY_TO_SUBMIT back to QUEUED)
        discovered = self.store.list_tasks(status=TaskState.DISCOVERED.value)
        for d in discovered:
            if not self.store.has_submitted(d["task_id"]):
                self.store.update_task_status(d["task_id"], TaskState.QUEUED)

        rem_sec = self.match_remaining_seconds
        cur_time = self.current_server_time

        # Prioritize READY_TO_SUBMIT tasks first without downgrading their state
        ready_tasks = [
            t
            for t in self.store.list_tasks(status=TaskState.READY_TO_SUBMIT.value)
            if not self.store.has_submitted(t["task_id"]) and not self.is_on_cooldown(t["task_id"])
        ]
        if ready_tasks:
            pq_ready = PriorityQueue()
            for t in ready_tasks:
                pq_ready.push(t, match_remaining_seconds=rem_sec, current_time=cur_time)
            return pq_ready.pop()

        queued_tasks = self.store.list_tasks(status=TaskState.QUEUED.value)

        # Strictly ignore any task that has already been submitted
        queued_tasks = [t for t in queued_tasks if not self.store.has_submitted(t["task_id"])]
        if not queued_tasks:
            return None

        # Prefer tasks not currently on a transient retry cooldown
        eligible_tasks = [t for t in queued_tasks if not self.is_on_cooldown(t["task_id"])]
        pool = eligible_tasks if eligible_tasks else queued_tasks

        # Build priority queue
        pq = PriorityQueue()
        for t in pool:
            pq.push(t, match_remaining_seconds=rem_sec, current_time=cur_time)

        next_task = pq.pop()
        return next_task

    def assign_task(self, task_id: str, worker_id: str = "worker-01") -> None:
        """Mark task as assigned and running (or reserve capacity for READY_TO_SUBMIT recovery)."""
        existing = self.store.get_task(task_id)
        if existing and existing.get("status") == TaskState.READY_TO_SUBMIT.value:
            existing["start_time"] = time.time()
            existing["worker_id"] = worker_id
            self.running_tasks[task_id] = existing
            return

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

    def check_preemption(self, incoming_task: Dict[str, Any]) -> Optional[str]:
        """Check if incoming task should preempt any currently running worker.

        Returns task_id to preempt if policy triggers, else None.
        """
        for running_id, running_task in list(self.running_tasks.items()):
            elapsed = time.time() - float(running_task.get("start_time") or time.time())
            if PreemptionPolicy.should_preempt(incoming_task, running_task, elapsed):
                return running_id
        return None

    def requeue_task(self, task_id: str) -> None:
        """Deregister running task and return back to QUEUED for later execution."""
        self.running_tasks.pop(task_id, None)
        self.store.update_task_status(task_id, TaskState.QUEUED)
