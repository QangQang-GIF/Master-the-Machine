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


def _extract_submission_score(resp_data: Dict[str, Any], envelope_points: int, status: str) -> int:
    """Extract awarded score distinguishing 0 from None. Only PASSED awards default points if omitted."""
    raw = resp_data.get("points")
    if raw is not None:
        return int(raw)
    if status == "PASSED":
        return int(envelope_points)
    return 0


class SubmissionGuard:
    """Enforces zero-duplicate, fully-verified submission gating with server-clock awareness and in-flight reconciliation."""

    def __init__(self, task_store: TaskStore):
        self.store = task_store

    def validate_pre_submission(
        self,
        envelope: TaskEnvelope,
        worker_result: WorkerResult,
        revision: VerifiedRevision,
        current_time: Optional[float] = None,
    ) -> None:
        """Run all pre-submission gating checks."""
        task_id = envelope.task_id
        now = current_time if current_time is not None else time.time()

        # 1. Idempotency: Has this task already been submitted?
        # Note: If it is marked in-flight, execute_guarded_submission reconciles with GM.
        if self.store.has_submitted(task_id) and not self.store.is_in_flight(task_id):
            raise SubmissionGateError(f"Task {task_id} has already been submitted! Once-only policy blocks re-submission.")

        # 2. Worker result status must be READY_FOR_REVIEW
        if worker_result.status != "READY_FOR_REVIEW":
            raise SubmissionGateError(f"Cannot submit task {task_id}: status is '{worker_result.status}', expected READY_FOR_REVIEW")

        # 3. Verified revision must exist
        if not revision or not revision.commit_sha:
            raise SubmissionGateError(f"Cannot submit task {task_id}: missing verified Git revision")

        # 4. Deadline check using server/calibrated time
        if envelope.deadline_epoch and now > envelope.deadline_epoch:
            raise SubmissionGateError(f"Cannot submit task {task_id}: deadline expired at {envelope.deadline_epoch} (current time {now})")

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
        game_master_status_fn: Optional[Any] = None,
        current_time: Optional[float] = None,
    ) -> SubmissionResult:
        """Atomically record SUBMIT_IN_FLIGHT, invoke GM submission, and record outcome with timeout reconciliation."""
        task_id = envelope.task_id

        # Reconciliation: If task is already marked in-flight, check server status first
        if self.store.is_in_flight(task_id) and game_master_status_fn:
            try:
                server_status = game_master_status_fn(task_id)
                st = server_status.get("status")
                if st in ("ACCEPTED", "PASSED", "FAILED"):
                    pts = _extract_submission_score(server_status, envelope.points, st)
                    self.store.record_submission(task_id, status=st, score=pts, response_json=server_status, git_revision=revision.commit_sha)
                    return SubmissionResult(
                        success=(st in ("ACCEPTED", "PASSED")),
                        task_id=task_id,
                        points_awarded=pts,
                        status=st,
                    )
            except Exception:
                pass

        self.validate_pre_submission(envelope, worker_result, revision, current_time=current_time)

        # Transition state machine and record atomic in-flight marker
        self.store.start_submission(task_id, git_revision=revision.commit_sha)

        try:
            # Call Game Master adapter (single-shot, no automatic retry on POST /submit)
            response = game_master_submit_fn(
                task_id=task_id,
                git_revision=revision.commit_sha,
                evidence=worker_result.evidence_paths,
            )
            status = response.get("status", "ACCEPTED")
            score = _extract_submission_score(response, envelope.points, status)
            sub_id = response.get("submission_id")

            # Record in TaskStore once-only ledger
            self.store.record_submission(
                task_id=task_id,
                status=status,
                score=score,
                response_json=response,
                git_revision=revision.commit_sha,
                submission_id=sub_id,
            )

            return SubmissionResult(
                success=(status in ("ACCEPTED", "PASSED")),
                task_id=task_id,
                submission_id=sub_id,
                points_awarded=score,
                status=status,
            )
        except Exception as e:
            # 1. Distinguish 4xx server rejection from network timeout/loss
            status_code = getattr(e, "status_code", None)
            if status_code is not None and 400 <= status_code < 500:
                self.store.record_submission(
                    task_id=task_id,
                    status="REJECTED",
                    score=0,
                    response_json=getattr(e, "payload", {"error": str(e)}),
                    git_revision=revision.commit_sha,
                )
                return SubmissionResult(
                    success=False,
                    task_id=task_id,
                    error_message=str(e),
                    status="REJECTED",
                )

            # 2. Timeout or network error: check server to see if it landed
            if game_master_status_fn:
                try:
                    reconciled = game_master_status_fn(task_id)
                    st = reconciled.get("status")
                    if st in ("ACCEPTED", "PASSED", "FAILED"):
                        score = _extract_submission_score(reconciled, envelope.points, st)
                        self.store.record_submission(
                            task_id=task_id,
                            status=st,
                            score=score,
                            response_json=reconciled,
                            git_revision=revision.commit_sha,
                            submission_id=reconciled.get("submission_id"),
                        )
                        return SubmissionResult(
                            success=(st in ("ACCEPTED", "PASSED")),
                            task_id=task_id,
                            points_awarded=score,
                            status=st,
                        )
                except Exception:
                    pass

            # 3. Server status indeterminate: keep in SUBMIT_IN_FLIGHT for reconciliation loop
            self.store.record_event(
                task_id,
                "SUBMISSION_TIMEOUT_IN_FLIGHT",
                TaskState.SUBMIT_IN_FLIGHT.value,
                TaskState.SUBMIT_IN_FLIGHT.value,
                {"error": str(e), "action": "retain_in_flight_for_reconciliation"},
            )
            return SubmissionResult(
                success=False,
                task_id=task_id,
                error_message=f"Submission timeout/network error; preserved in SUBMIT_IN_FLIGHT: {e}",
                status="SUBMIT_IN_FLIGHT",
            )
