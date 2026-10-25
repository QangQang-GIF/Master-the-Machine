"""Safe production verification command policy with expanded per-stack templates."""
from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

_ALLOWED = {
    "pytest",
    "tox",
    "mvn",
    "mvnw",
    "gradle",
    "gradlew",
    "npm",
    "pnpm",
    "yarn",
    "npx",
    "jest",
    "vitest",
    "mocha",
    "go",
    "cargo",
    "dotnet",
    "make",
    "ctest",
    "uv",
    "poetry",
    "bundle",
    "rspec",
    "phpunit",
    "cobc",
}
_PYTHON_MODULES = {"pytest", "unittest", "compileall", "doctest"}
_NPX_RUNNERS = {"jest", "vitest", "mocha", "tsc", "playwright"}
_BUNDLE_RUNNERS = {"rspec", "rake"}
_SHELL_MARKERS = (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "\n", "\r")
_FORBIDDEN_INLINE_FLAGS = {"-c", "-e", "--eval", "--call"}


def strict_verification_enabled() -> bool:
    return (
        os.getenv("MTM_ENV", os.getenv("MTM_MODE", "development")).lower() == "production"
        or os.getenv("MTM_SECURE_TEST_COMMANDS") == "1"
    )


def parse_safe_command(command: str) -> list[str]:
    if not isinstance(command, str) or not command.strip() or len(command) > 2000:
        raise ValueError("Empty or oversized test command")
    if any(ch in command for ch in ("\n", "\r")):
        raise ValueError("Multiline commands are not allowed")
    posix_mode = sys.platform != "win32"
    argv = [arg.strip('"\'') for arg in shlex.split(command, posix=posix_mode)]
    if not argv:
        raise ValueError("Empty test command")
    if any(any(marker in arg for marker in _SHELL_MARKERS) for arg in argv):
        raise ValueError("Shell control syntax is not allowed")

    raw_exec = argv[0].replace("\\", "/")
    executable = Path(raw_exec).name.lower()
    if executable.endswith(".exe") or executable.endswith(".cmd") or executable.endswith(".bat"):
        executable = Path(executable).stem.lower()

    if executable.startswith("python") or executable == "py":
        if len(argv) < 3 or argv[1] != "-m" or argv[2] not in _PYTHON_MODULES:
            raise ValueError("Only python -m approved test modules are allowed")
        if any(arg in _FORBIDDEN_INLINE_FLAGS for arg in argv[3:]):
            raise ValueError("Inline code execution flags are not allowed")
        return argv

    if executable not in _ALLOWED:
        raise ValueError(f"Test executable not allowed: {executable}")

    if any(arg in _FORBIDDEN_INLINE_FLAGS for arg in argv[1:]):
        raise ValueError("Inline code execution flags are not allowed")

    if executable in {"npm", "pnpm", "yarn"}:
        if any(arg in {"exec", "dlx", "add", "install", "create", "init", "publish"} for arg in argv[1:]):
            raise ValueError("Package installation/arbitrary execution is not verification")
        if len(argv) < 2:
            raise ValueError(f"{executable} requires a test subcommand")
        sub = argv[1]
        if sub not in {"test", "t", "tst", "run"}:
            raise ValueError(f"Only test or run test* subcommands are allowed for {executable}")
        if sub == "run":
            if len(argv) < 3 or not argv[2].startswith(("test", "check", "verify", "typecheck", "lint")):
                raise ValueError(f"Unapproved {executable} run script: {argv[2] if len(argv) > 2 else ''}")

    elif executable == "npx":
        if len(argv) < 2 or argv[1] not in _NPX_RUNNERS:
            raise ValueError("Only approved npx test runners (jest, vitest, mocha, tsc, playwright) are allowed")
        if any(arg in {"--package", "-p", "--yes", "-y", "dlx", "exec"} for arg in argv[1:]):
            raise ValueError("Dynamic package installation via npx is not allowed")

    elif executable in {"uv", "poetry"}:
        if len(argv) < 3 or argv[1] != "run" or argv[2] not in {"pytest", "unittest"}:
            raise ValueError(f"Only {executable} run pytest/unittest is approved")

    elif executable == "bundle":
        if len(argv) < 3 or argv[1] != "exec" or argv[2] not in _BUNDLE_RUNNERS:
            raise ValueError("Only bundle exec rspec/rake is approved")

    elif executable == "go":
        if len(argv) < 2 or argv[1] not in {"test", "vet"}:
            raise ValueError("Only go test/vet is approved")
        if any(arg.startswith("-exec") for arg in argv[2:]):
            raise ValueError("go test -exec is not allowed")

    elif executable == "cargo":
        if len(argv) < 2 or argv[1] not in {"test", "check"}:
            raise ValueError("Only cargo test/check is approved")

    elif executable == "dotnet":
        if len(argv) < 2 or argv[1] not in {"test", "build"}:
            raise ValueError("Only dotnet test/build is approved")

    return argv


def run_verification_command(
    command: str,
    cwd: Path | str,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    if strict_verification_enabled():
        argv = parse_safe_command(command)
        cwd_path = Path(cwd).resolve()
        if argv[0].startswith(("./", ".\\")):
            candidate = (cwd_path / argv[0]).resolve()
            try:
                candidate.relative_to(cwd_path)
            except ValueError as exc:
                raise ValueError("Relative executable escapes workspace") from exc
            argv = [str(candidate), *argv[1:]]
        elif sys.platform == "win32" and argv[0] == "pytest":
            argv = [sys.executable, "-m", "pytest", *argv[1:]]
        return subprocess.run(
            argv,
            shell=False,
            cwd=str(cwd_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    return subprocess.run(
        command,
        shell=True,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=timeout,
    )
