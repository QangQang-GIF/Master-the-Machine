"""CodexRuntime: Unified execution engine for OpenAI Codex CLI (and Antigravity fallback).

Single canonical runtime for both Competition Orchestrator and Arena Subsystems.
Enforces deterministic timeout semantics, process sandbox policies, and trace harvesting.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .trace import ExecutionTrace, parse_codex_trace, parse_agy_trace


@dataclass
class RuntimeResult:
    """Standardized output from a runtime execution."""
    exit_code: int
    timed_out: bool
    trace: ExecutionTrace
    usage: Dict[str, Any]
    commands: int
    file_changes: int
    last_message: str
    elapsed: float
    raw_stdout: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class CodexRuntime:
    """Manages spawning, sandboxing, timing, and harvesting of Codex / Antigravity runs."""

    def __init__(
        self,
        prefer_codex: bool = True,
        allow_agy_fallback: bool = True,
        codex_path: Optional[str] = None,
        agy_path: Optional[str] = None,
    ):
        self.prefer_codex = prefer_codex
        self.allow_agy_fallback = allow_agy_fallback
        self.codex_path = codex_path if codex_path is not None else shutil.which("codex")
        self.agy_path = agy_path if agy_path is not None else shutil.which("agy")

    @property
    def has_codex(self) -> bool:
        return self.codex_path is not None

    @property
    def has_agy(self) -> bool:
        return self.agy_path is not None

    def build_codex_cmd(
        self,
        workspace: Path,
        prompt: str,
        model: str = "gpt-6.1-sol",
        effort: str = "medium",
        sandbox_policy: str = "workspace-write",
        network_access: bool = True,
        codex_config: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        """Construct codex exec command matching official CLI contracts."""
        cmd = [
            "codex", "exec",
            "--json",
            "--ignore-user-config",
            "--skip-git-repo-check",
            "-C", str(workspace),
            "-s", sandbox_policy,
            "-c", f"sandbox_{sandbox_policy.replace('-', '_')}.network_access={'true' if network_access else 'false'}",
            "-c", 'approval_policy="never"',
            "-m", model,
            "-c", f'model_reasoning_effort="{effort}"',
        ]
        if sys.platform == "win32":
            cmd += ["-c", 'windows.sandbox="unelevated"']

        if codex_config:
            for k, v in codex_config.items():
                cmd += ["-c", f"{k}={json.dumps(v)}"]

        cmd.append(prompt)
        return cmd

    def build_agy_cmd(
        self,
        prompt: str,
        effort: Optional[str] = None,
    ) -> List[str]:
        """Construct Antigravity CLI fallback command."""
        cmd = [
            "agy", "-p", prompt,
            "--dangerously-skip-permissions",
            "--output-format", "json"
        ]
        if effort:
            cmd += ["--effort", effort]
        return cmd

    def run(
        self,
        workspace: Path | str,
        prompt: str,
        model: str = "gpt-6.1-sol",
        effort: str = "medium",
        timeout: int = 420,
        sandbox_policy: str = "workspace-write",
        network_access: bool = True,
        codex_config: Optional[Dict[str, Any]] = None,
        log_file: Optional[Path | str] = None,
    ) -> RuntimeResult:
        """Execute a worker in the given workspace.

        Guarantees strict timeout semantics:
        - If the process exceeds timeout, it is killed immediately and timed_out=True.
        - timed_out != READY_FOR_REVIEW.
        """
        ws = Path(workspace).resolve()
        ws.mkdir(parents=True, exist_ok=True)

        timed_out = False
        exit_code = 0
        stdout_text = ""
        trace = ExecutionTrace()
        t0 = time.time()

        if self.prefer_codex and self.has_codex:
            cmd = self.build_codex_cmd(
                workspace=ws,
                prompt=prompt,
                model=model,
                effort=effort,
                sandbox_policy=sandbox_policy,
                network_access=network_access,
                codex_config=codex_config,
            )
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(ws),
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            try:
                stdout_text, _ = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                stdout_text, _ = proc.communicate()
                exit_code = -1

            trace = parse_codex_trace(stdout_text)

        elif self.allow_agy_fallback and self.has_agy:
            cmd = self.build_agy_cmd(prompt=prompt, effort=effort)
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(ws),
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=(sys.platform == "win32"),
            )
            try:
                stdout_text, _ = proc.communicate(timeout=timeout)
                exit_code = proc.returncode
            except subprocess.TimeoutExpired:
                timed_out = True
                proc.kill()
                stdout_text, _ = proc.communicate()
                exit_code = -1

            trace = parse_agy_trace(stdout_text)

        else:
            raise RuntimeError(
                "Neither `codex` nor `agy` CLI is available on PATH. "
                "Ensure Codex CLI is installed or configure fallback."
            )

        elapsed = round(time.time() - t0, 2)

        if log_file:
            log_path = Path(log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(stdout_text, encoding="utf-8")

        usage = {
            "input_tokens": trace.input_tokens,
            "cached_input_tokens": trace.cached_input_tokens,
            "output_tokens": trace.output_tokens,
            "reasoning_output_tokens": trace.reasoning_output_tokens,
            "total_tokens": trace.total_tokens,
            "billable_tokens": trace.billable_tokens,
        }

        return RuntimeResult(
            exit_code=exit_code,
            timed_out=timed_out,
            trace=trace,
            usage=usage,
            commands=len(trace.commands),
            file_changes=trace.file_changes,
            last_message=trace.last_message,
            elapsed=elapsed,
            raw_stdout=stdout_text,
        )
