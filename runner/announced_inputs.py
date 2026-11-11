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
MAX_BINARY_BYTES = 50 * 1024 * 1024
# Only paths that could leak secrets, overwrite answers or run on open are refused. Any other GM-announced
# file is accepted whatever its item prefix, folder depth or extension (39PSWT5C: CR-08 announced
# challenge/CR-05-seed.js, the strict "<key>-*" rule raised after START_WORK and the item was never worked).
_PROTECTED_SUFFIXES = (".env", ".secret", ".pem", ".key", ".p12", ".pfx", ".exe", ".dll", ".bat", ".cmd", ".ps1", ".scr")
_PROTECTED_NAMES = {"exam-info.json", "id_rsa", "id_ed25519"}
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
    """Safety checks only: normalized relative path, nothing hidden, secret-like, executable or under answers/."""
    if not isinstance(raw, str) or not raw or "\\" in raw or "\x00" in raw:
        raise AnnouncedInputError("Announced input path is invalid.")
    path = PurePosixPath(raw)
    if path.is_absolute() or path.as_posix() != raw or any(p in ("", ".", "..") for p in path.parts):
        raise AnnouncedInputError("Announced input path must be a normalized relative path.")
    if path.parts[0] == "answers":
        raise AnnouncedInputError("Announced input path must not point into answers/.")
    name = path.parts[-1]
    if any(part.startswith(".") for part in path.parts) or name.lower() in _PROTECTED_NAMES \
            or name.lower().endswith(_PROTECTED_SUFFIXES):
        raise AnnouncedInputError("Announced input path is protected.")
    return path.as_posix()


def _text(value: bytes, path: str) -> str:
    if len(value) > MAX_INPUT_BYTES:
        raise AnnouncedInputError(f"Announced input {path!r} exceeds the 10 MiB limit.")
    try:
        return value.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise AnnouncedInputError(f"Announced input {path!r} is not valid UTF-8 text.") from exc


def _content(value: bytes, path: str, binary: bool) -> str | bytes:
    if binary:
        try:
            return _text(value, path)
        except AnnouncedInputError:
            if len(value) > MAX_BINARY_BYTES:
                raise AnnouncedInputError(f"Announced input {path!r} exceeds the 50 MiB limit.")
            return value
    return _text(value, path)


def _local_tracked_file(checkout: Path, match: Path, folder: str, relative: str,
                        binary: bool = False) -> str | bytes | None:
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
    if byte_count > (MAX_BINARY_BYTES if binary else MAX_INPUT_BYTES):
        raise AnnouncedInputError(f"Announced input {relative!r} is too large.")
    blob = _run_git(checkout, ["show", f"HEAD:{full_relative}"])
    return _content(blob, relative, binary)


def _remote_file(checkout: Path, folder: str, relative: str, binary: bool = False) -> str | bytes | None:
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
    if byte_count > (MAX_BINARY_BYTES if binary else MAX_INPUT_BYTES):
        raise AnnouncedInputError(f"Announced input {relative!r} is too large.")
    return _content(_run_git(checkout, ["show", spec]), relative, binary)


def resolve_announced_files(
    checkout_root: str | Path,
    match_root: str | Path,
    assigned_branch: str,
    backlog_key: str,
    announced_paths: Sequence[str],
    delivered_files: Mapping[str, str] | None,
    *,
    missing_paths: list[str] | None = None,
    problems: list[dict] | None = None,
    binary_files: dict[str, bytes] | None = None,
) -> dict[str, str]:
    """Return text content for announced inputs, preferring Start Work payloads.

    ``announced_paths`` are match-root-relative filenames supplied by public metadata; ``delivered_files``
    maps filenames to Start Work content. A single shallow fetch is performed only for inputs that are
    neither delivered nor tracked locally.

    Strict mode (``problems`` is None): any unsafe path, unannounced delivery, non-text file or absence
    (unless ``missing_paths`` is given) raises.  Fail-open mode (``problems`` is a list): the item has
    already been started at the Game Master, so one odd input must never block it. Each refused or
    unreadable file is appended to ``problems`` as {"path", "reason"} and skipped; delivered files the
    metadata did not list are accepted (the GM is authoritative); non-UTF-8 files go to ``binary_files``.
    """
    lenient = problems is not None

    def problem(path: object, reason: str) -> None:
        if not lenient:
            raise AnnouncedInputError(reason)
        problems.append({"path": str(path)[:300], "reason": reason[:300]})

    checkout = Path(checkout_root).resolve()
    match = Path(match_root).resolve()
    if not checkout.is_dir() or not match.is_dir():
        raise AnnouncedInputError("Assigned checkout or match root is unavailable.")
    folder = _repo_folder(checkout, match)
    if not _KEY_RE.fullmatch(backlog_key) and not lenient:
        raise AnnouncedInputError("Backlog key is invalid.")
    if not isinstance(assigned_branch, str) or not assigned_branch or assigned_branch.startswith("-"):
        raise AnnouncedInputError("Assigned branch is invalid.")
    _run_git(checkout, ["check-ref-format", "--branch", assigned_branch])

    paths: list[str] = []
    for raw in announced_paths:
        try:
            path = _validate_path(raw, backlog_key)
        except AnnouncedInputError as exc:
            problem(raw, str(exc))
            continue
        if path not in paths:
            paths.append(path)
    delivered = delivered_files or {}
    if not isinstance(delivered, Mapping):
        problem("<startWork.files>", "Start Work files must be a path-to-text mapping.")
        delivered = {}
    supplied: dict[str, str] = {}
    for raw_path, content in delivered.items():
        try:
            path = _validate_path(raw_path, backlog_key)
        except AnnouncedInputError as exc:
            problem(raw_path, str(exc))
            continue
        if path not in paths:
            if not lenient:
                raise AnnouncedInputError(f"Start Work supplied unannounced input {path!r}.")
            paths.append(path)
        if not isinstance(content, str):
            problem(path, f"Start Work input {path!r} is not text; reading it from the branch instead.")
            continue
        try:
            supplied[path] = _text(content.encode("utf-8"), path)
        except AnnouncedInputError as exc:
            problem(path, str(exc))

    binary = binary_files is not None and lenient
    result: dict[str, str] = dict(supplied)

    def keep(path: str, content: str | bytes) -> None:
        if isinstance(content, bytes):
            binary_files[path] = content
        else:
            result[path] = content

    missing: list[str] = []
    for path in paths:
        if path in result:
            continue
        try:
            local = _local_tracked_file(checkout, match, folder, path, binary)
        except AnnouncedInputError as exc:
            problem(path, str(exc))
            continue
        if local is None:
            missing.append(path)
        else:
            keep(path, local)
    if missing:
        try:
            _run_git(checkout, ["fetch", "--no-tags", "origin", assigned_branch])
        except AnnouncedInputError as exc:
            if not lenient:
                raise
            for path in missing:
                if missing_paths is not None:
                    missing_paths.append(path)
                problem(path, f"{exc} The file stays unavailable; work from the task text.")
            return result
        for path in missing:
            try:
                content = _remote_file(checkout, folder, path, binary)
            except AnnouncedInputError as exc:
                problem(path, str(exc))
                continue
            if content is None:
                if missing_paths is not None:
                    missing_paths.append(path)
                    continue
                problem(path, f"Announced input {path!r} is missing from the assigned branch.")
                continue
            keep(path, content)
    return result
