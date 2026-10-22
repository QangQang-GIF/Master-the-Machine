"""MockGameMasterAdapter: In-memory simulation of the Game Master for E2E testing."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import GameMasterAdapter


class MockGameMasterAdapter(GameMasterAdapter):
    """Simulates Game Master backlog dispatching, MVS reveal, and grading."""

    def __init__(
        self,
        tasks: Optional[List[Dict[str, Any]]] = None,
        duration_seconds: float = 2400.0,
    ):
        self.duration_seconds = duration_seconds
        self.start_time = time.time()
        self.tasks: Dict[str, Dict[str, Any]] = {
            t["task_id"]: dict(t) for t in (tasks or [])
        }
        self.submissions: Dict[str, Dict[str, Any]] = {}
        self.started_tasks: set[str] = set()

    def get_match_state(self) -> Dict[str, Any]:
        elapsed = time.time() - self.start_time
        rem = max(0.0, self.duration_seconds - elapsed)
        return {
            "status": "RUNNING" if rem > 0 else "FINISHED",
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(rem, 1),
            "total_score": sum(s.get("points", 0) for s in self.submissions.values()),
        }

    def list_backlog(self) -> List[Dict[str, Any]]:
        return list(self.tasks.values())

    def get_task(self, task_id: str) -> Dict[str, Any]:
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found in backlog")
        return self.tasks[task_id]

    def start_working(self, task_id: str) -> Dict[str, Any]:
        if task_id not in self.tasks:
            raise KeyError(f"Task {task_id} not found in backlog")
        self.started_tasks.add(task_id)
        return {"status": "ACKNOWLEDGED", "task_id": task_id, "started_at": time.time()}

    def get_mvs(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        if task_id not in self.tasks:
            return None
        return self.tasks[task_id].get("mvs")

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        if task_id in self.submissions:
            return {
                "status": "REJECTED",
                "error": f"Task {task_id} has already been submitted",
            }

        task = self.tasks.get(task_id, {})
        pts = int(task.get("points") or 100)
        res = {
            "status": "ACCEPTED",
            "task_id": task_id,
            "git_revision": git_revision,
            "points": pts,
            "submitted_at": time.time(),
        }
        self.submissions[task_id] = res
        return res

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        return self.submissions.get(task_id, {"status": "NOT_SUBMITTED"})
