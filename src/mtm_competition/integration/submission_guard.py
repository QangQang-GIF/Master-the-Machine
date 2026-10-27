"""SubmissionGuard: Deterministic once-only submission gating and integrity checks."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import TaskEnvelope, WorkerResult, TaskState
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.tasks.state_machine import TaskStateMachine
from mtm_competition.integration.git_guard import (
    GitGuard,
    PreSubmissionGitGuard,
    SubmissionGateError,
    ValidationResult,
    VerifiedRevision,
)
from mtm_competition.integration.integration_guard import is_forbidden_path


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
    if raw is None:
        raw = resp_data.get("score")
    if raw is not None:
        return int(raw)
    if status == "PASSED":
        return int(envelope_points)
    return 0


class SubmissionGuard:
    """Enforces zero-duplicate, fully-verified submission gating with server-clock awareness and in-flight reconciliation."""

    def __init__(
        self,
        task_store: TaskStore,
        strict_git_gate: bool = False,
        require_remote: bool = False,
        journal: Optional[SubmissionJournal] = None,
    ):
        self.store = task_store
        self.strict_git_gate = strict_git_gate
        self.require_remote = require_remote
        self.journal = (
            journal
            or getattr(task_store, "journal", None)
            or SubmissionJournal(conn=getattr(task_store, "conn", None), db_path=getattr(task_store, "db_path", ":memory:"))
        )

    def validate_pre_submission(
        self,
        envelope: TaskEnvelope,
        worker_result: WorkerResult,
        revision: VerifiedRevision,
        current_time: Optional[float] = None,
        answer_payload: Optional[Dict[str, Any]] = None,
        assigned_branch_ref: Optional[str] = None,
        remote_name: str = "origin",
        require_remote: Optional[bool] = None,
        match_root: Optional[Path | str] = None,
        checkout_root: Optional[Path | str] = None,
        strict_git_gate: Optional[bool] = None,
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

        # 7. Check for forbidden files in worker changes (GIT-04)
        for changed_f in worker_result.changed_files:
            forbidden, reason = is_forbidden_path(changed_f)
            if forbidden:
                raise SubmissionGateError(f"Cannot submit task {task_id}: forbidden file '{changed_f}' in worker result ({reason})")

        for ev_f in worker_result.evidence_paths:
            forbidden, reason = is_forbidden_path(ev_f)
            if forbidden:
                raise SubmissionGateError(f"Cannot submit task {task_id}: forbidden file '{ev_f}' in declared evidence ({reason})")

        # 8. Git Tree Hard Gate (GIT-01, GIT-02, GIT-03, GIT-05)
        repo_path = Path(envelope.repository_path).resolve() if envelope.repository_path else None
        repo_has_git = repo_path is not None and (repo_path / ".git").exists()

        effective_strict = self.strict_git_gate if strict_git_gate is None else strict_git_gate
        effective_answer = answer_payload or getattr(worker_result, "answer", None)
        has_submit_json_on_disk = repo_has_git and (repo_path / "answers" / task_id / "submit.json").exists()

        if repo_has_git and (effective_strict or effective_answer is not None or has_submit_json_on_disk):
            effective_branch = assigned_branch_ref or getattr(revision, "branch", "main") or "main"
            eff_remote = require_remote if require_remote is not None else self.require_remote
            needs_remote = eff_remote or getattr(revision, "is_pushed", False)

            git_gate = PreSubmissionGitGuard(repo_path)
            val_res = git_gate.validate_pre_submission(
                repo_path=repo_path,
                target_commit_sha=revision.commit_sha,
                remote_name=remote_name,
                assigned_branch_ref=effective_branch,
                backlog_item_key=task_id,
                answer_payload=effective_answer,
                evidence_files=worker_result.evidence_paths,
                match_root=match_root,
                checkout_root=checkout_root,
                require_remote=needs_remote,
                raise_on_error=False,
            )
            if not val_res.valid:
                raise SubmissionGateError(f"Pre-submission Git gate failed for task {task_id}: {val_res.error_message}")

    def execute_guarded_submission(
        self,
        envelope: TaskEnvelope,
        worker_result: WorkerResult,
        revision: VerifiedRevision,
        game_master_submit_fn: Any,
        game_master_status_fn: Optional[Any] = None,
        current_time: Optional[float] = None,
        answer_payload: Optional[Dict[str, Any]] = None,
        assigned_branch_ref: Optional[str] = None,
        remote_name: str = "origin",
        require_remote: Optional[bool] = None,
        match_root: Optional[Path | str] = None,
        checkout_root: Optional[Path | str] = None,
        strict_git_gate: Optional[bool] = None,
        match_uuid: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        client_submission_id: Optional[str] = None,
        artifact_urls: Optional[List[str]] = None,
    ) -> SubmissionResult:
        """Atomically record in-flight journal and execute guarded submission with idempotent replay."""
        task_id = envelope.task_id
        target_match_uuid = (
            match_uuid
            or (envelope.metadata.get("match_uuid") if envelope.metadata else None)
            or (envelope.metadata.get("matchId") if envelope.metadata else None)
            or (self.store.get_current_match_id() if hasattr(self.store, "get_current_match_id") else None)
            or "00000000-0000-0000-0000-000000000000"
        )
        effective_branch = assigned_branch_ref or getattr(revision, "branch", "main") or "main"

        # Reconciliation: If task is already marked in-flight, check server status first
        if self.store.is_in_flight(task_id) and game_master_status_fn:
            try:
                server_status = game_master_status_fn(task_id)
                st = server_status.get("status")
                if st in ("ACCEPTED", "PASSED", "FAILED"):
                    pts = _extract_submission_score(server_status, envelope.points, st)
                    if st == "ACCEPTED":
                        self.journal.record_receipt(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            receipt_id=server_status.get("submission_id") or str(uuid.uuid4()),
                            http_code=201,
                        )
                    elif st == "PASSED":
                        self.journal.record_receipt(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            receipt_id=server_status.get("submission_id") or str(uuid.uuid4()),
                            http_code=200,
                        )
                    else:
                        self.journal.record_failure(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            http_code=400,
                        )
                    self.store.record_submission(
                        task_id,
                        status=st,
                        score=pts,
                        response_json=server_status,
                        git_revision=revision.commit_sha,
                        submission_id=server_status.get("submission_id"),
                        score_status=server_status.get("scoreStatus", "PENDING" if st == "ACCEPTED" else None),
                    )
                    return SubmissionResult(
                        success=(st in ("ACCEPTED", "PASSED")),
                        task_id=task_id,
                        points_awarded=pts,
                        status=st,
                    )
            except Exception:
                pass

        # Check existing in-flight attempt in SubmissionJournal
        existing_in_flight = self.journal.get_in_flight(target_match_uuid, task_id)
        if existing_in_flight:
            # Enforce API-06: reject generating a new UUID for an unresolved attempt
            if client_submission_id and client_submission_id.strip() != existing_in_flight["client_submission_id"]:
                raise SubmissionGateError(
                    f"Unresolved in-flight submission exists for {task_id} with clientSubmissionId "
                    f"'{existing_in_flight['client_submission_id']}'. Rejecting generation of new UUID '{client_submission_id}'."
                )
            if idempotency_key and idempotency_key.strip() != existing_in_flight["idempotency_key"]:
                raise SubmissionGateError(
                    f"Unresolved in-flight submission exists for {task_id} with idempotencyKey "
                    f"'{existing_in_flight['idempotency_key']}'. Rejecting generation of new key '{idempotency_key}'."
                )
            client_sub_id = existing_in_flight["client_submission_id"]
            idemp_key = existing_in_flight["idempotency_key"]
            serialized_body_bytes = existing_in_flight["request_body_bytes"]

            # If new answer provided, verify body is not altered
            if answer_payload is not None:
                temp_payload = {
                    "clientSubmissionId": client_sub_id,
                    "backlogItemKey": task_id,
                    "gitRevision": revision.commit_sha,
                    "answer": answer_payload,
                }
                if artifact_urls:
                    temp_payload["artifactUrls"] = artifact_urls
                temp_bytes = json.dumps(temp_payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
                try:
                    self.journal.verify_or_abort(target_match_uuid, task_id, temp_bytes)
                except ValueError as ve:
                    raise SubmissionGateError(str(ve)) from ve
            effective_answer = answer_payload
        else:
            provided_answer = answer_payload or getattr(worker_result, "answer", None)
            repo_path = Path(envelope.repository_path).resolve() if envelope.repository_path else None
            disk_answer = None
            if provided_answer is None and repo_path and (repo_path / "answers" / task_id / "submit.json").exists():
                try:
                    disk_answer = json.loads((repo_path / "answers" / task_id / "submit.json").read_text(encoding="utf-8"))
                except Exception:
                    pass

            gate_answer = provided_answer if provided_answer is not None else disk_answer

            self.validate_pre_submission(
                envelope=envelope,
                worker_result=worker_result,
                revision=revision,
                current_time=current_time,
                answer_payload=gate_answer,
                assigned_branch_ref=effective_branch,
                remote_name=remote_name,
                require_remote=require_remote,
                match_root=match_root,
                checkout_root=checkout_root,
                strict_git_gate=strict_git_gate,
            )

            effective_answer = gate_answer if gate_answer is not None else {"codeChanges": True, "evidence": worker_result.evidence_paths}

            client_sub_id = client_submission_id.strip() if client_submission_id else str(uuid.uuid4())
            idemp_key = idempotency_key.strip() if idempotency_key else client_sub_id

            # Prepare canonical serialized request bytes: json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')
            payload = {
                "clientSubmissionId": client_sub_id,
                "backlogItemKey": task_id,
                "gitRevision": revision.commit_sha,
                "answer": effective_answer,
            }
            if artifact_urls:
                payload["artifactUrls"] = artifact_urls
            serialized_body_bytes = json.dumps(payload, sort_keys=True, separators=(',', ':')).encode('utf-8')

            # Persist record in SubmissionJournal before outbound network call
            self.journal.record_in_flight(
                match_uuid=target_match_uuid,
                backlog_item_key=task_id,
                client_submission_id=client_sub_id,
                idempotency_key=idemp_key,
                request_body_bytes=serialized_body_bytes,
                git_revision=revision.commit_sha,
                remote_ref=effective_branch,
            )
            # Transition state machine and record atomic in-flight marker in TaskStore
            self.store.start_submission(task_id, git_revision=revision.commit_sha)

        # Outbound HTTP dispatch with idempotent replay on timeout/429/5xx
        def _dispatch_attempt() -> Any:
            try:
                import inspect
                sig = inspect.signature(game_master_submit_fn)
                if "backlog_item_key" in sig.parameters:
                    return game_master_submit_fn(
                        match_uuid=target_match_uuid,
                        client_submission_id=client_sub_id,
                        idempotency_key=idemp_key,
                        backlog_item_key=task_id,
                        git_revision=revision.commit_sha,
                        answer=effective_answer,
                        artifact_urls=artifact_urls,
                        serialized_body_bytes=serialized_body_bytes,
                    )
            except Exception:
                pass

            call_kwargs = {
                "task_id": task_id,
                "git_revision": revision.commit_sha,
                "evidence": worker_result.evidence_paths,
            }
            if answer_payload is not None:
                call_kwargs["answer"] = answer_payload
                if idempotency_key is not None:
                    call_kwargs["idempotency_key"] = idemp_key
                if client_submission_id is not None:
                    call_kwargs["client_submission_id"] = client_sub_id
                if match_uuid is not None:
                    call_kwargs["match_uuid"] = target_match_uuid
                if artifact_urls is not None:
                    call_kwargs["artifact_urls"] = artifact_urls
                try:
                    import inspect
                    sig = inspect.signature(game_master_submit_fn)
                    if "serialized_body_bytes" in sig.parameters:
                        call_kwargs["serialized_body_bytes"] = serialized_body_bytes
                except Exception:
                    pass

            try:
                return game_master_submit_fn(**call_kwargs)
            except TypeError:
                call_kwargs.pop("serialized_body_bytes", None)
                call_kwargs.pop("artifact_urls", None)
                call_kwargs.pop("client_submission_id", None)
                call_kwargs.pop("match_uuid", None)
                call_kwargs.pop("idempotency_key", None)
                call_kwargs.pop("answer", None)
                return game_master_submit_fn(**call_kwargs)

        response = None
        last_error = None
        try:
            response = _dispatch_attempt()
        except Exception as exc:
            last_error = exc

        if response is not None:
            sub_data = response.get("submission") if isinstance(response.get("submission"), dict) else response
            status = sub_data.get("status", "ACCEPTED")
            sub_id = sub_data.get("id") or response.get("submission_id") or client_sub_id

            # On HTTP 201 receipt: update journal and store with status="ACCEPTED", score=0 (pending final grading). Do not mark PASSED.
            if status == "ACCEPTED":
                legacy_raw = (response.get("points") if response.get("points") is not None else response.get("score")) if "submission" not in response else None
                store_score = int(legacy_raw) if legacy_raw is not None else 0

                self.journal.record_receipt(
                    match_uuid=target_match_uuid,
                    backlog_item_key=task_id,
                    receipt_id=sub_id,
                    http_code=201,
                )
                self.store.record_submission(
                    task_id=task_id,
                    status="ACCEPTED",
                    score=store_score,
                    response_json=response,
                    git_revision=revision.commit_sha,
                    submission_id=sub_id,
                    score_status="PENDING",
                )
                return SubmissionResult(
                    success=True,
                    task_id=task_id,
                    submission_id=sub_id,
                    points_awarded=store_score,
                    status="ACCEPTED",
                )
            else:
                score = _extract_submission_score(response, envelope.points, status)
                if status == "PASSED":
                    self.journal.record_receipt(
                        match_uuid=target_match_uuid,
                        backlog_item_key=task_id,
                        receipt_id=sub_id,
                        http_code=200,
                    )
                else:
                    self.journal.record_failure(
                        match_uuid=target_match_uuid,
                        backlog_item_key=task_id,
                        http_code=getattr(last_error, "status_code", 400),
                    )

                self.store.record_submission(
                    task_id=task_id,
                    status=status,
                    score=score,
                    response_json=response,
                    git_revision=revision.commit_sha,
                    submission_id=sub_id,
                )
                return SubmissionResult(
                    success=(status == "PASSED"),
                    task_id=task_id,
                    submission_id=sub_id,
                    points_awarded=score,
                    status=status,
                )

        # If we reached here, an exception occurred in dispatch
        e = last_error
        # 1. Distinguish 4xx server rejection from network timeout/loss
        status_code = getattr(e, "status_code", None)
        error_code = getattr(e, "error_code", None)
        if status_code is not None and 400 <= status_code < 500 and status_code != 429:
            self.journal.record_failure(
                match_uuid=target_match_uuid,
                backlog_item_key=task_id,
                http_code=status_code,
                error_code=str(error_code or e),
            )
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
                    pts = _extract_submission_score(reconciled, envelope.points, st)
                    if st == "ACCEPTED":
                        self.journal.record_receipt(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            receipt_id=reconciled.get("submission_id", client_sub_id),
                            http_code=201,
                        )
                    elif st == "PASSED":
                        self.journal.record_receipt(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            receipt_id=reconciled.get("submission_id", client_sub_id),
                            http_code=200,
                        )
                    else:
                        self.journal.record_failure(
                            match_uuid=target_match_uuid,
                            backlog_item_key=task_id,
                            http_code=status_code,
                            error_code=str(error_code or e),
                        )

                    self.store.record_submission(
                        task_id=task_id,
                        status=st,
                        score=pts,
                        response_json=reconciled,
                        git_revision=revision.commit_sha,
                        submission_id=reconciled.get("submission_id"),
                        score_status=reconciled.get("scoreStatus", "PENDING" if st == "ACCEPTED" else None),
                    )
                    return SubmissionResult(
                        success=(st in ("ACCEPTED", "PASSED")),
                        task_id=task_id,
                        points_awarded=pts,
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
            {
                "error": str(e),
                "action": "retain_in_flight_for_reconciliation",
                "client_submission_id": client_sub_id,
                "idempotency_key": idemp_key,
            },
        )
        return SubmissionResult(
            success=False,
            task_id=task_id,
            error_message=f"Submission timeout/network error; preserved in SUBMIT_IN_FLIGHT: {e}",
            status="SUBMIT_IN_FLIGHT",
        )
