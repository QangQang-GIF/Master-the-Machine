"""Process execution helpers for headless agent CLI runners."""
from __future__ import annotations

import shutil
import subprocess
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
