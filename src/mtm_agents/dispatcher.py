"""Headless native subagent dispatcher for Codex (.codex/agents/*.toml) and Antigravity (.agents/agents/*.md)."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Callable, Optional

from mtm_core.config.loader import get_project_root
from mtm_core.runtime import CodexRuntime, RuntimeResult
from .profiles import AgentProfile, export_native_agents, resolve_profile


def _verify_workspace_containment(path: Path | str, workspace_root: Path | str) -> Path:
    """Verify that path does not escape workspace_root via symlinks, junctions, or relative traversal."""
    ws = Path(workspace_root).resolve()
    target = Path(path)
    if not target.is_absolute():
        target = ws / target
    try:
        resolved = target.resolve()
    except (RuntimeError, OSError) as exc:
        raise ValueError(f"Path resolution error for '{path}': {exc}") from exc

    # Check 1: Symlink / junction escape check
    is_link = target.is_symlink() or (hasattr(target, "is_junction") and target.is_junction())
    if is_link and not resolved.is_relative_to(ws):
        raise ValueError(
            f"Agent configuration path '{target}' is an external symlink pointing outside workspace: '{resolved}'"
        )

    # Check 2: General workspace containment (covers junctions, traversal, non-existent children)
    if not resolved.is_relative_to(ws):
        raise ValueError(
            f"Agent configuration path '{target}' resolves outside workspace root '{ws}': '{resolved}'"
        )

    # Disallow configuring the workspace root itself as an agent subfolder
    if target.name in (".codex", "agents") and resolved == ws:
        raise ValueError(
            f"Agent configuration path '{target}' cannot resolve to workspace root '{ws}'"
        )

    return resolved


def _is_link_or_reparse(path: Path) -> bool:
    """Check if path is a symlink, Windows junction, or reparse point."""
    if path.is_symlink():
        return True
    if hasattr(path, "is_junction") and path.is_junction():
        return True
    if os.name == "nt" and hasattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT"):
        try:
            st = os.lstat(path)
            if hasattr(st, "st_file_attributes") and (st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                return True
        except (OSError, ValueError):
            pass
    return False


def _remove_link_or_reparse(path: Path) -> None:
    """Safely unlink/remove a symlink or junction without touching its target."""
    try:
        path.unlink()
    except OSError:
        if path.is_dir():
            path.rmdir()
        else:
            raise


def _get_intermediate_ancestors(dest: Path, ws: Path) -> list[Path]:
    """Return all intermediate ancestor directories from ws down to dest.parent (inclusive)."""
    norm_dest = Path(os.path.normpath(dest))
    norm_ws = Path(os.path.normpath(ws))
    if not norm_dest.is_relative_to(norm_ws) or norm_dest == norm_ws:
        raise ValueError(
            f"Agent configuration path '{dest}' resolves outside workspace root '{ws}'"
        )
    ancestors: list[Path] = []
    curr = norm_dest.parent
    while curr != norm_ws:
        if not curr.is_relative_to(norm_ws):
            raise ValueError(
                f"Ancestor directory '{curr}' resolves outside workspace root '{ws}'"
            )
        ancestors.append(curr)
        parent = curr.parent
        if parent == curr:
            raise ValueError(
                f"Agent configuration path '{dest}' resolves outside workspace root '{ws}'"
            )
        curr = parent
    return list(reversed(ancestors))


def _prepare_and_verify_ancestors(dest: Path, ws: Path) -> None:
    """Safely prepare and verify all intermediate ancestor directories top-down.

    Ensures no external symlink, junction, or reparse point exists before creating any directory.
    """
    ancestors = _get_intermediate_ancestors(dest, ws)
    for anc in ancestors:
        # 1. Symlink / junction / reparse point check
        if _is_link_or_reparse(anc):
            try:
                res = anc.resolve()
            except (RuntimeError, OSError) as exc:
                raise ValueError(f"Cannot resolve ancestor link '{anc}': {exc}") from exc
            if not res.is_relative_to(ws) or res == ws:
                raise ValueError(
                    f"Agent configuration path '{anc}' is an external symlink pointing outside workspace: '{res}'"
                )

        # 2. General resolution check if it exists
        if anc.exists() or _is_link_or_reparse(anc):
            try:
                res = anc.resolve()
            except (RuntimeError, OSError) as exc:
                raise ValueError(f"Cannot resolve ancestor directory '{anc}': {exc}") from exc
            if not res.is_relative_to(ws) or res == ws:
                raise ValueError(
                    f"Agent configuration path '{anc}' resolves outside workspace root '{ws}': '{res}'"
                )
            if not anc.is_dir():
                raise ValueError(f"Ancestor '{anc}' exists but is not a directory")

        # 3. Create missing ancestor top-down without parents=True
        if not anc.exists():
            if _is_link_or_reparse(anc):
                raise ValueError(f"Ancestor directory '{anc}' is a broken link or junction")
            try:
                anc.mkdir()
            except FileExistsError:
                pass

            # Verify immediately after creation
            if _is_link_or_reparse(anc):
                try:
                    res = anc.resolve()
                except (RuntimeError, OSError) as exc:
                    raise ValueError(f"Cannot resolve ancestor link '{anc}': {exc}") from exc
                if not res.is_relative_to(ws) or res == ws:
                    raise ValueError(
                        f"Agent configuration path '{anc}' was created as external link or junction: '{res}'"
                    )
            if not anc.is_dir():
                raise ValueError(f"Ancestor '{anc}' is not a directory")


def _verify_ancestors_containment(dest: Path, ws: Path) -> None:
    """Verify all intermediate ancestor directories exist, are directories,

    and do not link or resolve outside workspace. Fail-closed immediately.
    """
    ancestors = _get_intermediate_ancestors(dest, ws)
    for anc in ancestors:
        if not anc.exists():
            raise ValueError(f"Ancestor directory '{anc}' does not exist")
        if not anc.is_dir():
            raise ValueError(f"Ancestor '{anc}' exists but is not a directory")

        if _is_link_or_reparse(anc):
            try:
                res = anc.resolve()
            except (RuntimeError, OSError) as exc:
                raise ValueError(f"Cannot resolve ancestor link '{anc}': {exc}") from exc
            if not res.is_relative_to(ws) or res == ws:
                raise ValueError(
                    f"Agent configuration path '{anc}' is an external symlink pointing outside workspace: '{res}'"
                )

        try:
            res = anc.resolve()
        except (RuntimeError, OSError) as exc:
            raise ValueError(f"Cannot resolve ancestor directory '{anc}': {exc}") from exc
        if not res.is_relative_to(ws) or res == ws:
            raise ValueError(
                f"Agent configuration path '{anc}' resolves outside workspace root '{ws}': '{res}'"
            )


@dataclass(frozen=True)
class FileBackup:
    path: Path
    content: bytes
    sha256: str
    atime_ns: int
    mtime_ns: int
    mode: int
    workspace_root: Optional[Path] = None

    @classmethod
    def capture(
        cls,
        path: Path | str,
        workspace_root: Optional[Path | str] = None,
    ) -> "FileBackup":
        p = Path(path).resolve()
        st = p.stat()
        content = p.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        ws = Path(workspace_root).resolve() if workspace_root is not None else None
        return cls(
            path=p,
            content=content,
            sha256=digest,
            atime_ns=st.st_atime_ns,
            mtime_ns=st.st_mtime_ns,
            mode=st.st_mode,
            workspace_root=ws,
        )

    def restore(self, workspace_root: Optional[Path | str] = None) -> None:
        dest = self.path
        ws = Path(workspace_root or self.workspace_root or self.path.parent).resolve()

        if not dest.is_absolute():
            dest = ws / dest

        # Phase 1: Top-down ancestor preparation and verification (strictly before any write or mkdir)
        _prepare_and_verify_ancestors(dest, ws)
        _verify_workspace_containment(dest.parent, ws)

        # Phase 2: Prepare destination path
        if _is_link_or_reparse(dest):
            _remove_link_or_reparse(dest)
        elif dest.exists():
            try:
                os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass

        _verify_workspace_containment(dest, ws)

        # Phase 3: Create temporary file strictly inside verified dest.parent
        _verify_ancestors_containment(dest, ws)
        temp_dir = dest.parent
        tmp_file = tempfile.NamedTemporaryFile(dir=temp_dir, prefix=".restore_", delete=False)
        tmp_path = Path(tmp_file.name)
        try:
            tmp_file.write(self.content)
            tmp_file.flush()
            tmp_file.close()

            _verify_workspace_containment(tmp_path, ws)

            if _is_link_or_reparse(dest):
                _remove_link_or_reparse(dest)
            elif dest.exists():
                try:
                    os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
                except OSError:
                    pass

            # Phase 4: Pre-replace ancestor re-verification (Anti-TOCTOU)
            _verify_ancestors_containment(dest, ws)
            _verify_workspace_containment(dest, ws)
            _verify_workspace_containment(tmp_path, ws)
            os.replace(tmp_path, dest)
        finally:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass

        # Phase 5: Post-replace verification and attribute restoration
        if dest.is_symlink() or _is_link_or_reparse(dest):
            raise ValueError(f"Restored file {dest} is still a symlink or junction")

        restored_hash = hashlib.sha256(dest.read_bytes()).hexdigest()
        if restored_hash != self.sha256:
            raise ValueError(
                f"Restored file {dest} hash mismatch: expected {self.sha256}, got {restored_hash}"
            )

        try:
            os.utime(dest, ns=(self.atime_ns, self.mtime_ns))
        except OSError:
            pass
        try:
            os.chmod(dest, self.mode)
        except OSError:
            pass
        try:
            os.utime(dest, ns=(self.atime_ns, self.mtime_ns))
        except OSError:
            pass


class WrittenFiles(list):
    """List of written paths that also preserves backup metadata for unpack callers."""
    backups: dict[Path, FileBackup]
    ephemeral_files: list[Path]
    workspace: Optional[Path]

    def __init__(
        self,
        items: list[Path],
        backups: dict[Path, FileBackup],
        ephemeral_files: list[Path],
        workspace: Optional[Path] = None,
    ):
        super().__init__(items)
        self.backups = backups
        self.ephemeral_files = ephemeral_files
        self.workspace = workspace


class WorkspacePreparation(tuple):
    """Holds workspace state for cleanup. Unpacks as (written_files, created_dirs) for legacy callers."""
    written_files: list[Path]
    created_dirs: list[Path]
    backups: dict[Path, FileBackup]
    ephemeral_files: list[Path]
    workspace: Optional[Path]

    def __new__(
        cls,
        written_files: list[Path],
        created_dirs: list[Path],
        backups: dict[Path, FileBackup],
        ephemeral_files: list[Path],
        workspace: Optional[Path] = None,
    ) -> "WorkspacePreparation":
        written_wrapped = (
            written_files
            if isinstance(written_files, WrittenFiles)
            else WrittenFiles(written_files, backups, ephemeral_files, workspace)
        )
        instance = super().__new__(cls, (written_wrapped, created_dirs))
        instance.written_files = written_wrapped
        instance.created_dirs = created_dirs
        instance.backups = backups
        instance.ephemeral_files = ephemeral_files
        instance.workspace = workspace
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
            target_dir = ws / rel_dir
            resolved_dir = _verify_workspace_containment(target_dir, ws)
            if not target_dir.exists() and resolved_dir not in created_dirs:
                created_dirs.append(resolved_dir)

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
                except Exception:
                    continue
                p_codex = _verify_workspace_containment(ws / ".codex" / "agents" / f"{profile.name}.toml", ws)
                p_agy = _verify_workspace_containment(ws / ".agents" / "agents" / f"{profile.name}.md", ws)
                prospective_files.append(p_codex)
                prospective_files.append(p_agy)

        backups: dict[Path, FileBackup] = {}
        ephemeral_files: list[Path] = []
        for dest in prospective_files:
            if dest.exists() or dest.is_symlink():
                try:
                    backups[dest] = FileBackup.capture(dest, workspace_root=ws)
                except (PermissionError, OSError) as exc:
                    raise PermissionError(
                        f"Cannot backup pre-existing agent configuration {dest}: {exc}"
                    ) from exc
                except Exception as exc:
                    raise OSError(
                        f"Failed to backup pre-existing agent configuration {dest}: {exc}"
                    ) from exc
            else:
                ephemeral_files.append(dest)

        # Ensure writable only AFTER all existing files are securely backed up
        for dest in backups:
            try:
                os.chmod(dest, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass

        try:
            written = export_native_agents(ws, self.profiles_dir)
            resolved_written = [_verify_workspace_containment(p, ws) for p in written]
            for p in resolved_written:
                if p not in backups and p not in ephemeral_files:
                    ephemeral_files.append(p)
            return WorkspacePreparation(
                written_files=resolved_written,
                created_dirs=created_dirs,
                backups=backups,
                ephemeral_files=ephemeral_files,
                workspace=ws,
            )
        except Exception:
            partial_prep = WorkspacePreparation(
                written_files=[p.resolve() for p in prospective_files],
                created_dirs=created_dirs,
                backups=backups,
                ephemeral_files=ephemeral_files,
                workspace=ws,
            )
            self.cleanup_workspace_agents(partial_prep)
            raise

    @staticmethod
    def cleanup_workspace_agents(
        written_files: WorkspacePreparation | list[Path] | tuple[Any, ...],
        created_dirs: Optional[list[Path]] = None,
        workspace_root: Optional[Path | str] = None,
    ) -> None:
        """Remove ephemeral native agent definitions and restore backed-up files."""
        ws: Optional[Path] = Path(workspace_root).resolve() if workspace_root is not None else None
        if isinstance(written_files, WorkspacePreparation):
            backups = written_files.backups
            ephemeral_files = written_files.ephemeral_files
            dirs_to_clean = created_dirs if created_dirs is not None else written_files.created_dirs
            if ws is None:
                ws = written_files.workspace
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
            if ws is None and hasattr(written_files, "workspace"):
                ws = getattr(written_files, "workspace")
            elif ws is None and hasattr(target_files, "workspace"):
                ws = getattr(target_files, "workspace")
        elif hasattr(written_files, "backups") and hasattr(written_files, "ephemeral_files"):
            backups = getattr(written_files, "backups")
            ephemeral_files = getattr(written_files, "ephemeral_files")
            dirs_to_clean = created_dirs if created_dirs is not None else []
            if ws is None and hasattr(written_files, "workspace"):
                ws = getattr(written_files, "workspace")
        else:
            backups = {}
            ephemeral_files = list(written_files) if written_files else []
            dirs_to_clean = created_dirs if created_dirs is not None else []

        # 1. Unlink ephemeral files only (without .resolve() so symlink file itself is removed, not external target)
        for f in ephemeral_files:
            try:
                p = Path(f)
                if _is_link_or_reparse(p):
                    _remove_link_or_reparse(p)
                elif p.exists():
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
                backup.restore(workspace_root=ws)
            except Exception as e:
                restore_errors.append(e)

        # 3. Remove only empty created directories
        for d in sorted(dirs_to_clean, key=lambda p: len(p.parts), reverse=True):
            try:
                p = Path(d)
                if _is_link_or_reparse(p):
                    _remove_link_or_reparse(p)
                elif p.exists() and not any(p.iterdir()):
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

