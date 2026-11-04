"""Run a bounded local web server and its verification command together.

The server and check share one in-memory ProjectRuntime environment. Output is
kept in a small ring-like buffer and is never written to disk by this helper.
"""
from __future__ import annotations

import json
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


DEFAULT_PREWARM_ROUTES = ("/dashboard", "/accounting", "/boards", "/login")
SESSION_FILE = ".mtm-session.json"


def session_record_path(match_root) -> Path:
    """Where a background server's PID is recorded: outside the Git repository folder,
    so a worker's `git add -A` can never commit it."""
    root = Path(match_root).resolve()
    digest = __import__("hashlib").sha256(str(root).encode()).hexdigest()[:12]
    base = root.parent.parent if root.parent.parent != root.parent else root.parent
    return base / f"{SESSION_FILE[:-5]}-{digest}.json"


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


def _clean_routes(routes) -> list[str]:
    out = []
    for route in routes or ():
        route = str(route).strip()
        if route and route.startswith("/") and "//" not in route and route not in out:
            out.append(route)
    return out


def prewarm_routes(port: int, routes, per_route_timeout: float = 20) -> dict[str, object]:
    """Request every route in parallel so Next.js compiles them before the first capture."""
    routes = _clean_routes(routes)
    results: dict[str, object] = {}
    if not port or not routes:
        return results

    def hit(route: str) -> None:
        started = time.monotonic()
        try:
            with urlopen(f"http://127.0.0.1:{port}{route}", timeout=per_route_timeout) as response:
                results[route] = {"status": response.status, "ms": int((time.monotonic() - started) * 1000)}
        except Exception as exc:  # 401/redirects still compile the route
            code = getattr(exc, "code", None)
            results[route] = {"status": code or "error", "ms": int((time.monotonic() - started) * 1000)}

    threads = [threading.Thread(target=hit, args=(r,), daemon=True) for r in routes]
    for t in threads:
        t.start()
    for t in threads:
        t.join(per_route_timeout + 1)
    return results


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, timeout=5)
            return str(pid) in out.stdout
        except Exception:
            return False
    try:
        # Reap our own exited child first so a zombie does not look alive.
        reaped, _ = os.waitpid(pid, os.WNOHANG)
        if reaped == pid:
            return False
    except ChildProcessError:
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _url_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=0.5) as response:
            return 200 <= response.status < 400
    except Exception:
        return False


def start_background_server(runtime: ProjectRuntime, server_argv: list[str], routes=None,
                            readiness_url: str | None = None, readiness_timeout: float = 90) -> dict[str, object]:
    """Start the worker's dev server in the background and return once it answers.

    Used at the start of a UI task so compilation overlaps with coding; later captures
    run with `project_runtime.py ... run` and reach the same PORT (it is derived from
    match/worker IDs). The PID is recorded outside the repository (see session_record_path).
    """
    url = _validate_readiness_url(runtime, readiness_url)
    port = int(runtime.env["PORT"])
    record_path = session_record_path(runtime.match_root)
    routes = _clean_routes(routes) or list(DEFAULT_PREWARM_ROUTES)
    if record_path.is_file():
        try:
            record = json.loads(record_path.read_text(encoding="utf-8"))
            if _pid_alive(int(record.get("pid", 0))) and _url_ready(url):
                return {"status": "ALREADY_RUNNING", "port": port, "pid": record["pid"]}
        except (ValueError, OSError):
            pass
        stop_recorded_session(runtime.match_root)
    _kill_port_listeners(port)
    started = time.monotonic()
    creation = 0
    if os.name == "nt":
        creation = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        proc = subprocess.Popen(_resolve_server_cmd(server_argv), cwd=runtime.app_root, env=runtime.env.copy(),
                                shell=False, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, start_new_session=(os.name != "nt"),
                                creationflags=creation)
    except OSError as exc:
        return {"status": "FAILED", "port": port, "error": runtime._redact(str(exc))}
    record_path.write_text(json.dumps({"pid": proc.pid, "port": port, "startedAt": time.time(),
                                       "routes": routes}), encoding="utf-8")
    deadline = time.monotonic() + readiness_timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            stop_recorded_session(runtime.match_root)
            return {"status": "FAILED", "port": port, "error": f"server exited before readiness (exit {proc.returncode})"}
        if _url_ready(url):
            break
        time.sleep(0.2)
    else:
        stop_recorded_session(runtime.match_root)
        return {"status": "FAILED", "port": port, "error": f"server readiness timed out after {readiness_timeout:g}s"}
    ready_ms = int((time.monotonic() - started) * 1000)
    warm_started = time.monotonic()
    warmed = prewarm_routes(port, routes)
    return {"status": "READY", "port": port, "pid": proc.pid, "baseUrl": f"http://127.0.0.1:{port}",
            "serverReadyMs": ready_ms, "prewarmMs": int((time.monotonic() - warm_started) * 1000),
            "prewarmed": warmed}


def stop_recorded_session(match_root) -> dict[str, object]:
    """Stop a server started by start_background_server (whole process tree) and forget it."""
    record_path = session_record_path(match_root)
    if not record_path.is_file():
        return {"status": "NOT_RUNNING"}
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        record = {}
    pid, port = int(record.get("pid", 0) or 0), int(record.get("port", 0) or 0)
    if pid > 0:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
            except Exception:
                pass
        else:
            for sig in (signal.SIGTERM, signal.SIGKILL):
                try:
                    os.killpg(pid, sig)
                except (ProcessLookupError, PermissionError, OSError):
                    break
                for _ in range(20):
                    if not _pid_alive(pid):
                        break
                    time.sleep(0.1)
                if not _pid_alive(pid):
                    break
    if port:
        _kill_port_listeners(port)
    try:
        record_path.unlink()
    except OSError:
        pass
    return {"status": "STOPPED", "pid": pid, "port": port}


def run_web_session(runtime: ProjectRuntime, server_argv: list[str],
                    check_argv: list[str], readiness_url: str | None = None,
                    timeout: float = 300, readiness_timeout: float = 45, routes=None) -> CommandResult:
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
        server_cmd = _resolve_server_cmd(server_argv)
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
                prewarm_routes(port, _clean_routes(routes) or DEFAULT_PREWARM_ROUTES, per_route_timeout=10)

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
