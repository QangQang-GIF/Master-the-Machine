"""Headless native subagent dispatcher for Codex (.codex/agents/*.toml) and Antigravity (.agents/agents/*.md)."""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any, Callable, Optional

from mtm_core.runtime import CodexRuntime, RuntimeResult
from .profiles import AgentProfile, export_native_agents, resolve_profile


class NativeSubagentDispatcher:
    """Dispatches specialist tasks to Codex or Antigravity native custom agents in isolated workspaces."""

    def __init__(
        self,
        runtime: Optional[CodexRuntime] = None,
        profiles_dir: Path | str | None = None,
    ):
        self.runtime = runtime or CodexRuntime()
        self.profiles_dir = Path(profiles_dir) if profiles_dir else None

    def resolve_task_profile(self, task_type: str) -> AgentProfile:
        return resolve_profile(task_type, self.profiles_dir)

    def prepare_workspace_agents(self, workspace: Path | str) -> tuple[list[Path], list[Path]]:
        """Materialize native agent definitions into workspace; return (written_files, newly_created_dirs)."""
        ws = Path(workspace).resolve()
        created_dirs: list[Path] = []
        for rel_dir in (Path(".codex"), Path(".codex/agents"), Path(".agents"), Path(".agents/agents")):
            target_dir = ws / rel_dir
            if not target_dir.exists():
                created_dirs.append(target_dir)
        written = export_native_agents(ws, self.profiles_dir)
        return written, created_dirs

    @staticmethod
    def cleanup_workspace_agents(written_files: list[Path], created_dirs: list[Path]) -> None:
        """Remove ephemeral native agent definitions so customer repository diffs stay clean."""
        for f in written_files:
            try:
                if f.exists():
                    f.unlink()
            except OSError:
                pass
        for d in sorted(created_dirs, key=lambda p: len(p.parts), reverse=True):
            try:
                if d.exists() and not any(d.iterdir()):
                    d.rmdir()
            except OSError:
                pass

    def dispatch(
        self,
        envelope: dict[str, Any],
        prompt: str,
        workspace: Path | str,
        backend: str = "codex",
        model: str = "gpt-6.1-sol",
        effort: str = "medium",
        timeout: int = 420,
        sandbox_policy: str = "workspace-write",
        network_access: bool = True,
        log_file: Optional[Path | str] = None,
        on_process_start: Optional[Callable[[Any], None]] = None,
        cancel_event: Optional[Any] = None,
        pause_event: Optional[Any] = None,
    ) -> RuntimeResult:
        """Export native agent profiles into `workspace`, invoke CLI with `--agent` / `-c agent=...`, and clean up."""
        ws = Path(workspace).resolve()
        profile = self.resolve_task_profile(str(envelope.get("task_type", "TASK")))
        written, created_dirs = self.prepare_workspace_agents(ws)
        try:
            return self.runtime.run(
                workspace=ws,
                prompt=prompt,
                model=model,
                effort=effort,
                timeout=timeout,
                sandbox_policy=sandbox_policy,
                network_access=network_access,
                log_file=log_file,
                backend=backend,
                on_process_start=on_process_start,
                cancel_event=cancel_event,
                pause_event=pause_event,
                agent=profile.name,
            )
        finally:
            self.cleanup_workspace_agents(written, created_dirs)
