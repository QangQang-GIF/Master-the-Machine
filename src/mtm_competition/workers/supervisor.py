"""WorkerSupervisor: Supervises active worker execution, process liveness, and timeouts."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class SupervisedProcess:
    task_id: str
    attempt_number: int
    started_at: float
    timeout_seconds: float
    workspace_path: Path
    status: str = "RUNNING"


class WorkerSupervisor:
    """Supervises active worker processes and detects timeouts / unresponsiveness."""

    def __init__(self):
        self._active_workers: Dict[str, SupervisedProcess] = {}

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

    def unregister_worker(self, task_id: str) -> Optional[SupervisedProcess]:
        return self._active_workers.pop(task_id, None)

    def is_timed_out(self, task_id: str) -> bool:
        proc = self._active_workers.get(task_id)
        if not proc:
            return False
        return (time.time() - proc.started_at) > proc.timeout_seconds

    def active_worker_count(self) -> int:
        return len(self._active_workers)
