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
    - On Windows: Uses taskkill /F /T to terminate child trees.
    - On Unix:
        * If proc has its own process group (distinct from caller/parent), terminates
          the entire process group via os.killpg.
        * If proc shares caller's process group, terminates ONLY the child process via proc.kill().
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
                    # Child is in the caller's process group - kill child directly without killing caller
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
