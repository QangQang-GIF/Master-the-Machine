"""Run a bounded local web server and its verification command together.

The server and check share one in-memory ProjectRuntime environment. Output is
kept in a small ring-like buffer and is never written to disk by this helper.
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from urllib.error import URLError
from urllib.request import urlopen
from urllib.parse import urlsplit

from .project_runtime import CommandResult, ProjectRuntime

_LOG_LIMIT = 64 * 1024


def _validate_readiness_url(runtime: ProjectRuntime, url: str | None) -> str:
    port = int(runtime.env["PORT"])
    if not 1 <= port <= 65535:
        raise ValueError("runtime PORT must be a valid TCP port")
    candidate = url or f"http://127.0.0.1:{port}/"
    parts = urlsplit(candidate)
    if (parts.scheme not in {"http", "https"}
            or (parts.hostname or "").lower() not in {"localhost", "127.0.0.1", "::1"}
            or parts.port != port or parts.username or parts.password):
        raise ValueError("readiness URL must use loopback and the exact runtime PORT")
    return candidate


def _append_bounded(buffer: deque[str], size: list[int], chunk: str) -> None:
    if not chunk:
        return
    buffer.append(chunk)
    size[0] += len(chunk)
    while size[0] > _LOG_LIMIT and buffer:
        excess = size[0] - _LOG_LIMIT
        first = buffer[0]
        if len(first) <= excess:
            size[0] -= len(buffer.popleft())
        else:
            buffer[0] = first[excess:]
            size[0] -= excess


def _terminate_tree(proc: subprocess.Popen[str], grace: float = 2.0) -> None:
    if os.name == "nt":
        if proc.poll() is None:
            proc.terminate()
        try:
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=grace)
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    # The leader may exit while one of its descendants remains in the group.
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass


def run_web_session(runtime: ProjectRuntime, server_argv: list[str],
                    check_argv: list[str], readiness_url: str | None = None,
                    timeout: float = 300, readiness_timeout: float = 45) -> CommandResult:
    """Start a local server, wait for HTTP readiness, run a check, then reap it.

    Only loopback URLs on the exact match/worker runtime port are accepted.
    The overall timeout includes startup, readiness, and check execution.
    """
    if not server_argv or not check_argv:
        raise ValueError("server_argv and check_argv must be non-empty")
    if timeout <= 0 or readiness_timeout <= 0:
        raise ValueError("timeouts must be positive")
    url = _validate_readiness_url(runtime, readiness_url)
    started = time.monotonic()
    out: deque[str] = deque()
    err: deque[str] = deque()
    out_size, err_size = [0], [0]
    lock = threading.Lock()

    main_thread = threading.current_thread() is threading.main_thread()
    previous_sigterm = None
    proc: subprocess.Popen[str] | None = None
    if main_thread and hasattr(signal, "SIGTERM"):
        previous_sigterm = signal.getsignal(signal.SIGTERM)

        def stop_session(signum, _frame):
            # Cover the small window before the regular finally block is entered.
            signal.signal(signal.SIGTERM, previous_sigterm)
            if proc is not None:
                _terminate_tree(proc)
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, stop_session)
    try:
        server_cmd = list(map(str, server_argv))
        if os.name == "nt" and server_cmd:
            resolved = shutil.which(server_cmd[0])
            if resolved:
                server_cmd[0] = resolved
        proc = subprocess.Popen(
            server_cmd, cwd=runtime.app_root,
            env=runtime.env.copy(), shell=(os.name == "nt"), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, errors="replace", bufsize=1,
            start_new_session=(os.name != "nt"),
            creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0),
        )
    except OSError as exc:
        if previous_sigterm is not None:
            signal.signal(signal.SIGTERM, previous_sigterm)
        message = runtime._redact(str(exc))
        return CommandResult(tuple(map(str, server_argv)), str(runtime.app_root), 127, "", message)
    assert proc is not None

    def drain(stream, target: deque[str], size: list[int]) -> None:
        try:
            for line in iter(stream.readline, ""):
                with lock:
                    _append_bounded(target, size, line)
        finally:
            stream.close()

    readers = [threading.Thread(target=drain, args=(proc.stdout, out, out_size), daemon=True),
               threading.Thread(target=drain, args=(proc.stderr, err, err_size), daemon=True)]
    for reader in readers:
        reader.start()

    check_result: CommandResult | None = None
    timed_out = False
    readiness_error = ""
    try:
        deadline = min(started + timeout, time.monotonic() + readiness_timeout)
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                readiness_error = f"server exited before readiness (exit {proc.returncode})"
                break
            try:
                with urlopen(url, timeout=min(0.5, max(0.1, deadline - time.monotonic()))) as response:
                    if 200 <= response.status < 400:
                        break
            except (OSError, URLError, ValueError):
                pass
            time.sleep(0.15)
        else:
            readiness_error = f"server readiness timed out after {readiness_timeout:g}s"

        if not readiness_error:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
            else:
                check_result = runtime.run(check_argv, timeout=remaining)
                timed_out = check_result.timed_out
    finally:
        try:
            _terminate_tree(proc)
            for reader in readers:
                reader.join(timeout=1)
        finally:
            if previous_sigterm is not None:
                signal.signal(signal.SIGTERM, previous_sigterm)

    with lock:
        server_out, server_err = "".join(out), "".join(err)
    check_out = check_result.stdout if check_result else ""
    check_err = check_result.stderr if check_result else ""
    if readiness_error:
        check_err = (check_err + "\n" if check_err else "") + readiness_error
    return CommandResult(
        tuple(runtime._redact(str(x)) for x in check_argv), str(runtime.app_root),
        check_result.returncode if check_result else (-1 if timed_out or readiness_error else 1),
        runtime._redact(server_out + check_out),
        runtime._redact(server_err + check_err), timed_out,
    )
