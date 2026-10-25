"""Runtime checks; never assume native subagents work in noninteractive CLI mode."""
from __future__ import annotations

from dataclasses import dataclass
import shutil


@dataclass(frozen=True)
class BackendCapabilities:
    backend: str
    executable: str | None
    cli_available: bool
    native_headless_verified: bool = False
    details: str = "Native delegation requires a separate authenticated server smoke test."


def probe_backend(backend: str) -> BackendCapabilities:
    if backend not in {"codex", "antigravity"}:
        raise ValueError(f"Unknown backend: {backend}")
    executable = shutil.which("codex" if backend == "codex" else "agy")
    return BackendCapabilities(backend=backend, executable=executable, cli_available=bool(executable))


def select_available_backend(preferred: str = "codex") -> str:
    """Deterministic fallback is only for explicit discovery, never silent mid-attempt switching."""
    if preferred not in {"codex", "antigravity"}:
        raise ValueError("preferred must be codex or antigravity")
    other = "antigravity" if preferred == "codex" else "codex"
    for backend in (preferred, other):
        if probe_backend(backend).cli_available:
            return backend
    raise RuntimeError("No supported backend CLI available")
