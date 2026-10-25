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
    """Check whether a process with the given PID is currently alive across Windows and POSIX.

    Guarantees:
    - Non-positive or non-integer PIDs return False.
    - On Windows: Uses tasklist to confirm PID presence.
    - On Linux:
        * If /proc is mounted and /proc/<pid> does not exist, returns False immediately.
        * Parses /proc/<pid>/status for 'State:\tZ' (zombie) and 'State:\tX' (dead).
          Zombie processes waiting for reaper are never reported as alive.
        * Never lets a non-existent /proc/<pid> fall through to return True.
    - On non-Linux POSIX: Uses os.kill(pid, 0).
    """
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        return False

    if sys.platform == "win32":
        try:
            res = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            out = res.stdout or ""
            if "No tasks are running" in out:
                return False
            for line in out.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2 and parts[1] == str(pid):
                    return True
            return str(pid) in out
        except Exception:
            return False

    # Linux-specific /proc check
    if sys.platform.startswith("linux"):
        if Path("/proc").is_dir():
            proc_pid_dir = Path(f"/proc/{pid}")
            if not proc_pid_dir.exists():
                return False

            status_file = proc_pid_dir / "status"
            if status_file.exists():
                try:
                    content = status_file.read_text(encoding="utf-8", errors="ignore")
                    for line in content.splitlines():
                        stripped = line.strip()
                        if stripped.lower().startswith("state:"):
                            parts = stripped.split(":", 1)
                            if len(parts) == 2:
                                val = parts[1].strip()
                                if val and val[0].upper() in ("Z", "X"):
                                    return False
                            break
                except (FileNotFoundError, ProcessLookupError):
                    return False
                except Exception:
                    if not proc_pid_dir.exists():
                        return False
            else:
                stat_file = proc_pid_dir / "stat"
                if stat_file.exists():
                    try:
                        stat_content = stat_file.read_text(encoding="utf-8", errors="ignore")
                        rparen = stat_content.rfind(")")
                        if rparen != -1 and len(stat_content) > rparen + 2:
                            state_char = stat_content[rparen + 2].upper()
                            if state_char in ("Z", "X"):
                                return False
                    except Exception:
                        if not proc_pid_dir.exists():
                            return False
                elif not proc_pid_dir.exists():
                    return False

            if not proc_pid_dir.exists():
                return False

            try:
                os.kill(pid, 0)
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                return True
            except Exception:
                return False

    # Non-Linux POSIX fallback or when /proc is not mounted
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except Exception:
        return False


def _kill_unix_descendants(pid: int) -> None:
    """Recursively find and SIGKILL descendant processes of `pid` on Unix without using killpg."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or pid == os.getpid():
        return
    ppid = getattr(os, "getppid", lambda: None)()
    if ppid and pid == ppid:
        return

    child_pids: List[int] = []
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
                    c_pid = int(line)
                    if c_pid > 1 and c_pid != os.getpid() and c_pid != ppid:
                        child_pids.append(c_pid)
    except Exception:
        pass

    # Optional /proc fallback if pgrep returned nothing or failed on minimal Linux containers
    if not child_pids and sys.platform.startswith("linux") and Path("/proc").is_dir():
        try:
            for p in Path("/proc").iterdir():
                if p.is_dir() and p.name.isdigit():
                    c_pid = int(p.name)
                    if c_pid <= 1 or c_pid == os.getpid() or c_pid == ppid or c_pid == pid:
                        continue
                    status_f = p / "status"
                    if status_f.exists():
                        for s_line in status_f.read_text(encoding="utf-8", errors="ignore").splitlines():
                            if s_line.startswith("PPid:"):
                                ppid_val = s_line.split(":", 1)[1].strip()
                                if ppid_val.isdigit() and int(ppid_val) == pid:
                                    child_pids.append(c_pid)
                                break
        except Exception:
            pass

    for child_pid in child_pids:
        _kill_unix_descendants(child_pid)
        try:
            os.kill(child_pid, signal.SIGKILL)
        except ProcessLookupError:
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
        * ALWAYS actively discovers and recursively terminates descendant processes
          via _kill_unix_descendants.
        * If proc has its own process group (distinct from caller/parent), terminates
          the entire process group via os.killpg.
        * Never dispatches os.killpg to the parent's process group (e.g. orchestrator).
    - Always issues proc.kill() and proc.wait(timeout=1.0) as final fallback.
    """
    if proc is None:
        return

    raw_pid = getattr(proc, "pid", None)
    valid_int_pid = (
        isinstance(raw_pid, int)
        and not isinstance(raw_pid, bool)
        and raw_pid > 1
        and raw_pid != os.getpid()
        and raw_pid != getattr(os, "getppid", lambda: None)()
    )

    try:
        if valid_int_pid:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/F", "/T", "/PID", str(raw_pid)],
                    capture_output=True,
                    timeout=5,
                )
            else:
                # Unix execution: ALWAYS discover and kill descendants first
                _kill_unix_descendants(raw_pid)

                try:
                    pgid = os.getpgid(raw_pid)
                    parent_pgid = os.getpgrp()
                    # CRITICAL UNIX SAFETY:
                    # Only call os.killpg when pgid > 1, pgid == raw_pid (child is its own session/group leader),
                    # and pgid != parent_pgid. Never call killpg(0, ...) or killpg(1, ...), which on Linux
                    # can signal the caller's own process group (including CI runner agents).
                    if (
                        isinstance(pgid, int)
                        and not isinstance(pgid, bool)
                        and pgid > 1
                        and pgid == raw_pid
                        and pgid != parent_pgid
                    ):
                        os.killpg(pgid, signal.SIGKILL)
                    else:
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
