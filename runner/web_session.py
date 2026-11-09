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
from pathlib import Path
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


def _kill_port_listeners(port: int) -> None:
    if os.name != "nt" or not port:
        return
    try:
        ps_cmd = f"Get-NetTCPConnection -LocalPort {port} -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", ps_cmd], shell=False, capture_output=True, timeout=5)
    except Exception:
        pass


def _terminate_tree(proc: subprocess.Popen[str], grace: float = 2.0) -> None:
    if os.name == "nt":
        # On Windows, always kill entire process tree (/T /F) to prevent orphaned node.exe holding ports!
        try:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                           capture_output=True, check=False, timeout=grace)
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass
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
    port = int(runtime.env.get("PORT", 0))
    if port:
        _kill_port_listeners(port)
    try:
        server_cmd = list(map(str, server_argv))
        if os.name == "nt" and server_cmd:
            base_name = server_cmd[0]
            resolved = None
            for ext in (".cmd", ".exe", ".bat"):
                cand = shutil.which(base_name + ext if not base_name.lower().endswith(ext) else base_name)
                if cand and Path(cand).suffix.lower() in {".cmd", ".exe", ".bat"}:
                    resolved = cand
                    break
            if not resolved:
                resolved = shutil.which(base_name)
            if resolved:
                if Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                    server_cmd = ["cmd.exe", "/c", resolved] + server_cmd[1:]
                elif Path(resolved).suffix.lower() == ".exe":
                    server_cmd[0] = resolved
        proc = subprocess.Popen(
            server_cmd, cwd=runtime.app_root,
            env=runtime.env.copy(), shell=False, stdout=subprocess.PIPE,
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
            # Pre-warm common Next.js routes so Playwright / checkers don't hit cold on-demand compilation timeouts
            if port:
                prewarm_routes(port)

            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                timed_out = True
            else:
                check_result = runtime.run(check_argv, timeout=remaining)
                timed_out = check_result.timed_out
    finally:
        try:
            _terminate_tree(proc)
            if port:
                _kill_port_listeners(port)
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


def prewarm_routes(port: int, routes: list[str] | None = None, timeout: float = 10.0) -> None:
    """Compile common Next.js routes in parallel (was sequential: up to 4 x 10 s per session)."""
    if routes is None:
        raw = os.environ.get("MTM_PREWARM_ROUTES", "/dashboard,/accounting,/boards,/login")
        routes = [r.strip() for r in raw.split(",") if r.strip().startswith("/")]

    def hit(route: str) -> None:
        try:
            with urlopen(f"http://127.0.0.1:{port}{route}", timeout=timeout) as _:
                pass
        except Exception:
            pass

    threads = [threading.Thread(target=hit, args=(r,), daemon=True) for r in routes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout + 1)


def _resolve_server_cmd(server_argv: list[str]) -> list[str]:
    server_cmd = list(map(str, server_argv))
    if os.name == "nt" and server_cmd:
        base_name = server_cmd[0]
        resolved = None
        for ext in (".cmd", ".exe", ".bat"):
            cand = shutil.which(base_name + ext if not base_name.lower().endswith(ext) else base_name)
            if cand and Path(cand).suffix.lower() in {".cmd", ".exe", ".bat"}:
                resolved = cand
                break
        if not resolved:
            resolved = shutil.which(base_name)
        if resolved:
            if Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                server_cmd = ["cmd.exe", "/c", resolved] + server_cmd[1:]
            elif Path(resolved).suffix.lower() == ".exe":
                server_cmd[0] = resolved
    return server_cmd


# ---- Persistent per-worker server ----------------------------------------------------------
# `session` starts and kills a fresh `next dev` for every capture/check, so every evidence step
# pays the cold start and route compilation again. A worker can instead start one server for the
# whole item (`serve-start`), run all captures against $PORT with `run`, and stop it at the end
# (`serve-stop`). State lives in the system temp directory, never in the worktree.

def _server_state_path(runtime: ProjectRuntime) -> Path:
    import tempfile
    d = Path(tempfile.gettempdir()) / "mtm-servers"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{runtime.match_id}_{runtime.worker_id}.json"


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True, timeout=5)
            return str(pid) in out.stdout
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _responds(url: str, timeout: float = 1.0) -> bool:
    try:
        with urlopen(url, timeout=timeout) as response:
            return 200 <= response.status < 500
    except Exception:
        return False


def start_background_server(runtime: ProjectRuntime, server_argv: list[str], readiness_url: str | None = None,
                            readiness_timeout: float = 90) -> dict:
    import json
    url = _validate_readiness_url(runtime, readiness_url)
    state_path = _server_state_path(runtime)
    port = int(runtime.env["PORT"])
    if state_path.is_file():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            if _pid_alive(int(state.get("pid", 0))) and _responds(url):
                return {"status": "READY", "reused": True, "port": port, "pid": state["pid"], "url": url}
        except Exception:
            pass
        stop_background_server(runtime)
    _kill_port_listeners(port)
    log_path = state_path.with_suffix(".log")
    log = open(log_path, "a", encoding="utf-8", errors="replace")
    def _spawn(flags: int):
        return subprocess.Popen(
            _resolve_server_cmd(server_argv), cwd=runtime.app_root, env=runtime.env.copy(), shell=False,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=(os.name != "nt"), creationflags=flags,
        )
    try:
        if os.name == "nt":
            # Detach from the caller's console and job so the server outlives this command; fall back
            # to a plain new process group if the job object forbids breakaway.
            base = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)
            detach = base | getattr(subprocess, "DETACHED_PROCESS", 0) | 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
            try:
                proc = _spawn(detach)
            except OSError:
                proc = _spawn(base)
        else:
            proc = _spawn(0)
    except OSError as exc:
        log.close()
        return {"status": "FAILED", "reason": runtime._redact(str(exc))}
    state_path.write_text(json.dumps({"pid": proc.pid, "port": port, "url": url, "startedAt": time.time()}), encoding="utf-8")
    deadline = time.monotonic() + readiness_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            log.close()
            state_path.unlink(missing_ok=True)
            return {"status": "FAILED", "reason": f"server exited before readiness (exit {proc.returncode})", "log": str(log_path)}
        if _responds(url, 0.5):
            prewarm_routes(port)
            log.close()
            return {"status": "READY", "reused": False, "port": port, "pid": proc.pid, "url": url, "log": str(log_path)}
        time.sleep(0.25)
    log.close()
    stop_background_server(runtime)
    return {"status": "FAILED", "reason": f"server readiness timed out after {readiness_timeout:g}s", "log": str(log_path)}


def stop_background_server(runtime: ProjectRuntime) -> dict:
    import json
    state_path = _server_state_path(runtime)
    port = int(runtime.env.get("PORT", 0) or 0)
    pid = None
    if state_path.is_file():
        try:
            pid = int(json.loads(state_path.read_text(encoding="utf-8")).get("pid", 0))
        except Exception:
            pid = None
    if pid:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10)
            except Exception:
                pass
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    break
                time.sleep(0.5)
    if port:
        _kill_port_listeners(port)
    state_path.unlink(missing_ok=True)
    return {"status": "STOPPED", "pid": pid, "port": port}
