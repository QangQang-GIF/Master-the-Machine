"""Safe production verification command policy."""
from __future__ import annotations
import os
import shlex
import subprocess
from pathlib import Path

_ALLOWED = {"pytest", "tox", "mvn", "mvnw", "gradle", "gradlew", "npm", "pnpm", "yarn", "go", "cargo", "dotnet", "make", "uv"}
_PYTHON_MODULES = {"pytest", "unittest", "compileall"}

def strict_verification_enabled() -> bool:
    return os.getenv("MTM_ENV", os.getenv("MTM_MODE", "development")).lower() == "production" or os.getenv("MTM_SECURE_TEST_COMMANDS") == "1"

def parse_safe_command(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip() or len(command) > 2000:
        raise ValueError("Empty or oversized test command")
    argv = shlex.split(command)
    executable = Path(argv[0]).name.lower()
    if executable.startswith("python"):
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in _PYTHON_MODULES:
            raise ValueError("Only python -m approved test modules are allowed")
    elif executable not in _ALLOWED:
        raise ValueError(f"Test executable not allowed: {executable}")
    if any(any(marker in arg for marker in (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "\n", "\r")) for arg in argv):
        raise ValueError("Shell control syntax is not allowed")
    if executable in {"npm", "pnpm", "yarn"} and any(arg in {"exec", "dlx", "add", "install"} for arg in argv[1:]):
        raise ValueError("Package installation/arbitrary execution is not verification")
    if executable == "uv" and argv[1:3] != ["run", "pytest"]:
        raise ValueError("Only uv run pytest is approved")
    return argv

def run_verification_command(command: str, cwd: Path | str, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    if strict_verification_enabled():
        return subprocess.run(parse_safe_command(command), shell=False, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    return subprocess.run(command, shell=True, cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
