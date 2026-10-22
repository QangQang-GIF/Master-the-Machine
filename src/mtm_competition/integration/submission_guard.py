"""SubmissionGuard: Deterministic once-only submission gating and integrity checks."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from mtm_core.contracts import TaskEnvelope, WorkerResult, TaskState
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.state_machine import TaskStateMachine
from mtm_competition.integration.git_guard import VerifiedRevision


class SubmissionGateError(Exception):
    pass


@dataclass
class SubmissionResult:
    success: bool
    task_id: str
    submission_id: Optional[str] = None
    points_awarded: int = 0
    status: str = "PENDING"
    error_message: Optional[str] = None


class SubmissionGuard:
    """Enforces zero-duplicate, fully-verified submission gating."""

    def __init__(self, task_store: TaskStore):
        self.store = task_store

    def validate_pre_submission(
        self,
        envelope: TaskEnvelope,
        worker_result: WorkerResult,
        revision: VerifiedRevision,
    ) -> None:
        """Run all pre-submission gating checks."""
        task_id = envelope.task_id

        # 1. Idempotency: Has this task already been submitted?
        if self.store.has_submitted(task_id):
            raise SubmissionGateError(f"Task {task_id} has already been submitted! Once-only policy blocks re-submission.")

        # 2. Worker result status must be READY_FOR_REVIEW
        if worker_result.status != "READY_FOR_REVIEW":
            raise SubmissionGateError(f"Cannot submit task {task_id}: status is '{worker_result.status}', expected READY_FOR_REVIEW")

        # 3. Verified revision must exist
        if not revision or not revision.commit_sha:
            raise SubmissionGateError(f"Cannot submit task {task_id}: missing verified Git revision")

        # 4. Deadline check
        if envelope.deadline_epoch and time.time() > envelope.deadline_epoch:
            raise SubmissionGateError(f"Cannot submit task {task_id}: deadline expired at {envelope.deadline_epoch}")

        # 5. Required evidence presence check
        for req_ev in envelope.evidence_required:
            if req_ev not in worker_result.evidence_paths:
                raise SubmissionGateError(f"Cannot submit task {task_id}: missing required evidence '{req_ev}'")

        # 6. Test results must not have any failures
        for tr in worker_result.test_results:
            if not tr.get("passed"):
                raise SubmissionGateError(f"Cannot submit task {task_id}: test failed: {tr.get('command')}")

    def execute_guarded_submission(
        self,
        envelope: TaskEnvelope,
        worker_result: WorkerResult,
        revision: VerifiedRevision,
        game_master_submit_fn: Any,
    ) -> SubmissionResult:
        """Atomically transition state to SUBMITTING, invoke GM submission, and record outcome."""
        self.validate_pre_submission(envelope, worker_result, revision)

        task_id = envelope.task_id
        # Transition state machine to SUBMITTING / READY_TO_SUBMIT
        self.store.update_task_status(task_id, TaskState.READY_TO_SUBMIT)

        try:
            # Call Game Master adapter
            response = game_master_submit_fn(
                task_id=task_id,
                git_revision=revision.commit_sha,
                evidence=worker_result.evidence_paths,
            )
            score = int(response.get("points") or envelope.points if response.get("status") == "ACCEPTED" else 0)
            status = response.get("status", "ACCEPTED")

            # Record in TaskStore once-only ledger
            self.store.record_submission(task_id, status=status, score=score, response_json=response)
            self.store.update_task_status(task_id, TaskState.SUBMITTED, {"score": score})

            return SubmissionResult(
                success=(status in ("ACCEPTED", "PASSED")),
                task_id=task_id,
                points_awarded=score,
                status=status,
            )
        except Exception as e:
            self.store.record_event(task_id, "SUBMISSION_FAILED", TaskState.READY_TO_SUBMIT.value, TaskState.REJECTED.value, {"error": str(e)})
            return SubmissionResult(
                success=False,
                task_id=task_id,
                error_message=str(e),
                status="FAILED",
            )
