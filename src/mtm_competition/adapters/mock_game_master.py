"""MockGameMasterAdapter: In-memory simulation of the Game Master for E2E testing."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .base import GameMasterAdapter


class MockGameMasterAdapter(GameMasterAdapter):
    """Simulates Game Master backlog dispatching, MVS reveal, server clock, and grading."""

    def __init__(
        self,
        tasks: Optional[List[Dict[str, Any]]] = None,
        duration_seconds: float = 2400.0,
        two_phase_grading: bool = False,
    ):
        self.duration_seconds = duration_seconds
        self.start_time = time.time()
        self.is_paused = False
        self.pause_start_time: Optional[float] = None
        self.total_paused_duration = 0.0
        self.two_phase_grading = two_phase_grading

        self.tasks: Dict[str, Dict[str, Any]] = {
            t["task_id"]: dict(t) for t in (tasks or [])
        }
        self.submissions: Dict[str, Dict[str, Any]] = {}
        self.grading_overrides: Dict[str, Dict[str, Any]] = {}
        self.started_tasks: set[str] = set()

    def pause_match(self) -> None:
        """Pause tournament clock and match progress."""
        if not self.is_paused:
            self.is_paused = True
            self.pause_start_time = time.time()

    def resume_match(self) -> None:
        """Resume tournament clock."""
        if self.is_paused and self.pause_start_time:
            self.total_paused_duration += (time.time() - self.pause_start_time)
            self.is_paused = False
            self.pause_start_time = None

    def finish_match(self) -> None:
        """End tournament match immediately."""
        self.is_paused = False
        self.duration_seconds = 0.0

    def add_wave(self, tasks: List[Dict[str, Any]]) -> None:
        """Dynamically add new backlog items during match execution."""
        for t in tasks:
            self.tasks[t["task_id"]] = dict(t)

    def set_grading_outcome(self, task_id: str, status: str, points: int) -> None:
        """Configure explicit grading result for testing."""
        self.grading_overrides[task_id] = {
            "status": status,
            "points": points,
            "task_id": task_id,
        }

    def get_match_state(self) -> Dict[str, Any]:
        now = time.time()
        current_paused = self.total_paused_duration
        if self.is_paused and self.pause_start_time:
            current_paused += (now - self.pause_start_time)

        elapsed = max(0.0, (now - self.start_time) - current_paused)
        rem = max(0.0, self.duration_seconds - elapsed)

        if self.is_paused:
            status = "PAUSED"
        elif rem <= 0:
            status = "FINISHED"
        else:
            status = "RUNNING"

        total_pts = sum(
            s.get("points", 0) for s in self.submissions.values()
            if s.get("status") in ("ACCEPTED", "PASSED")
        )

        return {
            "status": status,
            "elapsed_seconds": round(elapsed, 1),
            "remaining_seconds": round(rem, 1),
            "total_score": total_pts,
            "server_epoch": now,
            "is_paused": self.is_paused,
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

        if self.two_phase_grading:
            # Server returns ACCEPTED with 0 points awarded until graded
            res = {
                "status": "ACCEPTED",
                "task_id": task_id,
                "git_revision": git_revision,
                "points": 0,
                "submitted_at": time.time(),
                "submission_id": f"sub_{task_id}",
            }
        else:
            # Immediate score mode for backwards compatibility
            res = {
                "status": "ACCEPTED",
                "task_id": task_id,
                "git_revision": git_revision,
                "points": pts,
                "submitted_at": time.time(),
                "submission_id": f"sub_{task_id}",
            }

        self.submissions[task_id] = res
        return res

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        if task_id in self.grading_overrides:
            override = self.grading_overrides[task_id]
            if task_id in self.submissions:
                self.submissions[task_id]["points"] = override.get("points", 0)
                self.submissions[task_id]["status"] = override.get("status", "PASSED")
            return override

        sub = self.submissions.get(task_id)
        if not sub:
            return {"status": "NOT_SUBMITTED", "task_id": task_id}

        if self.two_phase_grading:
            # Auto-grade to PASSED with task points
            task = self.tasks.get(task_id, {})
            pts = int(task.get("points") or 100)
            sub["status"] = "PASSED"
            sub["points"] = pts
            return {"status": "PASSED", "task_id": task_id, "points": pts}

        return sub
