"""Process execution helpers for headless agent CLI runners."""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Tuple


def is_cli_available(cli_name: str) -> bool:
    """Check if the given CLI binary is available on PATH."""
    return shutil.which(cli_name) is not None


def get_process_create_time(pid: int) -> Optional[float]:
    """Return the OS process creation timestamp (Unix epoch seconds) for `pid`, or None if unavailable/dead."""
    if not pid or pid <= 0:
        return None
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return None
            try:
                creation_time = wintypes.FILETIME()
                exit_time = wintypes.FILETIME()
                kernel_time = wintypes.FILETIME()
                user_time = wintypes.FILETIME()
                ok = kernel32.GetProcessTimes(
                    handle,
                    ctypes.byref(creation_time),
                    ctypes.byref(exit_time),
                    ctypes.byref(kernel_time),
                    ctypes.byref(user_time),
                )
                if not ok:
                    return None
                ft = (creation_time.dwHighDateTime << 32) | creation_time.dwLowDateTime
                if ft <= 0:
                    return None
                # Convert Windows FILETIME (100-ns intervals since 1601-01-01) to Unix epoch seconds
                return (ft - 116444736000000000) / 10_000_000.0
            finally:
                kernel32.CloseHandle(handle)
        except Exception:
            return None
    else:
        try:
            proc_dir = Path(f"/proc/{int(pid)}")
            if proc_dir.exists():
                return float(proc_dir.stat().st_ctime)
            res = subprocess.run(
                ["ps", "-p", str(int(pid)), "-o", "etimes="],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if res.returncode == 0 and res.stdout.strip().isdigit():
                import time as _time

                return _time.time() - float(res.stdout.strip())
        except Exception:
            return None
    return None


def is_expected_worker_pid(
    pid: int,
    recorded_start_time: Optional[float],
    task_updated_at: Optional[float] = None,
    tolerance_sec: float = 5.0,
) -> bool:
    """Verify that `pid` still belongs to the recorded worker process and was not recycled by the OS."""
    if not pid or pid <= 1 or pid == os.getpid():
        return False
    ppid = getattr(os, "getppid", lambda: None)()
    if ppid and pid == ppid:
        return False
    if not is_pid_alive(pid):
        return False

    actual_start = get_process_create_time(pid)
    if actual_start is None:
        return False

    if recorded_start_time is not None:
        return abs(actual_start - float(recorded_start_time)) <= tolerance_sec

    if task_updated_at is not None:
        # Legacy fallback when only task_updated_at is present: process must not have started after last update
        return actual_start <= float(task_updated_at) + tolerance_sec

    return False


def is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is currently alive across Windows and POSIX."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return str(pid) in (res.stdout or "")
        except Exception:
            return False
    else:
        try:
            os.kill(pid, 0)
            # On Linux, also check if it is a zombie process
            stat_file = Path(f"/proc/{pid}/stat")
            if stat_file.exists():
                content = stat_file.read_text(encoding="utf-8", errors="ignore")
                # Format: pid (comm) state ...
                rparen = content.rfind(")")
                if rparen != -1 and len(content) > rparen + 2:
                    state_char = content[rparen + 2]
                    if state_char == "Z":
                        return False
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False


def _kill_unix_descendants(pid: int) -> None:
    """Recursively find and SIGKILL descendant processes of `pid` on Unix without using killpg."""
    try:
        res = subprocess.run(
            ["pgrep", "-P", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout:
            for line in res.stdout.strip().splitlines():
                line = line.strip()
                if line.isdigit():
                    child_pid = int(line)
                    _kill_unix_descendants(child_pid)
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
    except Exception:
        pass


def run_command(
    cmd: List[str],
    cwd: Path | str,
    timeout: Optional[float] = None,
) -> Tuple[int, str, str, bool]:
    """Execute command with timeout.

    Returns (returncode, stdout, stderr, timed_out).
    """
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr, False
    except subprocess.TimeoutExpired as e:
        return -1, e.stdout or "", e.stderr or "Process timed out", True
    except Exception as e:
        return -1, "", str(e), False


def terminate_process_tree(proc: subprocess.Popen) -> None:
    """Cross-platform termination of a process and all its children.

    Guarantees:
    - On Windows: Uses taskkill /F /T to terminate child and grandchild trees.
    - On Unix:
        * If proc has its own process group (distinct from caller/parent), terminates
          the entire process group via os.killpg.
        * If proc shares caller's process group, recursively kills all descendants via pgrep -P
          and then kills the child process via proc.kill().
          Guarantees os.killpg is NEVER dispatched to the parent's process group (e.g. orchestrator).
    - Always issues proc.kill() as final fallback.
    """
    if proc is None:
        return

    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            try:
                pgid = os.getpgid(proc.pid)
                parent_pgid = os.getpgrp()
                if pgid != parent_pgid:
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    # Child shares caller's process group - recursively kill descendants, then child
                    _kill_unix_descendants(proc.pid)
                    proc.kill()
            except ProcessLookupError:
                pass
    except Exception:
        pass

    try:
        proc.kill()
        proc.wait(timeout=1.0)
    except Exception:
        pass
