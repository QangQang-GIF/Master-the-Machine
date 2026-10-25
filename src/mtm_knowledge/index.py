"""Safe, deterministic, incremental source map with revision & stat invalidation.

No embeddings, secrets ingestion, source-content persistence, or external services.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go",
    ".rs", ".cs", ".cpp", ".c", ".h", ".rb", ".php", ".cbl", ".cob",
    ".sql", ".yml", ".yaml", ".toml", ".md", ".json",
}
_IGNORE_DIRS = {
    ".git", ".venv", "venv", "node_modules", "dist", "build", "coverage",
    "__pycache__", ".next", ".cache", ".tox", ".pytest_cache",
    ".codex", ".agents", ".mtm_index_cache",
}
_SENSITIVE = re.compile(
    r"(?:^\.env(?:\.|$)|secret|credential|private[_-]?key|\.pem$|\.p12$|\.key$|password)",
    re.I,
)
_SYMBOL = re.compile(
    r"^\s*(?:def|class|async def|function|export function|interface|type|public class|func)\s+([A-Za-z_][A-Za-z0-9_]*)",
    re.M,
)
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")

# Process-local incremental cache keyed by resolved repository path
_INDEX_CACHE: dict[str, dict[str, Any]] = {}


def clear_index_cache() -> None:
    """Clear the in-memory incremental knowledge index cache."""
    _INDEX_CACHE.clear()


def _revision(repo: Path) -> str:
    try:
        run = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if run.returncode == 0:
            return run.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unversioned"


def _cache_file_for_repo(root: Path, cache_dir: Path) -> Path:
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:16]
    return cache_dir / f"index_{digest}.json"


def _load_cached_index(root: Path, cache_dir: Path | None) -> dict[str, Any] | None:
    key = str(root)
    if cache_dir is not None:
        cfile = _cache_file_for_repo(root, cache_dir)
        if cfile.is_file():
            try:
                data = json.loads(cfile.read_text(encoding="utf-8"))
                if isinstance(data, dict) and isinstance(data.get("files"), list):
                    return data
            except (OSError, ValueError):
                pass
    return _INDEX_CACHE.get(key)


def _save_cached_index(root: Path, index_data: dict[str, Any], cache_dir: Path | None) -> None:
    key = str(root)
    _INDEX_CACHE[key] = index_data
    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            cfile = _cache_file_for_repo(root, cache_dir)
            tmp = cfile.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(index_data), encoding="utf-8")
            os.replace(tmp, cfile)
        except OSError:
            pass


def build_index(
    repository: Path | str,
    max_files: int = 3000,
    max_file_bytes: int = 262144,
    cache_dir: Path | str | None = None,
) -> dict[str, Any]:
    """Incremental metadata-only index with revision and per-file stat invalidation.

    Source contents are never persisted in memory or on disk; only relative path,
    extracted symbol names, byte size, and mtime_ns are stored.
    """
    root = Path(repository).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)

    env_cache = os.environ.get("MTM_KNOWLEDGE_CACHE_DIR")
    resolved_cache_dir = (
        Path(cache_dir).resolve()
        if cache_dir is not None
        else (Path(env_cache).resolve() if env_cache else None)
    )

    rev = _revision(root)
    prev = _load_cached_index(root, resolved_cache_dir)
    prev_files_map: dict[str, dict[str, Any]] = {}
    if prev and isinstance(prev.get("files"), list):
        for item in prev["files"]:
            if isinstance(item, dict) and "path" in item:
                prev_files_map[str(item["path"])] = item

    entries: list[dict[str, Any]] = []
    reused_files = 0
    reindexed_files = 0

    for path in sorted(root.rglob("*")):
        if len(entries) >= max_files:
            break
        try:
            rel = path.relative_to(root)
            if any(p in _IGNORE_DIRS or _SENSITIVE.search(p) for p in rel.parts):
                continue
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _EXTENSIONS:
                continue
            st = path.stat()
            if st.st_size > max_file_bytes:
                continue
            rel_posix = rel.as_posix()
            cached_entry = prev_files_map.get(rel_posix)
            if (
                cached_entry is not None
                and cached_entry.get("mtime_ns") == st.st_mtime_ns
                and cached_entry.get("file_bytes") == st.st_size
                and isinstance(cached_entry.get("symbols"), list)
            ):
                entries.append(
                    {
                        "path": rel_posix,
                        "symbols": list(cached_entry["symbols"]),
                        "size": int(cached_entry.get("size", st.st_size)),
                        "mtime_ns": st.st_mtime_ns,
                        "file_bytes": st.st_size,
                    }
                )
                reused_files += 1
                continue

            data = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError, ValueError):
            continue

        symbols = list(dict.fromkeys(_SYMBOL.findall(data)))[:80]
        entries.append(
            {
                "path": rel_posix,
                "symbols": symbols,
                "size": len(data),
                "mtime_ns": st.st_mtime_ns,
                "file_bytes": st.st_size,
            }
        )
        reindexed_files += 1

    prev_count = prev.get("indexed_count", -1) if prev else -1
    prev_rev = prev.get("revision") if prev else None
    cache_hit = bool(
        prev is not None
        and prev_rev == rev
        and reindexed_files == 0
        and reused_files == len(entries)
        and prev_count == len(entries)
    )

    result = {
        "revision": rev,
        "files": entries,
        "indexed_count": len(entries),
        "cache_hit": cache_hit,
        "reused_files": reused_files,
        "reindexed_files": reindexed_files,
    }
    _save_cached_index(root, result, resolved_cache_dir)
    return result


def retrieve_knowledge(index: dict[str, Any], query: str, limit: int = 10, max_chars: int = 3000) -> str:
    """Return a tiny task-specific symbol/path map, never source file contents."""
    words = {w.lower() for w in _WORDS.findall(query)}
    scored = []
    for entry in index.get("files", []):
        path = str(entry["path"])
        tokens = {w.lower() for w in _WORDS.findall(path.replace("/", " "))}
        symbols = {w.lower() for s in entry.get("symbols", []) for w in _WORDS.findall(s)}
        overlap = len(words & tokens) * 3 + len(words & symbols) * 5
        # Tests are useful to a verifier even when lexical overlap is sparse.
        if "/test" in "/" + path or path.startswith("test"):
            overlap += 1
        scored.append((overlap, path, entry))
    ranked = sorted(scored, key=lambda x: (-x[0], x[1]))
    chosen = [x[2] for x in ranked if x[0] > 0][:max(0, limit)]
    lines = [
        f"Repository revision: {index.get('revision', 'unknown')}",
        "Source map only; verify files and test commands before changing code.",
    ]
    for entry in chosen:
        lines.append(f"- {entry['path']} :: {', '.join(entry.get('symbols', [])[:12])}")
    return "\n".join(lines)[:max_chars]
