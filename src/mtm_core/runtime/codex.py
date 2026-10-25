"""CodexRuntime: Unified execution engine for OpenAI Codex CLI (and Antigravity fallback).

Single canonical runtime for both Competition Orchestrator and Arena Subsystems.
Enforces deterministic timeout semantics, pause-aware deadlines, policy-aware sandboxing,
native agent profile targeting, and trace harvesting.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .process import terminate_process_tree
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


_DEFAULT = object()

_terminate_process_tree = terminate_process_tree


class CodexRuntime:
    """Manages spawning, sandboxing, timing, and harvesting of Codex / Antigravity runs."""

    def __init__(
        self,
        prefer_codex: bool = True,
        allow_agy_fallback: bool = True,
        codex_path: Any = _DEFAULT,
        agy_path: Any = _DEFAULT,
        default_backend: str = "auto",
        strict_backend: bool = False,
    ):
        self.prefer_codex = prefer_codex
        self.allow_agy_fallback = allow_agy_fallback
        self.codex_path = shutil.which("codex") if codex_path is _DEFAULT else codex_path
        self.agy_path = shutil.which("agy") if agy_path is _DEFAULT else agy_path
        self.default_backend = default_backend
        self.strict_backend = strict_backend

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
        agent: Optional[str] = None,
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

        if agent:
            cmd += ["-c", f"agent={json.dumps(agent)}"]

        if codex_config:
            for k, v in codex_config.items():
                cmd += ["-c", f"{k}={json.dumps(v)}"]

        cmd.append(prompt)
        return cmd

    def build_agy_cmd(
        self,
        prompt: str,
        effort: Optional[str] = None,
        model: Optional[str] = None,
        sandbox_policy: Optional[str] = None,
        agent: Optional[str] = None,
    ) -> List[str]:
        """Construct Antigravity CLI command with optional policy-aware sandbox and agent role."""
        policy = (sandbox_policy or os.environ.get("MTM_AGY_SANDBOX_POLICY") or "").lower()
        cmd = ["agy", "-p", prompt]

        if policy == "read-only":
            cmd += ["--sandbox", "--mode", "plan"]
        elif policy in ("sandbox", "restricted", "workspace-sandbox") or os.environ.get("MTM_AGY_SANDBOX") == "1":
            cmd += ["--sandbox", "--mode", "accept-edits", "--dangerously-skip-permissions"]
        else:
            cmd += ["--dangerously-skip-permissions"]

        cmd += ["--output-format", "json"]
        if agent:
            cmd += ["--agent", agent]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]
        return cmd

    @staticmethod
    def _communicate_with_pause(
        proc: Any,
        timeout: float,
        pause_event: Optional[Any],
    ) -> tuple[str, str]:
        """Wait for subprocess output while freezing timeout depletion whenever `pause_event` is set."""
        if pause_event is None:
            out, err = proc.communicate(timeout=timeout)
            return out or "", err or ""

        remaining = float(timeout)
        t_last = time.monotonic()
        while True:
            poll_fn = getattr(proc, "poll", None)
            if callable(poll_fn) and poll_fn() is not None:
                out, err = proc.communicate(timeout=2)
                return out or "", err or ""

            time.sleep(0.04)
            t_now = time.monotonic()
            elapsed_slice = t_now - t_last
            t_last = t_now

            if not pause_event.is_set():
                remaining -= elapsed_slice
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(cmd=getattr(proc, "args", ["worker"]), timeout=timeout)

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
        backend: Optional[str] = None,
        on_process_start: Optional[Callable[[Any], None]] = None,
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
        agent: Optional[str] = None,
    ) -> RuntimeResult:
        """Execute a worker in the given workspace.

        Guarantees strict timeout semantics:
        - If the process exceeds timeout, it is killed immediately and timed_out=True.
        - While pause_event is set, timeout budget is frozen and does not expire.
        - timed_out != READY_FOR_REVIEW.
        - Backend selection: 'codex' or 'antigravity' (no silent fallbacks if explicitly requested).
        """
        ws = Path(workspace).resolve()
        if cancel_event is not None and cancel_event.is_set():
            return RuntimeResult(
                exit_code=-1,
                timed_out=True,
                trace=ExecutionTrace(),
                usage={
                    "input_tokens": 0,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_output_tokens": 0,
                    "total_tokens": 0,
                    "billable_tokens": 0,
                    "normalized_credits": 0.0,
                },
                commands=0,
                file_changes=0,
                last_message="",
                elapsed=0.0,
                raw_stdout="",
            )
        ws.mkdir(parents=True, exist_ok=True)

        target_backend = (backend or self.default_backend).lower()
        if self.strict_backend and target_backend not in ("codex", "antigravity"):
            target_backend = "codex"

        timed_out = False
        exit_code = 0
        stdout_text = ""
        trace = ExecutionTrace()
        t0 = time.time()

        if target_backend == "codex":
            if not self.has_codex:
                raise RuntimeError(
                    "Backend 'codex' explicitly selected but `codex` CLI is not available on PATH."
                )
            use_codex = True
        elif target_backend == "antigravity":
            if not self.has_agy:
                raise RuntimeError(
                    "Backend 'antigravity' explicitly selected but `agy` CLI is not available on PATH."
                )
            use_codex = False
        else:
            # Auto-detection with preference and fallback
            if self.prefer_codex and self.has_codex:
                use_codex = True
            elif self.allow_agy_fallback and self.has_agy:
                use_codex = False
            else:
                raise RuntimeError(
                    "Neither `codex` nor `agy` CLI is available on PATH. "
                    "Ensure Codex CLI is installed or configure fallback."
                )

        resolved_backend = "codex" if use_codex else "antigravity"

        if use_codex:
            cmd = self.build_codex_cmd(
                workspace=ws,
                prompt=prompt,
                model=model,
                effort=effort,
                sandbox_policy=sandbox_policy,
                network_access=network_access,
                codex_config=codex_config,
                agent=agent,
            )
            if cancel_event is not None and cancel_event.is_set():
                timed_out = True
                exit_code = -1
                stdout_text = ""
            else:
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
                    start_new_session=True,
                )
                if on_process_start is not None:
                    try:
                        on_process_start(proc)
                    except Exception:
                        pass
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_process_tree(proc)
                    try:
                        out, _ = proc.communicate(timeout=2)
                        stdout_text = out or ""
                    except Exception:
                        stdout_text = ""
                    timed_out = True
                    exit_code = -1
                else:
                    stop_watcher = threading.Event()
                    if cancel_event is not None:
                        def _watch_codex_cancel() -> None:
                            while not stop_watcher.is_set():
                                if cancel_event.is_set():
                                    _terminate_process_tree(proc)
                                    return
                                stop_watcher.wait(0.02)

                        threading.Thread(target=_watch_codex_cancel, daemon=True).start()
                    try:
                        stdout_text, _ = self._communicate_with_pause(proc, timeout=timeout, pause_event=pause_event)
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _terminate_process_tree(proc)
                        try:
                            out, _ = proc.communicate(timeout=2)
                            stdout_text = out or ""
                        except Exception:
                            stdout_text = ""
                        exit_code = -1
                    finally:
                        stop_watcher.set()

            trace = parse_codex_trace(stdout_text)

        else:
            agy_sandbox = sandbox_policy if sandbox_policy in ("read-only", "sandbox", "restricted", "workspace-sandbox") else None
            cmd = self.build_agy_cmd(
                prompt=prompt,
                effort=effort,
                model=model,
                sandbox_policy=agy_sandbox,
                agent=agent,
            )
            if cancel_event is not None and cancel_event.is_set():
                timed_out = True
                exit_code = -1
                stdout_text = ""
            else:
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
                    start_new_session=True,
                )
                if on_process_start is not None:
                    try:
                        on_process_start(proc)
                    except Exception:
                        pass
                if cancel_event is not None and cancel_event.is_set():
                    _terminate_process_tree(proc)
                    try:
                        out, _ = proc.communicate(timeout=2)
                        stdout_text = out or ""
                    except Exception:
                        stdout_text = ""
                    timed_out = True
                    exit_code = -1
                else:
                    stop_watcher = threading.Event()
                    if cancel_event is not None:
                        def _watch_agy_cancel() -> None:
                            while not stop_watcher.is_set():
                                if cancel_event.is_set():
                                    _terminate_process_tree(proc)
                                    return
                                stop_watcher.wait(0.02)

                        threading.Thread(target=_watch_agy_cancel, daemon=True).start()
                    try:
                        stdout_text, _ = self._communicate_with_pause(proc, timeout=timeout, pause_event=pause_event)
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        _terminate_process_tree(proc)
                        try:
                            out, _ = proc.communicate(timeout=2)
                            stdout_text = out or ""
                        except Exception:
                            stdout_text = ""
                        exit_code = -1
                    finally:
                        stop_watcher.set()

            trace = parse_agy_trace(stdout_text)

        trace.model = model or ""
        trace.backend = resolved_backend
        elapsed = round(time.time() - t0, 2)

        if cancel_event is not None and cancel_event.is_set():
            timed_out = True
            exit_code = -1
        elif log_file:
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
            "normalized_credits": trace.normalized_credits,
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
