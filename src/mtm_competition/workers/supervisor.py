"""WorkerSupervisor: Supervises active worker execution, process liveness, async dispatch, and preemption."""
from __future__ import annotations

import concurrent.futures
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mtm_core.contracts import TaskEnvelope, WorkerResult
from mtm_core.runtime import ExecutionTrace


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
    ) -> SupervisedProcess:
        proc = SupervisedProcess(
            task_id=task_id,
            attempt_number=attempt_number,
            started_at=time.time(),
            timeout_seconds=timeout_seconds,
            workspace_path=Path(workspace_path),
        )
        self._active_workers[task_id] = proc
        return proc

    def register_subprocess(self, task_id: str, proc: Any) -> None:
        """Register active subprocess handle for mid-execution killing."""
        active = self._active_workers.get(task_id)
        if active:
            active.subprocess_handle = proc

    def launch_async(
        self,
        launcher_fn: Callable[..., Tuple[WorkerResult, ExecutionTrace, Path]],
        envelope: TaskEnvelope,
        attempt_number: int = 1,
        backend: Optional[str] = None,
    ) -> SupervisedProcess:
        """Launch worker asynchronously in a supervised background execution thread."""
        task_id = envelope.task_id
        timeout_sec = float(envelope.time_budget_seconds or 420)
        repo_path = Path(envelope.repository_path)
        workspace_path = repo_path.parent / "scratch" / task_id

        proc = SupervisedProcess(
            task_id=task_id,
            attempt_number=attempt_number,
            started_at=time.time(),
            timeout_seconds=timeout_sec,
            workspace_path=workspace_path,
            status="RUNNING",
            envelope=envelope,
        )

        # Inspect launcher_fn signature safely outside _runner so exceptions during
        # execution NEVER trigger a second invocation (P1.1 Supervisor Reliability)
        import inspect
        has_on_proc = False
        try:
            sig = inspect.signature(launcher_fn)
            has_on_proc = "on_process_start" in sig.parameters
        except (ValueError, TypeError):
            has_on_proc = False

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
        if proc.future:
            return proc.future.done()
        return proc.status in ("COMPLETED", "FINISHED", "FAILED", "PREEMPTED", "TIMED_OUT")

    def get_result(
        self,
        task_id: str,
        timeout: Optional[float] = 0.0,
    ) -> Optional[Tuple[WorkerResult, ExecutionTrace, Path]]:
        proc = self._active_workers.get(task_id)
        if not proc:
            return None
        if proc.result:
            return proc.result
        if proc.future:
            try:
                proc.result = proc.future.result(timeout=timeout)
                proc.status = "COMPLETED"
                return proc.result
            except TimeoutError:
                return None
            except Exception as e:
                proc.status = "FAILED"
                raise e
        return None

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
            try:
                import sys
                if sys.platform == "win32":
                    import subprocess
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.subprocess_handle.pid)],
                        capture_output=True,
                        timeout=5,
                    )
                else:
                    import os, signal
                    os.killpg(os.getpgid(proc.subprocess_handle.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.subprocess_handle.kill()
                    proc.subprocess_handle.wait(timeout=1.0)
                except Exception:
                    pass

        if proc.future and not proc.future.done():
            proc.future.cancel()

        proc.status = "PREEMPTED"
        return self.unregister_worker(task_id)

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
