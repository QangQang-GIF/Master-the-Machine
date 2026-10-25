"""Safe, deterministic source map. No embeddings, secrets ingestion, or external services."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".kt", ".go",
               ".rs", ".cs", ".cpp", ".c", ".h", ".rb", ".php", ".cbl", ".cob",
               ".sql", ".yml", ".yaml", ".toml", ".md", ".json"}
_IGNORE_DIRS = {".git", ".venv", "venv", "node_modules", "dist", "build", "coverage",
                "__pycache__", ".next", ".cache", ".tox", ".pytest_cache"}
_SENSITIVE = re.compile(r"(?:^\.env(?:\.|$)|secret|credential|private[_-]?key|\.pem$|\.p12$|\.key$|password)", re.I)
_SYMBOL = re.compile(r"^\s*(?:def|class|async def|function|export function|interface|type|public class|func)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)
_WORDS = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{2,}")


def _revision(repo: Path) -> str:
    try:
        run = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        if run.returncode == 0:
            return run.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return "unversioned"


def build_index(repository: Path | str, max_files: int = 3000, max_file_bytes: int = 262144) -> dict[str, Any]:
    """In-memory metadata-only index; files are never persisted outside the caller."""
    root = Path(repository).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if len(entries) >= max_files:
            break
        try:
            rel = path.relative_to(root)
            if any(p in _IGNORE_DIRS or _SENSITIVE.search(p) for p in rel.parts):
                continue
            if path.is_symlink() or not path.is_file() or path.suffix.lower() not in _EXTENSIONS:
                continue
            if path.stat().st_size > max_file_bytes:
                continue
            data = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeError, ValueError):
            continue
        symbols = list(dict.fromkeys(_SYMBOL.findall(data)))[:80]
        entries.append({"path": rel.as_posix(), "symbols": symbols, "size": len(data)})
    return {"revision": _revision(root), "files": entries, "indexed_count": len(entries)}


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
    lines = [f"Repository revision: {index.get('revision', 'unknown')}",
             "Source map only; verify files and test commands before changing code."]
    for entry in chosen:
        lines.append(f"- {entry['path']} :: {', '.join(entry.get('symbols', [])[:12])}")
    return "\n".join(lines)[:max_chars]
