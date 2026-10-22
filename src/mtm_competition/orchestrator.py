"""CompetitionOrchestrator: Autonomous control plane for the MTM 2026 Tournament."""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import TaskEnvelope, WorkerResult, TaskState
from mtm_competition.tasks.store import TaskStore
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.workers.launcher import WorkerLauncher
from mtm_competition.verification.qa_gate import validate_qa_gate, QAGateError
from mtm_competition.integration.integration_guard import IntegrationGuard
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.adapters.base import GameMasterAdapter


class CompetitionOrchestrator:
    """Master controller connecting Game Master, Scheduler, Workers, and Submission Guards."""

    def __init__(
        self,
        gm_adapter: GameMasterAdapter,
        repository_path: Path | str,
        scratch_dir: Path | str,
        db_path: Path | str = ":memory:",
        concurrency_limit: int = 1,
    ):
        self.gm = gm_adapter
        self.repo_path = Path(repository_path).resolve()
        self.scratch_dir = Path(scratch_dir).resolve()
        self.store = TaskStore(db_path)
        self.scheduler = TournamentScheduler(self.store, concurrency_limit=concurrency_limit)
        self.launcher = WorkerLauncher(self.scratch_dir)
        self.integration_guard = IntegrationGuard(self.repo_path)
        self.git_guard = GitGuard(self.repo_path)
        self.submission_guard = SubmissionGuard(self.store)

    def sync_backlog(self) -> int:
        """Poll backlog from Game Master and register new items into TaskStore."""
        backlog = self.gm.list_backlog()
        count = 0
        for item in backlog:
            task_id = item["task_id"]
            if not self.store.get_task(task_id):
                env = TaskEnvelope(
                    task_id=task_id,
                    task_type=item.get("task_type", "BUG"),
                    title=item.get("title", ""),
                    description=item.get("description", ""),
                    repository_path=str(self.repo_path),
                    points=int(item.get("points") or 100),
                    deadline_epoch=item.get("deadline_epoch"),
                    time_budget_seconds=int(item.get("time_budget_seconds") or 420),
                    evidence_required=item.get("evidence_required") or [],
                )
                self.store.add_task(env)
                count += 1
        return count

    def step(self) -> Optional[Dict[str, Any]]:
        """Perform one discrete orchestration cycle.

        1. Select next task from scheduler.
        2. Unlock MVS from Game Master via start_working.
        3. Launch isolated worker.
        4. Validate outputs via QA Gate.
        5. Integrate patch, verify git revision, and submit via Submission Guard.
        """
        task_data = self.scheduler.select_next_task()
        if not task_data:
            return None

        task_id = task_data["task_id"]
        self.scheduler.assign_task(task_id)

        # 1. Signal Game Master and unlock MVS
        self.gm.start_working(task_id)
        mvs = self.gm.get_mvs(task_id)

        envelope = TaskEnvelope(
            task_id=task_id,
            task_type=task_data["task_type"],
            title=task_data["title"],
            description=task_data["description"],
            repository_path=str(self.repo_path),
            points=task_data["points"],
            deadline_epoch=task_data["deadline_epoch"],
            mvs=mvs,
            evidence_required=task_data.get("evidence_required_json") and __import__("json").loads(task_data["evidence_required_json"]) or [],
        )

        # 2. Launch isolated worker
        worker_res, trace, ws_path = self.launcher.launch(envelope)

        # Record attempt
        self.store.record_attempt(
            task_id=task_id,
            attempt_id=f"{task_id}_att1",
            status=worker_res.status,
            elapsed_seconds=worker_res.elapsed_seconds,
            billable_tokens=trace.billable_tokens,
            result_json=worker_res.to_dict(),
        )

        # 3. Deterministic QA Gate validation
        try:
            validate_qa_gate(envelope.to_dict(), worker_res.to_dict(), workspace_dir=ws_path)
        except QAGateError as e:
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            return {"task_id": task_id, "status": "REJECTED", "error": str(e)}

        # 4. If Ready for Review, integrate and submit
        if worker_res.status == "READY_FOR_REVIEW":
            integ_res = self.integration_guard.integrate(worker_res, ws_path)
            if not integ_res.success:
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {"task_id": task_id, "status": "INTEGRATION_FAILED", "error": integ_res.error_message}

            verified_rev = self.git_guard.verify_head(integ_res.commit_sha)

            # Guarded Submission
            sub_res = self.submission_guard.execute_guarded_submission(
                envelope=envelope,
                worker_result=worker_res,
                revision=verified_rev,
                game_master_submit_fn=self.gm.submit,
            )

            final_state = TaskState.SUBMITTED if sub_res.success else TaskState.REJECTED
            self.scheduler.complete_task(task_id, final_state)
            return {"task_id": task_id, "status": sub_res.status, "points": sub_res.points_awarded}

        else:
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            return {"task_id": task_id, "status": worker_res.status}
