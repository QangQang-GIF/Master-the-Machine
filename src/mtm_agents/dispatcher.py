"""Headless native subagent dispatcher for Codex (.codex/agents/*.toml) and Antigravity (.agents/agents/*.md)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from typing import Any, Callable, Optional

from mtm_core.config.loader import get_project_root
from mtm_core.runtime import CodexRuntime, RuntimeResult
from .profiles import AgentProfile, export_native_agents, resolve_profile


@dataclass(frozen=True)
class FileBackup:
    path: Path
    content: bytes
    sha256: str
    atime_ns: int
    mtime_ns: int
    mode: int

    @classmethod
    def capture(cls, path: Path | str) -> "FileBackup":
        p = Path(path).resolve()
        st = p.stat()
        content = p.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        return cls(
            path=p,
            content=content,
            sha256=digest,
            atime_ns=st.st_atime_ns,
            mtime_ns=st.st_mtime_ns,
            mode=st.st_mode,
        )

    def restore(self) -> None:
        if self.path.exists() or self.path.is_symlink():
            try:
                os.chmod(self.path, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)

        self.path.write_bytes(self.content)
        try:
            os.utime(self.path, ns=(self.atime_ns, self.mtime_ns))
        except OSError:
            pass
        try:
            os.chmod(self.path, self.mode)
        except OSError:
            pass
        try:
            os.utime(self.path, ns=(self.atime_ns, self.mtime_ns))
        except OSError:
            pass
        restored_hash = hashlib.sha256(self.path.read_bytes()).hexdigest()
        if restored_hash != self.sha256:
            raise ValueError(
                f"Restored file {self.path} hash mismatch: expected {self.sha256}, got {restored_hash}"
            )


class WrittenFiles(list):
    """List of written paths that also preserves backup metadata for unpack callers."""
    backups: dict[Path, FileBackup]
    ephemeral_files: list[Path]

    def __init__(
        self,
        items: list[Path],
        backups: dict[Path, FileBackup],
        ephemeral_files: list[Path],
    ):
        super().__init__(items)
        self.backups = backups
        self.ephemeral_files = ephemeral_files


class WorkspacePreparation(tuple):
    """Holds workspace state for cleanup. Unpacks as (written_files, created_dirs) for legacy callers."""
    written_files: list[Path]
    created_dirs: list[Path]
    backups: dict[Path, FileBackup]
    ephemeral_files: list[Path]

    def __new__(
        cls,
        written_files: list[Path],
        created_dirs: list[Path],
        backups: dict[Path, FileBackup],
        ephemeral_files: list[Path],
    ) -> "WorkspacePreparation":
        written_wrapped = (
            written_files
            if isinstance(written_files, WrittenFiles)
            else WrittenFiles(written_files, backups, ephemeral_files)
        )
        instance = super().__new__(cls, (written_wrapped, created_dirs))
        instance.written_files = written_wrapped
        instance.created_dirs = created_dirs
        instance.backups = backups
        instance.ephemeral_files = ephemeral_files
        return instance


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

    def prepare_workspace_agents(self, workspace: Path | str) -> WorkspacePreparation:
        """Materialize native agent definitions into workspace; return WorkspacePreparation."""
        ws = Path(workspace).resolve()
        created_dirs: list[Path] = []
        for rel_dir in (Path(".codex"), Path(".codex/agents"), Path(".agents"), Path(".agents/agents")):
            target_dir = (ws / rel_dir).resolve()
            if not target_dir.exists() and target_dir not in created_dirs:
                created_dirs.append(target_dir)

        source = (
            Path(self.profiles_dir).resolve()
            if self.profiles_dir
            else (get_project_root() / "profiles" / "agents").resolve()
        )
        prospective_files: list[Path] = []
        if source.exists() and source.is_dir():
            for profile_file in sorted(source.glob("*.yaml")):
                try:
                    profile = AgentProfile.load(profile_file)
                    prospective_files.append((ws / ".codex" / "agents" / f"{profile.name}.toml").resolve())
                    prospective_files.append((ws / ".agents" / "agents" / f"{profile.name}.md").resolve())
                except Exception:
                    continue

        backups: dict[Path, FileBackup] = {}
        ephemeral_files: list[Path] = []
        for dest in prospective_files:
            if dest.exists() or dest.is_symlink():
                try:
                    backups[dest] = FileBackup.capture(dest)
                except OSError:
                    pass
                try:
                    os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass
            else:
                ephemeral_files.append(dest)

        try:
            written = export_native_agents(ws, self.profiles_dir)
            resolved_written = [p.resolve() for p in written]
            for p in resolved_written:
                if p not in backups and p not in ephemeral_files:
                    ephemeral_files.append(p)
            return WorkspacePreparation(
                written_files=resolved_written,
                created_dirs=created_dirs,
                backups=backups,
                ephemeral_files=ephemeral_files,
            )
        except Exception:
            partial_prep = WorkspacePreparation(
                written_files=[p.resolve() for p in prospective_files],
                created_dirs=created_dirs,
                backups=backups,
                ephemeral_files=ephemeral_files,
            )
            self.cleanup_workspace_agents(partial_prep)
            raise

    @staticmethod
    def cleanup_workspace_agents(
        written_files: WorkspacePreparation | list[Path] | tuple[Any, ...],
        created_dirs: Optional[list[Path]] = None,
    ) -> None:
        """Remove ephemeral native agent definitions and restore backed-up files."""
        if isinstance(written_files, WorkspacePreparation):
            backups = written_files.backups
            ephemeral_files = written_files.ephemeral_files
            dirs_to_clean = created_dirs if created_dirs is not None else written_files.created_dirs
        elif (
            isinstance(written_files, tuple)
            and len(written_files) == 2
            and created_dirs is None
            and isinstance(written_files[0], (list, tuple))
            and isinstance(written_files[1], (list, tuple))
        ):
            target_files, target_dirs = written_files
            if hasattr(target_files, "backups") and hasattr(target_files, "ephemeral_files"):
                backups = getattr(target_files, "backups")
                ephemeral_files = getattr(target_files, "ephemeral_files")
            else:
                backups = {}
                ephemeral_files = list(target_files)
            dirs_to_clean = list(target_dirs)
        elif hasattr(written_files, "backups") and hasattr(written_files, "ephemeral_files"):
            backups = getattr(written_files, "backups")
            ephemeral_files = getattr(written_files, "ephemeral_files")
            dirs_to_clean = created_dirs if created_dirs is not None else []
        else:
            backups = {}
            ephemeral_files = list(written_files) if written_files else []
            dirs_to_clean = created_dirs if created_dirs is not None else []

        # 1. Unlink ephemeral files only
        for f in ephemeral_files:
            try:
                p = Path(f).resolve()
                if p.exists() or p.is_symlink():
                    try:
                        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
                    except OSError:
                        pass
                    p.unlink()
            except OSError:
                pass

        # 2. Restore all backed-up files
        restore_errors: list[Exception] = []
        for backup in backups.values():
            try:
                backup.restore()
            except Exception as e:
                restore_errors.append(e)

        # 3. Remove only empty created directories
        for d in sorted(dirs_to_clean, key=lambda p: len(p.parts), reverse=True):
            try:
                p = Path(d).resolve()
                if p.exists() and not any(p.iterdir()):
                    p.rmdir()
            except OSError:
                pass

        if restore_errors:
            raise restore_errors[0]

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
        prep = self.prepare_workspace_agents(ws)
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
            self.cleanup_workspace_agents(prep)

