"""WorkerLauncher: Launches and supervises an isolated worker execution."""
from __future__ import annotations

import inspect
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_core.git import git_status
from mtm_competition.routing.prompt_builder import PromptBuilder
from mtm_competition.routing.skill_router import SkillRouter
from mtm_competition.workers.workspace import WorkspaceManager

from mtm.constants import (
    MODEL as DEFAULT_TOURNAMENT_MODEL,
    MODEL_EFFORT as DEFAULT_REASONING_EFFORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
)
DEFAULT_TIMEOUT_SECONDS = int(DEFAULT_TASK_TIMEOUT_SECONDS)


def _write_task_file_guarded(target: Path, content: str, cancel_event: Optional[Any]) -> bool:
    """Write `target` atomically unless cancelled; return False (leaving no TASK.md) if cancelled.

    Cancellation is checked before the write, again right before the atomic publish (`os.replace`),
    and once more after publishing; if cancellation landed in any of those windows the file is removed.
    Combined with WorkerSupervisor quiescence (cancel -> join worker thread) and scheduler quarantine,
    no attempt can observe or reuse a workspace that an un-quiesced worker may still touch.
    """
    def _cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    if _cancelled():
        return False
    tmp = target.with_name(target.name + ".tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        if _cancelled():
            return False
        os.replace(tmp, target)
        if _cancelled():
            try:
                target.unlink()
            except FileNotFoundError:
                pass
            return False
        return True
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass


class WorkerLauncher:
    """Orchestrates worker execution in an isolated workspace with strict resource bounds."""

    def __init__(
        self,
        scratch_dir: Path | str,
        runtime: Optional[CodexRuntime] = None,
        backend: str = "codex",
        strict_backend: bool = True,
        allow_fallback: bool = False,
        production_mode: Optional[bool] = None,
    ):
        self.workspace_mgr = WorkspaceManager(scratch_dir)
        self.backend = backend
        self.strict_backend = strict_backend
        self.allow_fallback = allow_fallback
        self.production_mode = (
            production_mode
            if production_mode is not None
            else (os.environ.get("MTM_ENV") == "production" or os.environ.get("MTM_PRODUCTION") == "1")
        )
        self.runtime = runtime or CodexRuntime(
            prefer_codex=(backend != "antigravity"),
            allow_agy_fallback=allow_fallback,
            default_backend=backend,
        )

    def launch(
        self,
        envelope: TaskEnvelope | Dict[str, Any],
        attempt_number: int = 1,
        backend: Optional[str] = None,
        on_process_start: Optional[Callable[[Any], None]] = None,
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
    ) -> Tuple[WorkerResult, ExecutionTrace, Path]:
        """Execute task in an isolated workspace.

        Returns (WorkerResult, ExecutionTrace, isolated_workspace_path).
        """
        env_dict = envelope.to_dict() if isinstance(envelope, TaskEnvelope) else dict(envelope)
        target_backend = backend or env_dict.get("backend") or self.backend

        # Fixed tournament model configuration with optional envelope overrides
        model = env_dict.get("model") or DEFAULT_TOURNAMENT_MODEL
        effort = env_dict.get("reasoning_effort") or DEFAULT_REASONING_EFFORT
        timeout = int(env_dict.get("time_budget_seconds") or DEFAULT_TIMEOUT_SECONDS)
        skills = SkillRouter.route_skills(env_dict)

        repo_path = Path(env_dict["repository_path"]).resolve()
        if cancel_event is not None and cancel_event.is_set():
            return (
                WorkerResult(
                    task_id=env_dict["task_id"],
                    status=WorkerStatus.TIMED_OUT,
                    skill_ids=skills,
                    unresolved_risks=["Worker cancelled before workspace preparation"],
                    model=model,
                    effort=effort,
                ),
                ExecutionTrace(),
                repo_path,
            )

        # Prepare isolated workspace
        ws_path = self.workspace_mgr.prepare_workspace(
            repo_path=repo_path,
            task_id=env_dict["task_id"],
            attempt=attempt_number,
        )

        if cancel_event is not None and cancel_event.is_set():
            return (
                WorkerResult(
                    task_id=env_dict["task_id"],
                    status=WorkerStatus.TIMED_OUT,
                    skill_ids=skills,
                    unresolved_risks=["Worker cancelled before kickoff"],
                    model=model,
                    effort=effort,
                ),
                ExecutionTrace(),
                ws_path,
            )

        # Build prompt & write TASK.md (atomic write; re-checked against cancellation at the check/write boundary)
        prompt_content = PromptBuilder.build_prompt(env_dict, selected_skills=skills)
        # Opt-in portable role, source metadata and trusted skills. Baseline remains unchanged.
        if os.environ.get("MTM_AGENT_CONTEXT_ENABLED") == "1":
            from mtm_agents.context import compile_task_context
            try:
                prompt_content += "\n\n" + compile_task_context(env_dict, skills)
            except (OSError, ValueError, KeyError) as exc:
                prompt_content += f"\n\nAgent context unavailable: {type(exc).__name__}"
        if not _write_task_file_guarded(ws_path / "TASK.md", prompt_content, cancel_event):
            return (
                WorkerResult(
                    task_id=env_dict["task_id"],
                    status=WorkerStatus.TIMED_OUT,
                    skill_ids=skills,
                    unresolved_risks=["Worker cancelled while writing TASK.md"],
                    model=model,
                    effort=effort,
                ),
                ExecutionTrace(),
                ws_path,
            )

        kickoff = (
            f"Read TASK.md and complete the backlog item {env_dict['task_id']} now. "
            "Follow all requirements, run tests, and save worker_result.json. Work autonomously."
        )

        trace_log = ws_path / "trace.jsonl"
        use_native_dispatch = (
            os.environ.get("MTM_NATIVE_AGENT_DISPATCH") == "1"
            or env_dict.get("use_native_subagents")
            or env_dict.get("native_subagents")
        )
        if use_native_dispatch:
            from mtm_agents.dispatcher import NativeSubagentDispatcher

            dispatcher = NativeSubagentDispatcher(runtime=self.runtime)
            runtime_res = dispatcher.dispatch(
                envelope=env_dict,
                prompt=kickoff,
                workspace=ws_path,
                backend=target_backend,
                model=model,
                effort=effort,
                timeout=timeout,
                log_file=trace_log,
                on_process_start=on_process_start,
                cancel_event=cancel_event,
                pause_event=pause_event,
            )
        else:
            if self.production_mode and target_backend == "codex" and not os.environ.get("MTM_TEST_MOCK_RUNTIME"):
                # RUNTIME-01: Prohibit secondary 'codex exec' spawns in production execution path
                raise RuntimeError(
                    "RUNTIME-01 Invariant: Prohibited secondary 'codex exec' subprocess in production execution path. "
                    "The MTM Player owns the single root Codex session (team_orchestrator)."
                )

            run_kwargs: Dict[str, Any] = {
                "workspace": ws_path,
                "prompt": kickoff,
                "model": model,
                "effort": effort,
                "timeout": timeout,
                "log_file": trace_log,
                "backend": target_backend,
                "on_process_start": on_process_start,
            }
            try:
                sig = inspect.signature(self.runtime.run)
                has_var_kw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())
                if "cancel_event" in sig.parameters or has_var_kw:
                    run_kwargs["cancel_event"] = cancel_event
                if ("pause_event" in sig.parameters or has_var_kw) and pause_event is not None:
                    run_kwargs["pause_event"] = pause_event
            except Exception:
                pass

            runtime_res = self.runtime.run(**run_kwargs)

        trace = runtime_res.trace
        elapsed = runtime_res.elapsed

        # Strict timeout / cancellation handling
        if runtime_res.timed_out or (cancel_event is not None and cancel_event.is_set()):
            result = WorkerResult(
                task_id=env_dict["task_id"],
                status=WorkerStatus.TIMED_OUT,
                skill_ids=skills,
                changed_files=[],
                test_results=[],
                evidence_paths=["trace.jsonl"] if trace_log.exists() else [],
                elapsed_seconds=elapsed,
                usage=runtime_res.usage,
                unresolved_risks=["Worker execution timed out"],
                model=model,
                effort=effort,
            )
            return result, trace, ws_path

        # Parse worker_result.json
        res_file = ws_path / "worker_result.json"
        if res_file.exists():
            try:
                res_data = json.loads(res_file.read_text(encoding="utf-8"))
                result = WorkerResult.from_dict(res_data)
                # If runtime failed with non-zero exit code, don't let it pass as READY_FOR_REVIEW
                if runtime_res.exit_code != 0 and result.status == WorkerStatus.READY_FOR_REVIEW.value:
                    result.status = WorkerStatus.FAILED
                    result.unresolved_risks.append(f"Runtime process exited with non-zero code {runtime_res.exit_code}")
            except Exception:
                result = self._synthesize_result(env_dict, ws_path, skills, elapsed, exit_code=runtime_res.exit_code)
        else:
            result = self._synthesize_result(env_dict, ws_path, skills, elapsed, exit_code=runtime_res.exit_code)

        result.elapsed_seconds = elapsed
        result.usage = runtime_res.usage
        result.model = model
        result.effort = effort

        return result, trace, ws_path

    def _synthesize_result(
        self,
        env: Dict[str, Any],
        ws: Path,
        skills: list[str],
        elapsed: float,
        exit_code: int = 0,
    ) -> WorkerResult:
        changed = []
        try:
            status_files = git_status(ws)
            for f in status_files:
                norm_f = f.replace("\\", "/")
                if (
                    norm_f not in ("TASK.md", "trace.jsonl", "worker_result.json")
                    and not norm_f.startswith(".codex/agents/")
                    and not norm_f.startswith(".agents/agents/")
                ):
                    changed.append(f)
        except Exception:
            pass

        task_type = str(env.get("task_type", "BUG")).upper()
        # Non-zero exit code or CR/BUG/PI without explicit tests cannot be READY_FOR_REVIEW
        if exit_code != 0 or not changed:
            status = WorkerStatus.FAILED
        elif task_type in ("CR", "BUG", "PI"):
            # Rigorous: CR, BUG, and PI require passing tests, cannot synthesize READY_FOR_REVIEW without tests
            status = WorkerStatus.FAILED
        else:
            # Simple TASK may be reviewable if files changed
            status = WorkerStatus.READY_FOR_REVIEW

        unresolved = []
        if status == WorkerStatus.FAILED:
            if exit_code != 0:
                unresolved.append(f"Runtime process exited with code {exit_code}")
            elif not changed:
                unresolved.append("No files were changed")
            else:
                unresolved.append(f"{task_type} requires verified test execution, none was found")

        return WorkerResult(
            task_id=env["task_id"],
            status=status,
            skill_ids=skills,
            changed_files=changed,
            test_results=[],
            evidence_paths=["trace.jsonl"] if (ws / "trace.jsonl").exists() else [],
            elapsed_seconds=elapsed,
            unresolved_risks=unresolved,
        )
