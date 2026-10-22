"""Worker Adapter: Dispatches TaskEnvelope to Codex CLI (or Antigravity fallback)."""
from __future__ import annotations
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .trace_parser import ExecutionTrace, parse_codex_trace, parse_agy_trace
from .qa_gate import validate_task_envelope, validate_worker_result, QAGateError


class WorkerAdapter:
    def __init__(self, use_agy_fallback: bool = True):
        self.use_agy_fallback = use_agy_fallback
        self.codex_available = shutil.which("codex") is not None
        self.agy_available = shutil.which("agy") is not None

    def prompt_for_envelope(self, envelope: Dict[str, Any], workspace: Path) -> str:
        """Construct the kickoff prompt for the non-interactive worker."""
        skills = ", ".join(envelope.get("selected_skills", []))
        prompt_lines = [
            f"# BACKLOG TASK: {envelope['task_id']} ({envelope['task_type']})",
            f"Title: {envelope['title']}",
            f"Selected Skills: {skills}",
            "",
            "## Description",
            envelope["description"],
            "",
            "## Requirements",
            "1. Investigate the codebase in this workspace and implement the minimal necessary fix/change.",
            "2. Run all relevant tests to verify the solution.",
            "3. Before finishing, write your final structured output to `worker_result.json` in the current directory.",
            "",
            "## worker_result.json Format",
            "```json",
            "{",
            f'  "task_id": "{envelope["task_id"]}",',
            '  "status": "READY_FOR_REVIEW",',
            f'  "skill_ids": {json.dumps(envelope.get("selected_skills", []))},',
            '  "changed_files": ["<relative_path_1>", ...],',
            '  "test_results": [{"command": "<command>", "passed": true/false}],',
            '  "evidence_paths": ["<path_to_log_or_artifact>", ...],',
            '  "git_revision": null,',
            '  "unresolved_risks": [],',
            '  "elapsed_seconds": 0',
            "}",
            "```",
            "Work autonomously until complete. Output the worker_result.json file."
        ]
        return "\n".join(prompt_lines)

    def execute(self, envelope: Dict[str, Any]) -> Tuple[Dict[str, Any], ExecutionTrace]:
        """Execute a TaskEnvelope and return (WorkerResult, ExecutionTrace)."""
        validate_task_envelope(envelope)

        workspace = Path(envelope["repository_path"]).resolve()
        workspace.mkdir(parents=True, exist_ok=True)

        task_md = workspace / "TASK.md"
        task_md.write_text(self.prompt_for_envelope(envelope, workspace), encoding="utf-8")

        prompt = (
            f"Read TASK.md and complete the backlog item {envelope['task_id']} now. "
            "Follow all requirements, run tests, and save worker_result.json. Work autonomously."
        )

        timeout_s = int(envelope.get("time_budget_seconds", 420))
        t0 = time.time()

        trace_log_path = workspace / "trace.jsonl"
        stdout_text = ""
        trace = ExecutionTrace()

        if self.codex_available:
            # Use Codex CLI
            model = envelope.get("model") or "gpt-6.1-sol"
            effort = envelope.get("reasoning_effort") or "medium"
            cmd = [
                "codex", "exec", "--json", "--ignore-user-config", "--skip-git-repo-check",
                "-C", str(workspace),
                "-m", model,
                "-c", f'model_reasoning_effort="{effort}"',
                prompt
            ]
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=str(workspace), text=True, encoding="utf-8", errors="replace"
            )
            try:
                stdout_text, _ = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, _ = proc.communicate()
            trace = parse_codex_trace(stdout_text)

        elif self.use_agy_fallback and self.agy_available:
            # Fallback to Antigravity CLI in local environment
            cmd = [
                "agy", "-p", prompt,
                "--dangerously-skip-permissions",
                "--output-format", "json"
            ]
            if envelope.get("reasoning_effort"):
                cmd += ["--effort", envelope["reasoning_effort"]]
            
            proc = subprocess.Popen(
                cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=str(workspace), text=True, encoding="utf-8", errors="replace",
                shell=(sys.platform == "win32")
            )
            try:
                stdout_text, _ = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                proc.kill()
                stdout_text, _ = proc.communicate()
            trace = parse_agy_trace(stdout_text)

        else:
            raise RuntimeError(
                "Neither `codex` nor `agy` CLI is available on PATH. "
                "Please install Codex CLI or Antigravity CLI."
            )

        elapsed = round(time.time() - t0, 2)
        trace_log_path.write_text(stdout_text, encoding="utf-8")

        # Parse worker_result.json from workspace
        result_file = workspace / "worker_result.json"
        if result_file.exists():
            try:
                worker_result = json.loads(result_file.read_text(encoding="utf-8"))
            except Exception:
                worker_result = self._synthesize_result(envelope, workspace, trace, elapsed)
        else:
            worker_result = self._synthesize_result(envelope, workspace, trace, elapsed)

        # Ensure elapsed time & usage are recorded
        worker_result["elapsed_seconds"] = elapsed
        worker_result["usage"] = {
            "input_tokens": trace.input_tokens,
            "cached_input_tokens": trace.cached_input_tokens,
            "output_tokens": trace.output_tokens,
            "credits": None
        }

        # Deterministic QA Gate validation
        validate_worker_result(worker_result, workspace_dir=workspace)
        return worker_result, trace

    def _synthesize_result(
        self, envelope: Dict[str, Any], workspace: Path, trace: ExecutionTrace, elapsed: float
    ) -> Dict[str, Any]:
        """Construct fallback WorkerResult by inspecting git status if agent didn't output file."""
        changed = []
        try:
            r = subprocess.run(["git", "status", "--porcelain"], cwd=str(workspace),
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                for line in r.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2 and parts[1] not in ("TASK.md", "trace.jsonl", "worker_result.json"):
                        changed.append(parts[1])
        except Exception:
            pass

        return {
            "task_id": envelope["task_id"],
            "status": "READY_FOR_REVIEW" if changed else "FAILED",
            "skill_ids": envelope.get("selected_skills", ["mtm-task-worker@0.1.0"]),
            "changed_files": changed,
            "test_results": [],
            "evidence_paths": ["trace.jsonl"] if (workspace / "trace.jsonl").exists() else [],
            "git_revision": None,
            "unresolved_risks": [],
            "elapsed_seconds": elapsed
        }
