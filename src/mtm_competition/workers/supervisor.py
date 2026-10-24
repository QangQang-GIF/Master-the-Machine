"""WorkerSupervisor: Supervises active worker execution, process liveness, async dispatch, and preemption."""
from __future__ import annotations

import concurrent.futures
import inspect
import threading
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
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)
    quiesced: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)


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

    def _register_proc_subprocess(
        self,
        proc: SupervisedProcess,
        handle: Any,
        on_pid_callback: Optional[Callable[[int], None]] = None,
    ) -> None:
        """Bind subprocess handle directly to `proc` under lock; kill immediately if already cancelled."""
        with proc._lock:
            proc.subprocess_handle = handle
            already_cancelled = (
                proc.cancel_event.is_set()
                or proc.status in ("PREEMPT_REQUESTED", "PREEMPTED", "TIMED_OUT", "CANCELLED")
            )
        if already_cancelled:
            terminate_process_tree(handle)
            return
        if on_pid_callback is not None:
            pid = getattr(handle, "pid", None)
            if isinstance(pid, int) and pid > 0:
                try:
                    on_pid_callback(pid)
                except Exception:
                    pass

    def register_subprocess(self, task_id: str, handle: Any) -> None:
        """Register active subprocess handle for mid-execution killing."""
        proc = self._active_workers.get(task_id)
        if proc:
            self._register_proc_subprocess(proc, handle)

    def get_process(self, task_id: str) -> Optional[SupervisedProcess]:
        return self._active_workers.get(task_id)

    def get_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        return self.get_process(task_id)

    def launch_async(
        self,
        launcher_fn: Callable[..., Tuple[WorkerResult, ExecutionTrace, Path]],
        envelope: TaskEnvelope,
        attempt_number: int = 1,
        on_pid_callback: Optional[Callable[[int], None]] = None,
    ) -> SupervisedProcess:
        """Asynchronously dispatch worker execution wrapped in a supervised future."""
        task_id = envelope.task_id
        proc = self.register_worker(
            task_id=task_id,
            attempt_number=attempt_number,
            timeout_seconds=envelope.time_budget_seconds,
            workspace_path=envelope.repository_path,
            envelope=envelope,
        )

        target_fn = (
            launcher_fn.side_effect
            if callable(getattr(launcher_fn, "side_effect", None))
            else launcher_fn
        )
        sig = inspect.signature(target_fn)
        has_on_proc = "on_process_start" in sig.parameters
        has_cancel_ev = "cancel_event" in sig.parameters

        def _runner():
            if proc.cancel_event.is_set():
                return None
            kwargs: Dict[str, Any] = {"attempt_number": attempt_number}
            if has_on_proc:
                kwargs["on_process_start"] = lambda p: self._register_proc_subprocess(proc, p, on_pid_callback)
            if has_cancel_ev:
                kwargs["cancel_event"] = proc.cancel_event
            return launcher_fn(envelope, **kwargs)

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
                if proc.status in ("PREEMPTED", "TIMED_OUT", "CANCELLED") or proc.cancel_event.is_set():
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
                if proc.status in ("PREEMPTED", "TIMED_OUT", "CANCELLED") or proc.cancel_event.is_set():
                    return None
                proc.status = "FAILED"
                raise e
        return None

    def _quiesce_worker(
        self,
        proc: SupervisedProcess,
        target_status: str,
        join_timeout: float = 5.0,
    ) -> bool:
        """Atomically signal cancellation, kill subprocess tree, and wait for worker thread quiescence.

        Continuously monitors and terminates any late-registered subprocess handles until the
        worker future finishes (or `join_timeout` elapses), and records `proc.quiesced`.
        """
        with proc._lock:
            proc.cancel_event.set()
            proc.status = target_status
            handle = proc.subprocess_handle

        if handle is not None:
            terminate_process_tree(handle)

        if proc.future is None:
            proc.quiesced = True
            return True

        if not proc.future.done():
            proc.future.cancel()

        deadline = time.monotonic() + max(0.1, float(join_timeout))
        while not proc.future.done() and time.monotonic() < deadline:
            with proc._lock:
                cur_handle = proc.subprocess_handle
            if cur_handle is not None:
                poll_fn = getattr(cur_handle, "poll", None)
                if not callable(poll_fn) or poll_fn() is None:
                    terminate_process_tree(cur_handle)
            try:
                concurrent.futures.wait([proc.future], timeout=0.05)
            except Exception:
                pass

        proc.quiesced = bool(proc.future.done())
        return proc.quiesced

    def timeout_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Terminate worker due to timeout. Sets status TIMED_OUT and keeps registration for status inspection."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None
        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc
        self._quiesce_worker(proc, "TIMED_OUT")
        return proc

    def preempt_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Preempt, terminate process tree immediately, and cancel running worker."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None

        # Avoid race condition if worker already completed
        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc

        self._quiesce_worker(proc, "PREEMPTED")
        return self.unregister_worker(task_id)

    def cancel_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        """Cancel and terminate an active worker (e.g. when match transitions to FINISHED)."""
        proc = self._active_workers.get(task_id)
        if not proc:
            return None

        if proc.future and proc.future.done() and proc.status == "COMPLETED":
            return proc

        self._quiesce_worker(proc, "CANCELLED")
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

    def shutdown_all(self) -> List[SupervisedProcess]:
        """Alias for terminate_all() used during clean shutdown."""
        return self.terminate_all("CANCELLED")

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
