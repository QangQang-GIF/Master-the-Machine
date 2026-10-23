"""WorkerSupervisor: Supervises active worker execution, process liveness, async dispatch, and preemption."""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mtm_core.contracts import TaskEnvelope, WorkerResult
from mtm_core.runtime import ExecutionTrace
from mtm_core.runtime.process import terminate_process_tree


# Valid supervised process lifecycle states
SUPERVISOR_STATES = frozenset({
    "RUNNING",
    "PREEMPT_REQUESTED",
    "PREEMPTED",
    "TIMED_OUT",
    "CANCELLED",
    "FAILED",
    "COMPLETED",
})


@dataclass
class SupervisedProcess:
    task_id: str
    attempt_number: int
    started_at: float
    timeout_seconds: float
    workspace_path: Path
    status: str = "RUNNING"
    future: Optional[concurrent.futures.Future] = None
    envelope: Optional[TaskEnvelope] = None
    result: Optional[Tuple[WorkerResult, ExecutionTrace, Path]] = None
    subprocess_handle: Optional[Any] = None


class WorkerSupervisor:
    """Supervises active worker processes and handles async dispatch, timeouts, and preemption."""

    def __init__(self, max_workers: int = 2):
        self._active_workers: Dict[str, SupervisedProcess] = {}
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)

    def register_worker(
        self,
        task_id: str,
        attempt_number: int,
        timeout_seconds: float,
        workspace_path: Path | str,
        envelope: Optional[TaskEnvelope] = None,
    ) -> SupervisedProcess:
        proc = SupervisedProcess(
            task_id=task_id,
            attempt_number=attempt_number,
            started_at=time.time(),
            timeout_seconds=timeout_seconds,
            workspace_path=Path(workspace_path),
            envelope=envelope,
        )
        self._active_workers[task_id] = proc
        return proc

    def register_subprocess(self, task_id: str, handle: Any) -> None:
        """Register active subprocess handle for mid-execution killing."""
        proc = self._active_workers.get(task_id)
        if proc:
            proc.subprocess_handle = handle

    def get_process(self, task_id: str) -> Optional[SupervisedProcess]:
        return self._active_workers.get(task_id)

    def launch_async(
        self,
        launcher_fn: Callable[..., Tuple[WorkerResult, ExecutionTrace, Path]],
        envelope: TaskEnvelope,
        attempt_number: int = 1,
    ) -> SupervisedProcess:
        """Asynchronously dispatch worker execution wrapped in a supervised future."""
        import inspect

        task_id = envelope.task_id
        proc = self.register_worker(
            task_id=task_id,
            attempt_number=attempt_number,
            timeout_seconds=envelope.time_budget_seconds,
            workspace_path=envelope.repository_path,
            envelope=envelope,
        )

        sig = inspect.signature(launcher_fn)
        has_on_proc = "on_process_start" in sig.parameters

        def _runner():
            if has_on_proc:
                return launcher_fn(
                    envelope,
                    attempt_number=attempt_number,
                    on_process_start=lambda p: self.register_subprocess(task_id, p),
                )
            return launcher_fn(envelope, attempt_number=attempt_number)

        self._active_workers[task_id] = proc
        future = self._executor.submit(_runner)
        proc.future = future
        return proc

    def is_finished(self, task_id: str) -> bool:
        proc = self._active_workers.get(task_id)
        if not proc:
            return False
        if proc.status in ("COMPLETED", "FINISHED", "FAILED", "PREEMPTED", "TIMED_OUT", "CANCELLED"):
            return True
        if proc.future:
            return proc.future.done()
        return False

    def get_result(
        self,
        task_id: str,
        timeout: Optional[float] = 0.0,
    ) -> Optional[Tuple[WorkerResult, ExecutionTrace, Path]]:
        """Retrieve worker result.

        Returns:
            - The (WorkerResult, ExecutionTrace, Path) tuple if worker completed normally.
            - None if worker is still running, was preempted, timed out, or cancelled.
            Check proc.status to distinguish RUNNING, TIMED_OUT, PREEMPTED, CANCELLED, FAILED, COMPLETED.
        """
        proc = self._active_workers.get(task_id)
        if not proc:
            return None
        if proc.result:
            return proc.result
        # If already in a terminal non-success state, don't try to retrieve result
        if proc.status in ("PREEMPTED", "TIMED_OUT", "CANCELLED"):
            return None
        if proc.future:
            try:
                res = proc.future.result(timeout=timeout)
                # Guard against concurrent preemption/timeout while future was unblocking
                if proc.status in ("PREEMPTED", "TIMED_OUT", "CANCELLED"):
                    return None
                proc.result = res
                proc.status = "COMPLETED"
                return proc.result
            except (TimeoutError, concurrent.futures.TimeoutError):
                return None
            except concurrent.futures.CancelledError:
                if proc.status not in ("PREEMPTED", "TIMED_OUT"):
                    proc.status = "CANCELLED"
                return None
            except Exception as e:
                if proc.status in ("PREEMPTED", "TIMED_OUT", "CANCELLED"):
                    return None
                proc.status = "FAILED"
                raise e
        return None

    def timeout_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Terminate worker due to timeout. Sets status TIMED_OUT and keeps registration for status inspection."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None
        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc
        proc.status = "TIMED_OUT"
        if proc.subprocess_handle is not None:
            terminate_process_tree(proc.subprocess_handle)
        if proc.future and not proc.future.done():
            proc.future.cancel()
        return proc

    def preempt_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Preempt, terminate process tree immediately, and cancel running worker."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None

        # Avoid race condition if worker already completed
        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc

        proc.status = "PREEMPT_REQUESTED"
        if proc.subprocess_handle is not None:
            terminate_process_tree(proc.subprocess_handle)

        if proc.future and not proc.future.done():
            proc.future.cancel()

        proc.status = "PREEMPTED"
        return self.unregister_worker(task_id)

    def cancel_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Cancel and terminate an active worker (e.g. when match transitions to FINISHED)."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None

        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc

        if proc.subprocess_handle is not None:
            terminate_process_tree(proc.subprocess_handle)

        if proc.future and not proc.future.done():
            proc.future.cancel()

        proc.status = "CANCELLED"
        return self.unregister_worker(task_id)

    def terminate_all(self, reason: str = "CANCELLED") -> List[SupervisedProcess]:
        """Terminate all active workers immediately."""
        terminated: List[SupervisedProcess] = []
        for task_id in list(self._active_workers.keys()):
            if reason == "TIMED_OUT":
                proc = self.timeout_worker(task_id)
                self.unregister_worker(task_id)
            elif reason == "PREEMPTED":
                proc = self.preempt_worker(task_id)
            else:
                proc = self.cancel_worker(task_id)
            if proc:
                terminated.append(proc)
        return terminated

    def unregister_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        return self._active_workers.pop(task_id, None)

    def is_timed_out(self, task_id: str) -> bool:
        proc = self._active_workers.get(task_id)
        if not proc:
            return False
        return (time.time() - proc.started_at) > proc.timeout_seconds

    def active_worker_count(self) -> int:
        return len(self._active_workers)

    def get_active_task_ids(self) -> List[str]:
        return list(self._active_workers.keys())
