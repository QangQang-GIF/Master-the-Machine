"""Resolve only publicly announced task input files.

This module intentionally does not check out a fetched revision. It reads only
the exact paths declared by the public task metadata (or delivered by Start
Work) from the assigned branch.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Mapping, Sequence

MAX_INPUT_BYTES = 10 * 1024 * 1024
_ALLOWED_EXTENSIONS = {".md", ".json", ".csv", ".ts", ".js", ".mjs", ".py", ".sh", ".txt", ".yaml", ".yml"}
_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")


class AnnouncedInputError(ValueError):
    """A declared public input could not be safely resolved."""


def _run_git(root: Path, args: list[str], *, timeout: int = 30) -> bytes:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AnnouncedInputError("Git could not resolve the announced input (operation failed or timed out).") from exc
    if result.returncode:
        # Never expose stderr: remote URLs and Git configuration can contain secrets.
        raise AnnouncedInputError("Git could not resolve the announced input from the assigned branch.")
    return result.stdout


def _repo_folder(checkout: Path, match: Path) -> str:
    try:
        relative = match.relative_to(checkout)
    except ValueError as exc:
        raise AnnouncedInputError("Match root must be inside the assigned checkout.") from exc
    if not relative.parts or any(part in (".", "..") for part in relative.parts):
        raise AnnouncedInputError("Match root must identify the repository's assigned folder.")
    return PurePosixPath(*relative.parts).as_posix()


def _validate_path(raw: str, key: str) -> str:
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise AnnouncedInputError("Announced input path is invalid.")
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or any(p in ("", ".", "..") for p in path.parts):
        raise AnnouncedInputError("Announced input path must be a normalized relative path.")
    # Inputs are deliberately limited to this task's public challenge documents.
    if len(path.parts) != 2 or path.parts[0] != "challenge":
        raise AnnouncedInputError("Only files under challenge/ may be announced as task inputs.")
    name = path.parts[1]
    if name in {".", ".."} or name.startswith(".") or name.lower().endswith((".env", ".secret")):
        raise AnnouncedInputError("Announced input path is protected.")
    stem = name.rsplit(".", 1)[0] if "." in name else ""
    extension = f".{name.rsplit('.', 1)[1].lower()}" if "." in name else ""
    if extension not in _ALLOWED_EXTENSIONS:
        raise AnnouncedInputError("Announced input extension is not allowed.")
    if stem != key and not stem.startswith(f"{key}-"):
        raise AnnouncedInputError("Announced input must belong to the current backlog item.")
    return path.as_posix()


def _text(value: bytes, path: str) -> str:
    if len(value) > MAX_INPUT_BYTES:
        raise AnnouncedInputError(f"Announced input {path!r} exceeds the 10 MiB limit.")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AnnouncedInputError(f"Announced input {path!r} is not valid UTF-8 text.") from exc


def _local_tracked_file(checkout: Path, match: Path, folder: str, relative: str) -> str | None:
    full_relative = f"{folder}/{relative}"
    # Read the exact tracked path from HEAD only. No filesystem traversal or globbing.
    entry = _run_git(checkout, ["ls-tree", "-z", "HEAD", "--", full_relative])
    if not entry:
        return None
    try:
        metadata, name = entry.rstrip(b"\0").split(b"\t", 1)
        mode, kind, _oid = metadata.decode("ascii").split(" ")
    except (ValueError, UnicodeDecodeError) as exc:
        raise AnnouncedInputError(f"Announced input {relative!r} has an invalid Git tree entry.") from exc
    if name.decode("utf-8", "strict") != full_relative or kind != "blob" or mode not in {"100644", "100755"}:
        raise AnnouncedInputError(f"Announced input {relative!r} is not a regular file.")
    size = _run_git(checkout, ["cat-file", "-s", f"HEAD:{full_relative}"]).strip()
    try:
        byte_count = int(size)
    except ValueError as exc:
        raise AnnouncedInputError(f"Announced input {relative!r} has invalid Git metadata.") from exc
    if byte_count > MAX_INPUT_BYTES:
        raise AnnouncedInputError(f"Announced input {relative!r} exceeds the 10 MiB limit.")
    blob = _run_git(checkout, ["show", f"HEAD:{full_relative}"])
    return _text(blob, relative)


def _remote_file(checkout: Path, folder: str, relative: str) -> str | None:
    spec = f"FETCH_HEAD:{folder}/{relative}"
    entry = _run_git(checkout, ["ls-tree", "-z", "FETCH_HEAD", "--", f"{folder}/{relative}"])
    if not entry:
        return None
    try:
        metadata, name = entry.rstrip(b"\0").split(b"\t", 1)
        mode, kind, _oid = metadata.decode("ascii").split(" ")
    except (ValueError, UnicodeDecodeError) as exc:
        raise AnnouncedInputError(f"Announced input {relative!r} has an invalid Git tree entry.") from exc
    expected = f"{folder}/{relative}".encode("utf-8")
    if name != expected or kind != "blob" or mode not in {"100644", "100755"}:
        raise AnnouncedInputError(f"Announced input {relative!r} is not a regular file.")
    size = _run_git(checkout, ["cat-file", "-s", spec]).strip()
    try:
        byte_count = int(size)
    except ValueError as exc:
        raise AnnouncedInputError(f"Announced input {relative!r} has invalid Git metadata.") from exc
    if byte_count > MAX_INPUT_BYTES:
        raise AnnouncedInputError(f"Announced input {relative!r} exceeds the 10 MiB limit.")
    return _text(_run_git(checkout, ["show", spec]), relative)


def resolve_announced_files(
    checkout_root: str | Path,
    match_root: str | Path,
    assigned_branch: str,
    backlog_key: str,
    announced_paths: Sequence[str],
    delivered_files: Mapping[str, str] | None,
    *,
    missing_paths: list[str] | None = None,
) -> dict[str, str]:
    """Return content for announced inputs, preferring Start Work payloads.

    ``announced_paths`` are match-root-relative filenames supplied by public
    metadata. ``delivered_files`` maps those same filenames to Start Work
    response content. A single shallow fetch is performed only for missing
    local inputs not already supplied by the API. When ``missing_paths`` is
    provided, genuinely absent public files are appended there and omitted from
    the result; without it, absence remains a strict error.
    """
    checkout = Path(checkout_root).resolve()
    match = Path(match_root).resolve()
    if not checkout.is_dir() or not match.is_dir():
        raise AnnouncedInputError("Assigned checkout or match root is unavailable.")
    folder = _repo_folder(checkout, match)
    if not _KEY_RE.fullmatch(backlog_key):
        raise AnnouncedInputError("Backlog key is invalid.")
    if not isinstance(assigned_branch, str) or not assigned_branch or assigned_branch.startswith("-"):
        raise AnnouncedInputError("Assigned branch is invalid.")
    _run_git(checkout, ["check-ref-format", "--branch", assigned_branch])

    paths = list(dict.fromkeys(_validate_path(p, backlog_key) for p in announced_paths))
    delivered = delivered_files or {}
    if not isinstance(delivered, Mapping):
        raise AnnouncedInputError("Start Work files must be a path-to-text mapping.")
    supplied: dict[str, str] = {}
    for raw_path, content in delivered.items():
        path = _validate_path(raw_path, backlog_key)
        if path not in paths:
            raise AnnouncedInputError(f"Start Work supplied unannounced input {path!r}.")
        if not isinstance(content, str):
            raise AnnouncedInputError(f"Start Work input {path!r} is not text.")
        supplied[path] = _text(content.encode("utf-8"), path)

    result = dict(supplied)
    missing: list[str] = []
    for path in paths:
        if path in result:
            continue
        local = _local_tracked_file(checkout, match, folder, path)
        if local is None:
            missing.append(path)
        else:
            result[path] = local
    if missing:
        _run_git(checkout, ["fetch", "--no-tags", "origin", assigned_branch])
        for path in missing:
            content = _remote_file(checkout, folder, path)
            if content is None:
                if missing_paths is not None:
                    missing_paths.append(path)
                    continue
                raise AnnouncedInputError(f"Announced input {path!r} is missing from the assigned branch.")
            result[path] = content
    return result
