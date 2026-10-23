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
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionResult
from mtm_competition.adapters.base import GameMasterAdapter

logger = logging.getLogger("mtm.orchestrator")


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
    ):
        self.gm = gm_adapter
        self.repo_path = Path(repository_path).resolve()
        self.scratch_dir = Path(scratch_dir).resolve()
        self.scratch_dir.mkdir(parents=True, exist_ok=True)

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
            or env_cfg.get("orchestrator", {}).get("worker_backend", "codex")
        ).lower()

        strict_backend = env_cfg.get("worker", {}).get("strict_backend", True)
        allow_fallback = env_cfg.get("worker", {}).get("allow_fallback", False)

        self.launcher = WorkerLauncher(
            self.scratch_dir,
            backend=self.worker_backend,
            strict_backend=strict_backend,
            allow_fallback=allow_fallback,
        )
        self.supervisor = WorkerSupervisor()
        self.integration_guard = IntegrationGuard(
            self.repo_path,
            push_to_remote=push_to_remote,
            verify_post_integration=verify_post_integration,
            require_remote=require_remote,
        )
        self.git_guard = GitGuard(self.repo_path)
        self.submission_guard = SubmissionGuard(self.store)

        self.cumulative_billable_tokens: int = 0
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

        # Reconcile any in-flight submissions and grading lifecycle remaining from crash/restart
        if hasattr(self.gm, "get_submission_status"):
            try:
                self.store.reconcile_in_flight_submissions(self.gm.get_submission_status)
                self.store.reconcile_grading_lifecycle(self.gm.get_submission_status)
            except Exception as e:
                logger.warning("Could not reconcile in-flight submissions on startup: %s", e)

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
                    self.supervisor.preempt_worker(preempt_task_id)
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
                return {"status": "FINISHED"}
        except Exception:
            pass

        task_data = self.scheduler.select_next_task()
        if not task_data:
            return None

        task_id = task_data["task_id"]
        self.scheduler.assign_task(task_id)

        t_stage_start = time.time()

        # 1. Signal Game Master and unlock MVS
        self.gm.start_working(task_id)
        mvs = self.gm.get_mvs(task_id)

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

        # 2. Token Budget Watchdog: enforce lower effort if approaching limit
        if self.cumulative_billable_tokens > (self.token_budget * 0.85):
            envelope.reasoning_effort = "low"

        # 3. Supervised worker launch with mid-execution preemption and match pause awareness
        t_worker_start = time.time()
        supervised_proc = self.supervisor.launch_async(self.launcher.launch, envelope)

        while not self.supervisor.is_finished(task_id):
            # Sync match state and clock
            try:
                match_state = self.gm.get_match_state()
                self.scheduler.sync_with_game_master(match_state)
            except Exception:
                pass

            if self.scheduler.is_paused:
                # Extend worker timeout while match clock is paused
                supervised_proc.timeout_seconds += 0.2
                time.sleep(0.2)
                continue

            # Poll backlog to detect if high-priority PI arrived that preempts us
            self.sync_backlog()
            if supervised_proc.status == "PREEMPTED":
                return {"task_id": task_id, "status": "PREEMPTED"}

            if self.supervisor.is_timed_out(task_id):
                self.supervisor.preempt_worker(task_id)
                break

            time.sleep(0.05)

        if supervised_proc.status == "PREEMPTED":
            return {"task_id": task_id, "status": "PREEMPTED"}

        try:
            worker_res, trace, ws_path = self.supervisor.get_result(task_id, timeout=5.0)
        except Exception as e:
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            return {"task_id": task_id, "status": "FAILED", "error": str(e)}

        worker_duration = round(time.time() - t_worker_start, 2)

        self.cumulative_billable_tokens += trace.billable_tokens

        # Record attempt
        self.store.record_attempt(
            task_id=task_id,
            attempt_id=f"{task_id}_att1",
            status=worker_res.status,
            elapsed_seconds=worker_res.elapsed_seconds,
            billable_tokens=trace.billable_tokens,
            result_json=worker_res.to_dict(),
        )

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
            self.scheduler.complete_task(task_id, TaskState.REJECTED)
            self.store.record_event(
                task_id, "QA_GATE_FAILED", TaskState.RUNNING.value, TaskState.REJECTED.value, {"error": str(e)}
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
                self.scheduler.complete_task(task_id, TaskState.REJECTED)
                return {
                    "task_id": task_id,
                    "status": "INTEGRATION_FAILED",
                    "error": f"Git revision verification failed: {e}",
                }
            integ_duration = round(time.time() - t_integ_start, 2)

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
            if sub_res.success and hasattr(self.gm, "get_submission_status"):
                try:
                    grading = self.gm.get_submission_status(task_id)
                    g_status = grading.get("status")
                    if g_status in ("PASSED", "FAILED"):
                        final_status = g_status
                        if g_status == "PASSED":
                            final_points = int(grading["points"]) if grading.get("points") is not None else (final_points or envelope.points)
                        else:  # FAILED
                            final_points = int(grading["points"]) if grading.get("points") is not None else 0
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
