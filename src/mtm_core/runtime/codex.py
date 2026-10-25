"""CodexRuntime: Unified execution engine for OpenAI Codex CLI (and Antigravity fallback).

Single canonical runtime for both Competition Orchestrator and Arena Subsystems.
Enforces deterministic timeout semantics, pause-aware deadlines, policy-aware sandboxing,
native agent profile targeting, and trace harvesting.
"""
from __future__ import annotations

import collections
import io
import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

from .process import terminate_process_tree
from .trace import ExecutionTrace, parse_codex_trace, parse_agy_trace


class DiskLogWriter:
    """Thread-safe disk logger with bounded disk quota capping (default 50MB)."""

    def __init__(
        self,
        log_path: Path | str,
        max_bytes: int = 50 * 1024 * 1024,
    ) -> None:
        self.path = Path(log_path)
        self.max_bytes = max_bytes
        self._bytes_written: int = 0
        self._bytes_omitted: int = 0
        self.lock = threading.Lock()
        self.closed: bool = False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle: Optional[io.BufferedWriter] = open(self.path, "wb")

    @property
    def is_truncated(self) -> bool:
        with self.lock:
            return self._bytes_omitted > 0

    @property
    def bytes_written(self) -> int:
        with self.lock:
            return self._bytes_written

    @property
    def bytes_omitted(self) -> int:
        with self.lock:
            return self._bytes_omitted

    def write(self, chunk: Any) -> int:
        if not chunk or self.closed:
            return 0
        raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", errors="replace")
        chunk_len = len(raw)
        with self.lock:
            if self.closed or self._handle is None:
                return 0
            if self._bytes_written >= self.max_bytes:
                self._bytes_omitted += chunk_len
                return 0
            remaining_quota = self.max_bytes - self._bytes_written
            allowed = min(chunk_len, remaining_quota)
            if allowed > 0:
                try:
                    self._handle.write(raw[:allowed])
                    self._handle.flush()
                    self._bytes_written += allowed
                except Exception:
                    pass
            omitted = chunk_len - allowed
            if omitted > 0:
                self._bytes_omitted += omitted
            return allowed

    def close(self) -> None:
        with self.lock:
            if not self.closed:
                self.closed = True
                if self._handle is not None:
                    try:
                        self._handle.close()
                    except Exception:
                        pass
                    self._handle = None


class BoundedStreamBuffer:
    """Thread-safe bounded in-memory buffer preserving rolling head and tail chunks.

    Guarantees that RAM accumulation never exceeds max_bytes (default 10MB).
    If total bytes <= max_bytes, 100% of the stream is preserved verbatim.
    If total bytes > max_bytes, the first head_bytes and the last tail_bytes are retained,
    with an informative truncation notice between them for diagnostics.

    Optionally writes to disk log up to max_disk_bytes (default 50MB quota).
    """

    def __init__(
        self,
        max_bytes: int = 10 * 1024 * 1024,
        log_file: Optional[Path | str | DiskLogWriter] = None,
        max_disk_bytes: int = 50 * 1024 * 1024,
    ):
        self.max_bytes = max_bytes
        self.max_disk_bytes = max_disk_bytes
        self.head_budget = max_bytes // 2
        self.tail_budget = max_bytes - self.head_budget
        self.head_chunks: List[bytes] = []
        self.head_bytes: int = 0
        self.tail_chunks: collections.deque[bytes] = collections.deque()
        self.tail_bytes: int = 0
        self.total_bytes: int = 0
        self.lock = threading.Lock()
        self.peer_buffer: Optional["BoundedStreamBuffer"] = None
        self.fair_share: int = max_bytes // 2

        if isinstance(log_file, DiskLogWriter):
            self.disk_writer: Optional[DiskLogWriter] = log_file
            self.log_file: Optional[Path | str] = log_file.path
            self._owns_disk_writer: bool = False
        elif log_file is not None:
            self.disk_writer = DiskLogWriter(log_file, max_bytes=max_disk_bytes)
            self.log_file = log_file
            self._owns_disk_writer = True
        else:
            self.disk_writer = None
            self.log_file = None
            self._owns_disk_writer = False

    def _check_peer_budget(self) -> None:
        if self.peer_buffer is not None:
            peer_bytes = self.peer_buffer.total_bytes
            if peer_bytes > self.fair_share and self.max_bytes > self.fair_share:
                self._shrink_to(self.fair_share)

    def _shrink_to(self, new_max: int) -> None:
        if self.max_bytes <= new_max:
            return
        self.max_bytes = new_max
        self.head_budget = new_max // 2
        self.tail_budget = new_max - self.head_budget

        if self.head_bytes > self.head_budget:
            new_head: List[bytes] = []
            accum = 0
            for c in self.head_chunks:
                if accum + len(c) <= self.head_budget:
                    new_head.append(c)
                    accum += len(c)
                else:
                    needed = self.head_budget - accum
                    if needed > 0:
                        new_head.append(c[:needed])
                        accum += needed
                    break
            self.head_chunks = new_head
            self.head_bytes = accum

        while self.tail_bytes > self.tail_budget and self.tail_chunks:
            excess = self.tail_bytes - self.tail_budget
            front = self.tail_chunks[0]
            if len(front) <= excess:
                self.tail_chunks.popleft()
                self.tail_bytes -= len(front)
            else:
                self.tail_chunks[0] = front[excess:]
                self.tail_bytes -= excess
                break

    def append(self, chunk: Any) -> None:
        if not chunk:
            return
        raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", errors="replace")
        chunk_len = len(raw)

        if self._owns_disk_writer and self.disk_writer is not None:
            try:
                self.disk_writer.write(raw)
            except Exception:
                pass

        with self.lock:
            self.total_bytes += chunk_len
            self._check_peer_budget()

            # 1. Fill head buffer up to head_budget
            if self.head_bytes < self.head_budget:
                avail = self.head_budget - self.head_bytes
                if chunk_len <= avail:
                    self.head_chunks.append(raw)
                    self.head_bytes += chunk_len
                    return
                else:
                    head_part = raw[:avail]
                    tail_part = raw[avail:]
                    self.head_chunks.append(head_part)
                    self.head_bytes += len(head_part)
                    raw = tail_part
                    chunk_len = len(raw)

            # 2. Append remainder to tail buffer (rolling window)
            if chunk_len >= self.tail_budget:
                self.tail_chunks.clear()
                self.tail_chunks.append(raw[-self.tail_budget:])
                self.tail_bytes = self.tail_budget
            else:
                self.tail_chunks.append(raw)
                self.tail_bytes += chunk_len
                while self.tail_bytes > self.tail_budget and self.tail_chunks:
                    excess = self.tail_bytes - self.tail_budget
                    front = self.tail_chunks[0]
                    if len(front) <= excess:
                        self.tail_chunks.popleft()
                        self.tail_bytes -= len(front)
                    else:
                        self.tail_chunks[0] = front[excess:]
                        self.tail_bytes -= excess
                        break

    def get_text(self) -> str:
        with self.lock:
            self._check_peer_budget()
            if not self.head_chunks and not self.tail_chunks:
                return ""
            if self.total_bytes <= (self.head_bytes + self.tail_bytes):
                # No data omitted: join all chunks directly to preserve multibyte UTF-8 across boundaries
                all_bytes = b"".join(self.head_chunks) + b"".join(self.tail_chunks)
                return all_bytes.decode("utf-8", errors="replace")

            head_text = b"".join(self.head_chunks).decode("utf-8", errors="replace")
            tail_text = b"".join(self.tail_chunks).decode("utf-8", errors="replace")
            omitted = self.total_bytes - (self.head_bytes + self.tail_bytes)

            if self.disk_writer is None or self.log_file is None:
                notice = (
                    f"\n... [output truncated: {omitted} bytes truncated in memory (no disk log configured)] ...\n"
                )
            elif self.disk_writer.is_truncated:
                quota_mb = self.disk_writer.max_bytes // (1024 * 1024)
                quota_str = f"{quota_mb}MB" if quota_mb > 0 else f"{self.disk_writer.max_bytes}B"
                notice = (
                    f"\n... [output truncated: {omitted} bytes truncated in memory. "
                    f"Disk log reached {quota_str} quota (DISK_LOG_TRUNCATED: "
                    f"{self.disk_writer.bytes_written} bytes saved, {self.disk_writer.bytes_omitted} bytes omitted from disk)] ...\n"
                )
            else:
                notice = (
                    f"\n... [output truncated: {omitted} bytes truncated in memory. "
                    f"Full stream written to log_file.] ...\n"
                )
            return head_text + notice + tail_text

    def close(self) -> None:
        if self._owns_disk_writer and self.disk_writer is not None:
            self.disk_writer.close()



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
        dangerously_skip_permissions: Optional[bool] = None,
        skip_permissions: Optional[bool] = None,
    ) -> List[str]:
        """Construct Antigravity CLI command with policy-aware sandbox decoupled from permission bypass."""
        policy = (sandbox_policy or os.environ.get("MTM_AGY_SANDBOX_POLICY") or "").strip().lower()
        cmd = ["agy", "-p", prompt]

        is_sandboxed = policy in ("read-only", "sandbox", "restricted", "workspace-sandbox") or (
            os.environ.get("MTM_AGY_SANDBOX") == "1"
        )

        # Apply sandbox mode flags
        if policy == "read-only":
            cmd += ["--sandbox", "--mode", "plan"]
        elif is_sandboxed:
            cmd += ["--sandbox", "--mode", "accept-edits"]

        # Resolve permission bypass orthogonally: defaults to False across ALL modes
        explicit_bypass = dangerously_skip_permissions if dangerously_skip_permissions is not None else skip_permissions
        if explicit_bypass is None:
            env_val = os.environ.get("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS") or os.environ.get("MTM_AGY_SKIP_PERMISSIONS")
            if env_val is not None and env_val.strip():
                explicit_bypass = env_val.strip().lower() in ("1", "true", "yes")

        should_skip = bool(explicit_bypass) if explicit_bypass is not None else False

        if should_skip:
            logger.warning("Antigravity execution with permission bypass enabled (--dangerously-skip-permissions).")
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
        log_file: Optional[Path | str] = None,
        max_buffer_bytes: Optional[int] = None,
        max_disk_bytes: int = 50 * 1024 * 1024,
        max_total_buffer_bytes: Optional[int] = None,
        **kwargs: Any,
    ) -> tuple[str, str]:
        """Wait for subprocess output while freezing timeout depletion whenever `pause_event` is set.

        Continuously drains stdout and stderr pipes in background daemon threads so that
        child process output (even >10MB) never saturates OS pipe buffers (~64KB) or causes
        deadlocks, while strictly preserving monotonic pause/resume deadline freezing.
        In-memory buffering is capped at max_total_buffer_bytes (default 10MB aggregate) across
        both streams with rolling head and tail retention. If log_file is provided, chunks are
        streamed directly to disk up to max_disk_bytes (default 50MB quota).
        """
        if max_total_buffer_bytes is None and "max_total_buffer_bytes" in kwargs:
            max_total_buffer_bytes = kwargs["max_total_buffer_bytes"]
        if max_buffer_bytes is None and "max_buffer_bytes" in kwargs:
            max_buffer_bytes = kwargs["max_buffer_bytes"]

        if max_total_buffer_bytes is not None:
            total_limit = max_total_buffer_bytes
        elif max_buffer_bytes is not None:
            total_limit = max_buffer_bytes
        else:
            total_limit = 10 * 1024 * 1024

        def _to_str(val: Any) -> str:
            if val is None:
                return ""
            if isinstance(val, bytes):
                return val.decode("utf-8", errors="replace")
            return str(val)

        def _is_pipe(stream: Any) -> bool:
            if stream is None:
                return False
            try:
                import unittest.mock as _mock
                if isinstance(stream, _mock.Base):
                    return False
            except ImportError:
                pass
            return callable(getattr(stream, "read", None))

        disk_writer: Optional[DiskLogWriter] = (
            DiskLogWriter(log_file, max_bytes=max_disk_bytes) if log_file else None
        )

        stdout_pipe = getattr(proc, "stdout", None)
        stderr_pipe = getattr(proc, "stderr", None)
        has_stdout = _is_pipe(stdout_pipe)
        has_stderr = _is_pipe(stderr_pipe) and stderr_pipe is not stdout_pipe

        if has_stdout and has_stderr:
            fair_share = total_limit // 2
            stdout_buf = BoundedStreamBuffer(max_bytes=total_limit, log_file=disk_writer)
            stderr_buf = BoundedStreamBuffer(max_bytes=total_limit, log_file=disk_writer)
            stdout_buf.peer_buffer = stderr_buf
            stdout_buf.fair_share = fair_share
            stderr_buf.peer_buffer = stdout_buf
            stderr_buf.fair_share = total_limit - fair_share
        elif has_stdout:
            stdout_buf = BoundedStreamBuffer(max_bytes=total_limit, log_file=disk_writer)
            stderr_buf = BoundedStreamBuffer(max_bytes=0, log_file=disk_writer)
        elif has_stderr:
            stdout_buf = BoundedStreamBuffer(max_bytes=0, log_file=disk_writer)
            stderr_buf = BoundedStreamBuffer(max_bytes=total_limit, log_file=disk_writer)
        else:
            stdout_buf = BoundedStreamBuffer(max_bytes=total_limit // 2, log_file=disk_writer)
            stderr_buf = BoundedStreamBuffer(max_bytes=total_limit - (total_limit // 2), log_file=disk_writer)

        stdout_thread: Optional[threading.Thread] = None
        stderr_thread: Optional[threading.Thread] = None
        poll_fn = getattr(proc, "poll", None)

        try:
            # If no pipes to drain and no pause_event, fast path directly to proc.communicate
            if not has_stdout and not has_stderr and pause_event is None and callable(getattr(proc, "communicate", None)):
                out, err = proc.communicate(timeout=timeout)
                out_str = _to_str(out)
                err_str = _to_str(err)
                if disk_writer is not None:
                    try:
                        if out:
                            disk_writer.write(out)
                        if err:
                            disk_writer.write(err)
                    except Exception:
                        pass
                return out_str, err_str

            def _drain_stream(stream: Any, buf: BoundedStreamBuffer) -> None:
                raw_stream = getattr(stream, "buffer", stream)
                read_fn = getattr(raw_stream, "read1", None)
                if not callable(read_fn):
                    read_fn = getattr(stream, "read1", None)
                if not callable(read_fn):
                    read_fn = getattr(stream, "read", None)
                if not callable(read_fn):
                    return

                try:
                    while True:
                        chunk = read_fn(65536)
                        if not chunk:
                            break
                        if disk_writer is not None:
                            try:
                                disk_writer.write(chunk)
                            except Exception:
                                pass
                        buf.append(chunk)
                except Exception:
                    pass
                finally:
                    try:
                        stream.close()
                    except Exception:
                        pass

            if has_stdout:
                stdout_thread = threading.Thread(
                    target=_drain_stream,
                    args=(stdout_pipe, stdout_buf),
                    name="mtm-codex-drain-stdout",
                    daemon=True,
                )
                stdout_thread.start()

            if has_stderr:
                stderr_thread = threading.Thread(
                    target=_drain_stream,
                    args=(stderr_pipe, stderr_buf),
                    name="mtm-codex-drain-stderr",
                    daemon=True,
                )
                stderr_thread.start()

            remaining = float(timeout)
            t_last = time.monotonic()

            while True:
                if callable(poll_fn) and poll_fn() is not None:
                    # Child process has terminated
                    if stdout_thread is not None:
                        stdout_thread.join(timeout=0.5)
                    if stderr_thread is not None:
                        stderr_thread.join(timeout=0.5)

                    out_str = stdout_buf.get_text()
                    err_str = stderr_buf.get_text()

                    # Compatibility fallback for mock proc objects without pipes
                    if not has_stdout and not has_stderr and callable(getattr(proc, "communicate", None)):
                        try:
                            out_c, err_c = proc.communicate(timeout=2)
                            out_str = _to_str(out_c)
                            err_str = _to_str(err_c)
                            if disk_writer is not None:
                                try:
                                    if out_c:
                                        disk_writer.write(out_c)
                                    if err_c:
                                        disk_writer.write(err_c)
                                except Exception:
                                    pass
                            return out_str, err_str
                        except Exception:
                            pass

                    return out_str, err_str

                time.sleep(0.02)
                t_now = time.monotonic()
                elapsed_slice = t_now - t_last
                t_last = t_now

                if pause_event is None or not pause_event.is_set():
                    remaining -= elapsed_slice
                    if remaining <= 0:
                        out_str = stdout_buf.get_text()
                        err_str = stderr_buf.get_text()
                        raise subprocess.TimeoutExpired(
                            cmd=getattr(proc, "args", ["worker"]),
                            timeout=timeout,
                            output=out_str,
                            stderr=err_str,
                        )
        finally:
            if disk_writer is not None:
                try:
                    disk_writer.close()
                except Exception:
                    pass
            # Ensure background threads are cleaned up if process has terminated
            if callable(poll_fn) and poll_fn() is not None:
                if stdout_thread is not None and stdout_thread.is_alive():
                    stdout_thread.join(timeout=0.2)
                if stderr_thread is not None and stderr_thread.is_alive():
                    stderr_thread.join(timeout=0.2)

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
        dangerously_skip_permissions: Optional[bool] = None,
        skip_permissions: Optional[bool] = None,
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
            if log_file:
                try:
                    Path(log_file).unlink(missing_ok=True)
                except Exception:
                    pass
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

        effective_bypass = dangerously_skip_permissions if dangerously_skip_permissions is not None else skip_permissions
        if effective_bypass is None and codex_config:
            effective_bypass = codex_config.get(
                "dangerously_skip_permissions",
                codex_config.get("skip_permissions"),
            )

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
                        stdout_text, _ = self._communicate_with_pause(
                            proc, timeout=timeout, pause_event=pause_event, log_file=log_file
                        )
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired as exc:
                        timed_out = True
                        _terminate_process_tree(proc)
                        partial_out = getattr(exc, "output", "") or ""
                        if partial_out:
                            stdout_text = partial_out
                        else:
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
                dangerously_skip_permissions=effective_bypass,
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
                        stdout_text, _ = self._communicate_with_pause(
                            proc, timeout=timeout, pause_event=pause_event, log_file=log_file
                        )
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired as exc:
                        timed_out = True
                        _terminate_process_tree(proc)
                        partial_out = getattr(exc, "output", "") or ""
                        if partial_out:
                            stdout_text = partial_out
                        else:
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
            if log_file:
                try:
                    Path(log_file).unlink(missing_ok=True)
                except Exception:
                    pass
        elif log_file and not Path(log_file).exists():
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
