"""Runtime checks; never assume native subagents work in noninteractive CLI mode without live verification."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tempfile
import time
import uuid


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


def verify_native_headless(
    backend: str,
    model: str | None = None,
    timeout: int = 45,
) -> BackendCapabilities:
    """Execute a live disposable smoke test of headless native custom-agent dispatch for `backend`.

    Never claims `native_headless_verified=True` unless the real CLI binary executes the custom agent
    profile non-interactively and exits with code 0.
    """
    base = probe_backend(backend)
    if not base.cli_available:
        return BackendCapabilities(
            backend=backend,
            executable=None,
            cli_available=False,
            native_headless_verified=False,
            details=f"BLOCKED: {backend} CLI binary not found on PATH.",
        )

    from mtm_agents.dispatcher import NativeSubagentDispatcher
    from mtm_core.runtime import CodexRuntime

    chosen_model = model or ("gpt-6.1-sol" if backend == "codex" else "gemini-3.8-flash-low")
    tmp_name = f"mtm_native_{backend}_{uuid.uuid4().hex[:8]}"
    ws = Path(tempfile.gettempdir()) / tmp_name
    ws.mkdir(parents=True, exist_ok=True)
    try:
        rt = CodexRuntime(default_backend=backend, strict_backend=True)
        dispatcher = NativeSubagentDispatcher(runtime=rt)
        res = dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Reply with the single word OK.",
            workspace=ws,
            backend=backend,
            model=chosen_model,
            effort="low",
            timeout=timeout,
        )
        verified = res.exit_code == 0 and not res.timed_out
        return BackendCapabilities(
            backend=backend,
            executable=base.executable,
            cli_available=True,
            native_headless_verified=verified,
            details=(
                f"Verified live headless native agent dispatch on {backend} (model={chosen_model}, elapsed={res.elapsed}s)."
                if verified
                else f"Headless native agent dispatch failed on {backend} (exit_code={res.exit_code}, timed_out={res.timed_out})."
            ),
        )
    finally:
        time.sleep(0.1)
        shutil.rmtree(ws, ignore_errors=True)
