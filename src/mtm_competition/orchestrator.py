"""CompetitionOrchestrator: Autonomous control plane for the MTM 2026 Tournament."""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import TaskEnvelope, WorkerResult, TaskState
from mtm_competition.tasks.store import TaskStore
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.workers.launcher import WorkerLauncher
from mtm_competition.workers.supervisor import WorkerSupervisor
from mtm_competition.verification.qa_gate import validate_qa_gate, QAGateError
from mtm_competition.integration.integration_guard import IntegrationGuard
from mtm_competition.integration.git_guard import GitGuard, VerifiedRevision
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.adapters.base import GameMasterAdapter

logger = logging.getLogger("mtm.orchestrator")


def _is_transient_gm_error(exc: Exception) -> bool:
    """Classify whether a Game Master exception is transient (retryable/requeueable) vs permanent."""
    if isinstance(exc, (KeyError, ValueError, TypeError)):
        return False
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True

    cls_name = exc.__class__.__name__
    if cls_name in ("MVSLockedError", "GameMasterRateLimitError", "GameMasterConnectionError"):
        return True
    if cls_name in ("GameMasterAuthError", "GameMasterValidationError", "GameMasterClientError"):
        return False

    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        if status_code in (408, 425, 429) or status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False

    msg = str(exc).lower()
    permanent_markers = (
        "http 400", "http 401", "http 403", "http 404", "http 409", "http 410", "http 422",
        " 400 ", " 401 ", " 403 ", " 404 ", " 409 ", " 422 ",
        "bad request", "unauthorized", "forbidden", "not found", "conflict",
        "invalid task", "already submitted", "unknown task",
    )
    if any(m in msg for m in permanent_markers):
        return False

    transient_markers = (
        "500", "502", "503", "504", "429", "408",
        "service unavailable", "bad gateway", "gateway timeout", "too many requests",
        "rate limit", "temporarily unavailable", "timeout", "timed out",
        "connection", "network", "locked", "mvs",
    )
    if any(m in msg for m in transient_markers):
        return True

    return False


class CompetitionOrchestrator:
    """Master controller connecting Game Master, Scheduler, Workers, and Submission Guards."""

    def __init__(
        self,
        gm_adapter: GameMasterAdapter,
        repository_path: Path | str,
        scratch_dir: Path | str,
        db_path: Optional[Path | str] = None,
        concurrency_limit: int = 1,
        worker_backend: Optional[str] = None,
        verify_skill_integrity: bool = False,
        push_to_remote: bool = True,
        verify_post_integration: bool = True,
        reverify_worker_tests: bool = False,
        require_remote: bool = False,
        token_budget: int = 1_000_000,
        start_working_max_retries: int = 2,
        start_working_backoff_sec: float = 0.25,
        start_working_max_requeues: int = 2,
        start_working_cooldown_sec: float = 15.0,
    ):
        self.gm = gm_adapter
        self.repo_path = Path(repository_path).resolve()
        self.scratch_dir = Path(scratch_dir).resolve()
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        self.start_working_max_retries = max(0, int(start_working_max_retries))
        self.start_working_backoff_sec = max(0.0, float(start_working_backoff_sec))
        self.start_working_max_requeues = max(0, int(start_working_max_requeues))
        self.start_working_cooldown_sec = max(0.0, float(start_working_cooldown_sec))

        if db_path is None:
            self.db_path = str(self.scratch_dir / "tournament.db")
        else:
            self.db_path = str(db_path)

        self.store = TaskStore(self.db_path)
        self.scheduler = TournamentScheduler(self.store, concurrency_limit=concurrency_limit)
        self.verify_post_integration = verify_post_integration
        self.reverify_worker_tests = reverify_worker_tests

        # Resolve backend configuration from argument, env var, or config file
        from mtm_core.config.loader import load_environment_config
        env_cfg = load_environment_config()
        self.worker_backend = (
            worker_backend
            or __import__("os").environ.get("MTM_WORKER_BACKEND")
            or __import__("os").environ.get("MTM_BACKEND")
            or env_cfg.get("orchestrator", {}).get("worker_backend", "codex")
        ).lower()

        if self.worker_backend not in ("codex", "antigravity", "auto"):
            raise ValueError(
                f"Invalid backend '{self.worker_backend}'. Explicit backend must be 'codex' or 'antigravity'"
            )

        strict_backend = env_cfg.get("worker", {}).get("strict_backend", True)
        allow_fallback = env_cfg.get("worker", {}).get("allow_fallback", False)

        self.launcher = WorkerLauncher(
            self.scratch_dir,
            backend=self.worker_backend,
            strict_backend=strict_backend,
            allow_fallback=allow_fallback,
        )
        self.supervisor = WorkerSupervisor(max_workers=concurrency_limit)
        self.integration_guard = IntegrationGuard(
            self.repo_path,
            push_to_remote=push_to_remote,
            verify_post_integration=verify_post_integration,
            require_remote=require_remote,
        )
        self.git_guard = GitGuard(self.repo_path)
        self.submission_guard = SubmissionGuard(self.store)

        self.cumulative_billable_tokens: int = self.store.get_total_billable_tokens()
        self.token_budget = token_budget

        mode = (
            __import__("os").environ.get("MTM_ENV")
            or env_cfg.get("mode")
            or env_cfg.get("orchestrator", {}).get("mode", "development")
        ).lower()
        if mode == "production":
            self.verify_skill_integrity = True
            self.reverify_worker_tests = True
            self.verify_post_integration = True
            self.integration_guard.push_to_remote = True
            self.integration_guard.require_remote = True
        else:
            self.verify_skill_integrity = verify_skill_integrity

        if self.verify_skill_integrity:
            from mtm_core.config.loader import verify_skill_checksums
            project_root = self.repo_path if (self.repo_path / "releases").exists() else Path.cwd()
            verify_skill_checksums(project_root)

        # Reconcile any orphaned tasks, in-flight submissions, and grading lifecycle remaining from crash/restart
        status_fn = getattr(self.gm, "get_submission_status", None)
        try:
            self.store.reconcile_orphaned_tasks(status_check_fn=status_fn)
        except Exception as e:
            logger.warning("Could not reconcile orphaned tasks on startup: %s", e)

        if status_fn is not None:
            try:
                self.store.reconcile_in_flight_submissions(status_fn)
                self.store.reconcile_grading_lifecycle(status_fn)
            except Exception as e:
                logger.warning("Could not reconcile in-flight submissions on startup: %s", e)

    def _call_gm_with_retry(self, fn: Any) -> Any:
        """Execute a Game Master RPC with bounded exponential backoff on transient failures."""
        max_attempts = 1 + self.start_working_max_retries
        last_exc: Optional[Exception] = None
        for attempt_idx in range(max_attempts):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if not _is_transient_gm_error(exc) or attempt_idx >= max_attempts - 1:
                    raise
                if self.start_working_backoff_sec > 0:
                    time.sleep(self.start_working_backoff_sec * (2 ** attempt_idx))
        if last_exc is not None:
            raise last_exc
        return None

    def _resume_ready_to_submit(self, task_data: Dict[str, Any]) -> Dict[str, Any]:
        """Resume a task already in READY_TO_SUBMIT directly at integration/submission without re-running worker."""
        task_id = task_data["task_id"]
        fencing_token = self.store.issue_fencing_token(task_id)
        t_stage_start = time.time()

        evidence_req = []
        if task_data.get("evidence_required_json"):
            try:
                evidence_req = json.loads(task_data["evidence_required_json"])
            except Exception:
                pass

        envelope = TaskEnvelope(
            task_id=task_id,
            task_type=task_data["task_type"],
            title=task_data["title"],
            description=task_data["description"],
            repository_path=str(self.repo_path),
            points=task_data["points"],
            deadline_epoch=task_data["deadline_epoch"],
            time_budget_seconds=int(task_data.get("time_budget_seconds") or 420),
            evidence_required=evidence_req,
            backend=self.worker_backend,
        )

        raw_res_json = task_data.get("verified_result_json")
        if raw_res_json:
            try:
                worker_res = WorkerResult.from_dict(json.loads(raw_res_json))
            except Exception:
                worker_res = WorkerResult(task_id=task_id, status="READY_FOR_REVIEW")
        else:
            worker_res = WorkerResult(task_id=task_id, status="READY_FOR_REVIEW")

        saved_rev = task_data.get("verified_revision")
        saved_ws = task_data.get("verified_workspace")
        pushed_flag = True

        if saved_rev:
            verified_rev = VerifiedRevision(commit_sha=saved_rev, branch="main", is_clean=True)
        else:
            ws_dir = Path(saved_ws) if saved_ws else self.repo_path
            integ_res = self.integration_guard.integrate(worker_res, ws_dir)
            if not integ_res.success:
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "INTEGRATION_FAILED",
                    "error": integ_res.error_message,
                }
            pushed_flag = integ_res.pushed
            verified_rev = VerifiedRevision(commit_sha=integ_res.commit_sha or "HEAD", branch="main", is_clean=True)

        if not self.store.validate_fencing_token(task_id, fencing_token):
            self.scheduler.requeue_task(task_id)
            return {"task_id": task_id, "status": "FENCED_OFF"}

        sub_res = self.submission_guard.execute_guarded_submission(
            envelope=envelope,
            worker_result=worker_res,
            revision=verified_rev,
            game_master_submit_fn=self.gm.submit,
            game_master_status_fn=getattr(self.gm, "get_submission_status", None),
            current_time=self.scheduler.current_server_time,
        )

        final_status = sub_res.status
        final_points = sub_res.points_awarded
        if sub_res.success and final_status not in ("PASSED", "FAILED") and hasattr(self.gm, "get_submission_status"):
            try:
                grading = self.gm.get_submission_status(task_id)
                if isinstance(grading, dict) and isinstance(grading.get("status"), str):
                    g_status = grading.get("status")
                    if g_status in ("PASSED", "FAILED"):
                        final_status = g_status
                        raw_pts = grading.get("points") if grading.get("points") is not None else grading.get("score")
                        if g_status == "PASSED":
                            final_points = int(raw_pts) if raw_pts is not None else (final_points or envelope.points)
                        else:
                            final_points = int(raw_pts) if raw_pts is not None else 0
                        self.store.update_submission_status(task_id, g_status, final_points, grading)
            except Exception:
                pass

        if final_status == "PASSED":
            final_state = TaskState.PASSED
        elif final_status == "FAILED":
            final_state = TaskState.FAILED
        elif sub_res.success:
            final_state = TaskState.SUBMITTED
        else:
            final_state = TaskState.REJECTED

        self.scheduler.complete_task(task_id, final_state)
        self.store.record_event(
            task_id,
            "READY_TO_SUBMIT_RESUMED",
            TaskState.READY_TO_SUBMIT.value,
            final_state.value,
            {"total_duration": round(time.time() - t_stage_start, 2), "billable_tokens": 0},
        )
        return {
            "task_id": task_id,
            "status": final_status,
            "points": final_points,
            "pushed": pushed_flag,
        }

    def sync_backlog(self) -> int:
        """Poll backlog from Game Master, sync server clock, and register new items into TaskStore."""
        # 1. Sync server clock and match status
        try:
            match_state = self.gm.get_match_state()
            self.scheduler.sync_with_game_master(match_state)
        except Exception as e:
            logger.warning("Could not sync match state from Game Master: %s", e)

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
                    backend=self.worker_backend,
                )
                self.store.add_task(env)
                count += 1

                # 2. Check preemption: Does an incoming PI task require stopping a lower-priority task?
                preempt_task_id = self.scheduler.check_preemption(item)
                if preempt_task_id:
                    logger.warning("Preemption triggered by %s on %s", task_id, preempt_task_id)
                    preempted_proc = self.supervisor.preempt_worker(preempt_task_id)
                    if not preempted_proc or preempted_proc.status != "COMPLETED":
                        self.scheduler.requeue_task(preempt_task_id)
                        self.store.record_event(
                            preempt_task_id,
                            "TASK_PREEMPTED",
                            TaskState.RUNNING.value,
                            TaskState.QUEUED.value,
                            {"preempted_by": task_id},
                        )

        return count

    def step(self) -> Optional[Dict[str, Any]]:
        """Perform one discrete orchestration cycle.

        1. Select next task from scheduler.
        2. Unlock MVS from Game Master via start_working.
        3. Launch isolated worker with stage timing and token accounting.
        4. Validate outputs via QA Gate.
        5. Integrate patch, verify git revision, and submit via Submission Guard.
        6. Confirm two-phase grading status.
        """
        # Periodic grading reconciliation loop
        if hasattr(self.gm, "get_submission_status"):
            try:
                self.store.reconcile_grading_lifecycle(self.gm.get_submission_status)
            except Exception as e:
                logger.debug("Grading reconciliation error: %s", e)

        # 0. Sync match state before step
        try:
            match_state = self.gm.get_match_state()
            self.scheduler.sync_with_game_master(match_state)
            if self.scheduler.is_paused:
                return {"status": "PAUSED"}
            if self.scheduler.match_status == "FINISHED":
                self.supervisor.terminate_all("CANCELLED")
                return {"status": "FINISHED"}
        except Exception:
            pass

        task_data = self.scheduler.select_next_task()
        if not task_data:
            return None

        task_id = task_data["task_id"]
        self.scheduler.assign_task(task_id)

        # Direct READY_TO_SUBMIT recovery path (do not re-run worker or start_working)
        if task_data.get("status") == TaskState.READY_TO_SUBMIT.value:
            return self._resume_ready_to_submit(task_data)

        t_stage_start = time.time()
        fencing_token = self.store.issue_fencing_token(task_id)

        # 1. Signal Game Master and unlock MVS with transient vs permanent error classification & retry
        try:
            self._call_gm_with_retry(lambda: self.gm.start_working(task_id))
            mvs = self._call_gm_with_retry(lambda: self.gm.get_mvs(task_id))
        except Exception as e:
            self.store.invalidate_fencing_token(task_id, reason="start_working_failed")
            if _is_transient_gm_error(e):
                requeue_count = self.store.increment_start_working_requeues(task_id)
                if requeue_count <= self.start_working_max_requeues:
                    self.scheduler.requeue_task(task_id)
                    self.scheduler.set_cooldown(task_id, self.start_working_cooldown_sec)
                    self.store.record_event(
                        task_id,
                        "START_WORKING_TRANSIENT_REQUEUED",
                        TaskState.RUNNING.value,
                        TaskState.QUEUED.value,
                        {"error": str(e), "requeue_count": requeue_count},
                    )
                    return {
                        "task_id": task_id,
                        "status": "TRANSIENT_RETRY_QUEUED",
                        "error": str(e),
                        "requeue_count": requeue_count,
                    }

            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            self.store.record_event(
                task_id, "START_WORKING_FAILED", TaskState.RUNNING.value, TaskState.REJECTED.value, {"error": str(e)}
            )
            return {"task_id": task_id, "status": "FAILED", "error": f"Failed to start working on Game Master: {e}"}

        evidence_req = []
        if task_data.get("evidence_required_json"):
            try:
                evidence_req = json.loads(task_data["evidence_required_json"])
            except Exception:
                pass

        time_budget = int(task_data.get("time_budget_seconds") or 420)
        envelope = TaskEnvelope(
            task_id=task_id,
            task_type=task_data["task_type"],
            title=task_data["title"],
            description=task_data["description"],
            repository_path=str(self.repo_path),
            points=task_data["points"],
            deadline_epoch=task_data["deadline_epoch"],
            time_budget_seconds=time_budget,
            mvs=mvs,
            evidence_required=evidence_req,
            backend=self.worker_backend,
        )

        # 2. Token Budget Watchdog: block launch if hard budget reached, or downshift effort
        if self.cumulative_billable_tokens >= self.token_budget:
            self.store.invalidate_fencing_token(task_id, reason="budget_exceeded")
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            return {
                "task_id": task_id,
                "status": "BUDGET_EXCEEDED",
                "error": f"Match token budget exceeded: {self.cumulative_billable_tokens} >= {self.token_budget}",
            }

        if self.cumulative_billable_tokens > (self.token_budget * 0.85):
            envelope.reasoning_effort = "low"
            envelope.model = "gpt-6.1-sol" if self.worker_backend == "codex" else "gemini-3.8-flash-low"

        # 3. Supervised worker launch with mid-execution preemption, PID tracking, and match pause awareness
        t_worker_start = time.time()
        attempt_num = self.store.get_attempt_count(task_id) + 1
        supervised_proc = self.supervisor.launch_async(
            self.launcher.launch,
            envelope,
            attempt_number=attempt_num,
            on_pid_callback=lambda pid: self.store.set_active_pid(task_id, pid, fencing_token=fencing_token),
        )

        while not self.supervisor.is_finished(task_id):
            # Sync match state and clock
            try:
                match_state = self.gm.get_match_state()
                self.scheduler.sync_with_game_master(match_state)
            except Exception:
                pass

            if self.scheduler.match_status == "FINISHED":
                self.supervisor.cancel_worker(task_id)
                self.store.invalidate_fencing_token(task_id, reason="match_finished")
                self.scheduler.complete_task(task_id, TaskState.ABORTED)
                return {"task_id": task_id, "status": "FINISHED"}

            if self.scheduler.is_paused:
                # Extend worker timeout while match clock is paused
                supervised_proc.timeout_seconds += 0.2
                time.sleep(0.2)
                continue

            # Poll backlog to detect if high-priority PI arrived that preempts us
            self.sync_backlog()
            if supervised_proc.status == "PREEMPTED":
                self.store.invalidate_fencing_token(task_id, reason="preempted")
                return {"task_id": task_id, "status": "PREEMPTED"}

            if self.supervisor.is_timed_out(task_id):
                self.supervisor.timeout_worker(task_id)
                self.store.invalidate_fencing_token(task_id, reason="timed_out")
                break

            time.sleep(0.05)

        if supervised_proc.status == "PREEMPTED":
            self.store.invalidate_fencing_token(task_id, reason="preempted")
            return {"task_id": task_id, "status": "PREEMPTED"}

        # Retrieve result with status-aware handling
        supervised_proc = self.supervisor.get_process(task_id) or supervised_proc
        try:
            res = self.supervisor.get_result(task_id, timeout=5.0)
        except Exception as e:
            self.store.invalidate_fencing_token(task_id, reason="worker_exception")
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            self.supervisor.unregister_worker(task_id)
            return {"task_id": task_id, "status": "FAILED", "error": str(e)}

        self.store.set_active_pid(task_id, None, fencing_token=fencing_token)

        if res is None:
            # Distinguish based on supervisor's tracked status
            terminal_status = supervised_proc.status
            self.supervisor.unregister_worker(task_id)
            self.store.invalidate_fencing_token(task_id, reason=f"terminal_{terminal_status}")
            att_num = getattr(supervised_proc, "attempt_number", attempt_num)
            unique_att_id = f"{task_id}_att{att_num}_{int(time.time() * 1000)}"
            if terminal_status == "TIMED_OUT":
                self.store.record_attempt(
                    task_id=task_id,
                    attempt_id=unique_att_id,
                    status="TIMED_OUT",
                    attempt_number=att_num,
                    elapsed_seconds=round(time.time() - t_worker_start, 2),
                    billable_tokens=0,
                    result_json={"task_id": task_id, "status": "TIMED_OUT"},
                    fencing_token=fencing_token,
                )
                self.scheduler.complete_task(task_id, TaskState.TIMED_OUT)
                self.store.record_event(
                    task_id,
                    "TASK_TIMED_OUT",
                    TaskState.RUNNING.value,
                    TaskState.TIMED_OUT.value,
                    {"error": "Worker exceeded time budget"},
                )
                return {"task_id": task_id, "status": "TIMED_OUT", "error": "Worker exceeded time budget"}
            elif terminal_status == "PREEMPTED":
                # Ensure running_tasks is cleaned up if preemption occurred outside sync_backlog
                if task_id in self.scheduler.running_tasks:
                    self.scheduler.requeue_task(task_id)
                return {"task_id": task_id, "status": "PREEMPTED"}
            elif terminal_status == "CANCELLED":
                self.scheduler.complete_task(task_id, TaskState.ABORTED)
                return {"task_id": task_id, "status": "CANCELLED", "error": "Worker execution was cancelled"}
            else:
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {"task_id": task_id, "status": "FAILED", "error": f"No result from worker (status: {terminal_status})"}

        worker_res, trace, ws_path = res
        self.supervisor.unregister_worker(task_id)

        worker_duration = round(time.time() - t_worker_start, 2)

        self.cumulative_billable_tokens += trace.billable_tokens

        # Record attempt with unique attempt ID and fencing token
        att_num = getattr(supervised_proc, "attempt_number", attempt_num)
        unique_att_id = f"{task_id}_att{att_num}_{int(time.time() * 1000)}"
        self.store.record_attempt(
            task_id=task_id,
            attempt_id=unique_att_id,
            status=worker_res.status,
            attempt_number=att_num,
            elapsed_seconds=worker_res.elapsed_seconds,
            billable_tokens=trace.billable_tokens,
            result_json=worker_res.to_dict(),
            fencing_token=fencing_token,
        )

        # If worker itself reported TIMED_OUT, transition directly to TIMED_OUT without QA Gate rejection
        if worker_res.status == "TIMED_OUT":
            self.store.invalidate_fencing_token(task_id, reason="worker_timed_out")
            self.scheduler.complete_task(task_id, TaskState.TIMED_OUT)
            self.store.record_event(
                task_id,
                "TASK_TIMED_OUT",
                TaskState.RUNNING.value,
                TaskState.TIMED_OUT.value,
                {"elapsed_seconds": worker_res.elapsed_seconds},
            )
            return {"task_id": task_id, "status": "TIMED_OUT", "error": "Worker execution timed out"}

        # Validate fencing token and transition RUNNING -> VERIFYING before QA Gate
        try:
            self.store.transition_state(
                task_id,
                TaskState.VERIFYING,
                expected_current=(TaskState.RUNNING,),
                fencing_token=fencing_token,
            )
        except ValueError as e:
            self.scheduler.requeue_task(task_id)
            return {"task_id": task_id, "status": "FENCED_OFF", "error": str(e)}

        # 4. Deterministic QA Gate validation
        t_qa_start = time.time()
        try:
            validate_qa_gate(
                envelope.to_dict(),
                worker_res.to_dict(),
                workspace_dir=ws_path,
                reverify_tests=self.reverify_worker_tests,
            )
        except QAGateError as e:
            self.store.invalidate_fencing_token(task_id, reason="qa_gate_failed")
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            self.store.record_event(
                task_id, "QA_GATE_FAILED", TaskState.VERIFYING.value, TaskState.REJECTED.value, {"error": str(e)}
            )
            return {"task_id": task_id, "status": "REJECTED", "error": str(e)}
        qa_duration = round(time.time() - t_qa_start, 2)

        # 5. If Ready for Review, integrate and submit
        if worker_res.status == "READY_FOR_REVIEW":
            t_integ_start = time.time()
            v_cmd = envelope.verification_cmd or envelope.metadata.get("verification_cmd")
            if not v_cmd and self.verify_post_integration and self.reverify_worker_tests:
                if worker_res.test_results:
                    v_cmd = worker_res.test_results[0].get("command")

            integ_res = self.integration_guard.integrate(
                worker_res,
                ws_path,
                verification_cmd=v_cmd,
            )
            if not integ_res.success:
                self.store.invalidate_fencing_token(task_id, reason="integration_failed")
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "INTEGRATION_FAILED",
                    "error": integ_res.error_message,
                }

            # Enforce remote push verification if remote exists and push is enabled
            remote_configured = self.git_guard.has_remote(self.integration_guard.remote_name)
            must_verify_pushed = self.integration_guard.push_to_remote and remote_configured
            if must_verify_pushed and not integ_res.pushed:
                self.store.invalidate_fencing_token(task_id, reason="push_failed")
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "INTEGRATION_FAILED",
                    "error": f"Commit {integ_res.commit_sha} was not pushed to remote {self.integration_guard.remote_name}",
                }

            try:
                verified_rev = self.git_guard.verify_head(
                    integ_res.commit_sha,
                    verify_pushed=must_verify_pushed,
                    remote=self.integration_guard.remote_name,
                )
            except Exception as e:
                self.store.invalidate_fencing_token(task_id, reason="verify_head_failed")
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "INTEGRATION_FAILED",
                    "error": f"Git revision verification failed: {e}",
                }
            integ_duration = round(time.time() - t_integ_start, 2)

            # Persist verified checkpoint and transition VERIFYING -> READY_TO_SUBMIT under fencing token
            try:
                self.store.save_verified_checkpoint(
                    task_id,
                    worker_result_json=json.dumps(worker_res.to_dict()),
                    workspace_path=str(ws_path),
                    revision_sha=verified_rev.commit_sha,
                    fencing_token=fencing_token,
                )
                self.store.transition_state(
                    task_id,
                    TaskState.READY_TO_SUBMIT,
                    expected_current=(TaskState.VERIFYING,),
                    fencing_token=fencing_token,
                )
            except ValueError as e:
                self.scheduler.requeue_task(task_id)
                return {"task_id": task_id, "status": "FENCED_OFF", "error": str(e)}

            # Re-check deadline immediately before submission (P1.2)
            current_server_now = self.scheduler.current_server_time
            if envelope.deadline_epoch and current_server_now > envelope.deadline_epoch:
                self.store.invalidate_fencing_token(task_id, reason="deadline_expired")
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "DEADLINE_EXPIRED",
                    "error": f"Task deadline expired before submission: {current_server_now} > {envelope.deadline_epoch}",
                }

            # Guarded Submission with Server Clock & Reconciliation
            t_submit_start = time.time()
            sub_res = self.submission_guard.execute_guarded_submission(
                envelope=envelope,
                worker_result=worker_res,
                revision=verified_rev,
                game_master_submit_fn=self.gm.submit,
                game_master_status_fn=self.gm.get_submission_status,
                current_time=self.scheduler.current_server_time,
            )
            submit_duration = round(time.time() - t_submit_start, 2)

            # Check grading status if server returned ACCEPTED
            final_status = sub_res.status
            final_points = sub_res.points_awarded
            if sub_res.success and final_status not in ("PASSED", "FAILED") and hasattr(self.gm, "get_submission_status"):
                try:
                    grading = self.gm.get_submission_status(task_id)
                    if isinstance(grading, dict) and isinstance(grading.get("status"), str):
                        g_status = grading.get("status")
                        if g_status in ("PASSED", "FAILED"):
                            final_status = g_status
                            raw_pts = grading.get("points") if grading.get("points") is not None else grading.get("score")
                            if g_status == "PASSED":
                                final_points = int(raw_pts) if raw_pts is not None else (final_points or envelope.points)
                            else:  # FAILED
                                final_points = int(raw_pts) if raw_pts is not None else 0
                            self.store.update_submission_status(task_id, g_status, final_points, grading)
                except Exception:
                    pass

            if final_status == "PASSED":
                final_state = TaskState.PASSED
            elif final_status == "FAILED":
                final_state = TaskState.FAILED
            elif sub_res.success:
                final_state = TaskState.SUBMITTED
            else:
                final_state = TaskState.REJECTED

            self.scheduler.complete_task(task_id, final_state)

            # Record stage trace metrics
            self.store.record_event(
                task_id,
                "TASK_PIPELINE_STAGES",
                None,
                final_state.value,
                {
                    "worker_duration": worker_duration,
                    "qa_duration": qa_duration,
                    "integ_duration": integ_duration,
                    "submit_duration": submit_duration,
                    "total_duration": round(time.time() - t_stage_start, 2),
                    "billable_tokens": trace.billable_tokens,
                },
            )

            return {
                "task_id": task_id,
                "status": final_status,
                "points": final_points,
                "pushed": integ_res.pushed,
            }

        else:
            if worker_res.status == "BLOCKED":
                self.scheduler.complete_task(task_id, TaskState.BLOCKED)
            else:
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
            return {"task_id": task_id, "status": worker_res.status}

    def run_loop(
        self,
        poll_interval: float = 0.5,
        max_cycles: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Autonomous tournament execution loop.

        Runs continuously until match state becomes FINISHED or max_cycles is reached.
        """
        cycles = 0
        while True:
            cycles += 1
            if max_cycles and cycles > max_cycles:
                break

            # 1. Sync backlog & check preemption
            self.sync_backlog()

            # 2. Check match state
            try:
                state = self.gm.get_match_state()
                if state.get("status") == "FINISHED":
                    logger.info("Tournament match FINISHED.")
                    self.supervisor.terminate_all("CANCELLED")
                    break
                if state.get("status") == "PAUSED":
                    logger.info("Tournament match PAUSED. Waiting...")
                    time.sleep(poll_interval)
                    continue
            except Exception as e:
                logger.warning("Error fetching match state in run_loop: %s", e)

            # 3. Execute next step
            step_res = self.step()
            if not step_res:
                time.sleep(poll_interval)

        final_match_state = self.gm.get_match_state()
        return {
            "cycles": cycles,
            "total_score": final_match_state.get("total_score", 0),
            "tokens_consumed": self.cumulative_billable_tokens,
            "match_status": final_match_state.get("status"),
        }

    def generate_match_report(self) -> Dict[str, Any]:
        """Generate structured final tournament match report with task states, score, tokens, git SHA, and errors."""
        from mtm_core.git import get_head_sha
        try:
            head_sha = get_head_sha(self.repo_path)
        except Exception:
            head_sha = "unknown"

        match_state = {}
        try:
            match_state = self.gm.get_match_state()
        except Exception:
            pass

        tasks_summary = []
        errors = []
        for t in self.store.list_tasks():
            tid = t["task_id"]
            sub = self.store.get_submission(tid)
            events = self.store.get_events(tid)

            task_errs = [
                e.get("payload")
                for e in events
                if e.get("event_type") in (
                    "QA_GATE_FAILED",
                    "SUBMISSION_FAILED",
                    "TASK_REJECTED",
                    "INTEGRATION_FAILED",
                    "TASK_TIMED_OUT",
                    "START_WORKING_FAILED",
                )
            ]
            if task_errs:
                errors.append({"task_id": tid, "errors": task_errs})

            tasks_summary.append({
                "task_id": tid,
                "task_type": t.get("task_type"),
                "state": t.get("status") or t.get("state"),
                "points": t.get("points", 0),
                "provisional_points": sub.get("provisional_points", 0) if sub else 0,
                "confirmed_points": sub.get("confirmed_points", 0) if sub else 0,
                "submission_status": sub.get("status") if sub else None,
                "commit_sha": sub.get("commit_sha") if sub else None,
            })

        return {
            "match_status": match_state.get("status", self.scheduler.match_status),
            "total_score": self.store.get_total_confirmed_points(),
            "provisional_score": self.store.get_total_provisional_points(),
            "gm_reported_score": match_state.get("total_score", 0),
            "tokens_consumed": self.cumulative_billable_tokens,
            "token_budget": self.token_budget,
            "git_head_sha": head_sha,
            "tasks": tasks_summary,
            "errors": errors,
        }

