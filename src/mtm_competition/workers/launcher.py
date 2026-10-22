"""WorkerLauncher: Launches and supervises an isolated worker execution."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_core.git import git_status
from mtm_competition.routing.prompt_builder import PromptBuilder
from mtm_competition.routing.skill_router import SkillRouter
from mtm_competition.routing.model_router import ModelRouter
from mtm_competition.workers.workspace import WorkspaceManager


class WorkerLauncher:
    """Orchestrates worker execution in an isolated workspace with strict resource bounds."""

    def __init__(
        self,
        scratch_dir: Path | str,
        runtime: Optional[CodexRuntime] = None,
    ):
        self.workspace_mgr = WorkspaceManager(scratch_dir)
        self.runtime = runtime or CodexRuntime(prefer_codex=True, allow_agy_fallback=True)
        self.model_router = ModelRouter()

    def launch(
        self,
        envelope: TaskEnvelope | Dict[str, Any],
        attempt_number: int = 1,
    ) -> Tuple[WorkerResult, ExecutionTrace, Path]:
        """Execute task in an isolated workspace.

        Returns (WorkerResult, ExecutionTrace, isolated_workspace_path).
        """
        env_dict = envelope.to_dict() if isinstance(envelope, TaskEnvelope) else dict(envelope)

        # Route model, effort, skills
        routing = self.model_router.route(env_dict, attempt_number=attempt_number)
        model = env_dict.get("model") or routing.model
        effort = env_dict.get("reasoning_effort") or routing.reasoning_effort
        timeout = int(env_dict.get("time_budget_seconds") or routing.timeout_seconds)
        skills = routing.selected_skills

        # Prepare isolated workspace
        repo_path = Path(env_dict["repository_path"]).resolve()
        ws_path = self.workspace_mgr.prepare_workspace(
            repo_path=repo_path,
            task_id=env_dict["task_id"],
            attempt=attempt_number,
        )

        # Build prompt & write TASK.md
        prompt_content = PromptBuilder.build_prompt(env_dict, selected_skills=skills)
        (ws_path / "TASK.md").write_text(prompt_content, encoding="utf-8")

        kickoff = (
            f"Read TASK.md and complete the backlog item {env_dict['task_id']} now. "
            "Follow all requirements, run tests, and save worker_result.json. Work autonomously."
        )

        trace_log = ws_path / "trace.jsonl"
        runtime_res = self.runtime.run(
            workspace=ws_path,
            prompt=kickoff,
            model=model,
            effort=effort,
            timeout=timeout,
            log_file=trace_log,
        )

        trace = runtime_res.trace
        elapsed = runtime_res.elapsed

        # Strict timeout handling
        if runtime_res.timed_out:
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
            except Exception:
                result = self._synthesize_result(env_dict, ws_path, skills, elapsed)
        else:
            result = self._synthesize_result(env_dict, ws_path, skills, elapsed)

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
    ) -> WorkerResult:
        changed = []
        try:
            status_files = git_status(ws)
            for f in status_files:
                if f not in ("TASK.md", "trace.jsonl", "worker_result.json"):
                    changed.append(f)
        except Exception:
            pass

        status = WorkerStatus.READY_FOR_REVIEW if changed else WorkerStatus.FAILED
        return WorkerResult(
            task_id=env["task_id"],
            status=status,
            skill_ids=skills,
            changed_files=changed,
            test_results=[],
            evidence_paths=["trace.jsonl"] if (ws / "trace.jsonl").exists() else [],
            elapsed_seconds=elapsed,
        )
