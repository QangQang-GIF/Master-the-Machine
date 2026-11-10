"""Collect EVERY entry of a production-log search, not just the newest 20.

The company ``search_logs`` tool returns at most ~20 newest matching entries per call together with the
``total`` number of matches, and asks the caller to narrow the query. Counting questions (first bad entry in
log order, distinct transactions, sums) need the whole result set, which an LLM worker cannot page through
quickly. This collector narrows deterministically by timestamp prefix (``2025-10-15T08``, then minutes,
seconds, ...) until every partition is complete (``returned == total``), deduplicates the entries, and
reports whether the union reached the official total. Only the same public tool is called.
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

SearchFn = Callable[[str, str], Dict[str, Any]]


def _next_chars(prefix: str) -> List[str]:
    """Children of a timestamp prefix following YYYY-MM-DDTHH:MM:SS[.fff]Z."""
    n = len(prefix)
    if n in (4, 7):
        return ["-"]
    if n == 10:
        return ["T"]
    if n in (13, 16):
        return [":"]
    if n == 19:
        return [".", "Z"]
    if n > 19:
        if prefix.endswith("Z"):
            return []
        if "." in prefix:
            frac = prefix.split(".", 1)[1]
            return [*"0123456789", "Z"] if frac else list("0123456789")
        return []
    return list("0123456789")


def _key(entry: Dict[str, Any]) -> str:
    ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
    if "seq" in ctx:
        return f"seq:{ctx['seq']}"
    return "raw:" + json.dumps(entry, sort_keys=True, ensure_ascii=False)


def collect_all(search: SearchFn, query: str = "", service: str = "", *, max_calls: int = 600,
                time_budget_seconds: float = 240.0) -> Dict[str, Any]:
    """Return {"total", "unique", "complete", "calls", "entries", "incompletePartitions"}."""
    started = time.monotonic()
    calls = 0
    entries: Dict[str, Dict[str, Any]] = {}
    incomplete: List[str] = []

    def run(q: str) -> Dict[str, Any]:
        nonlocal calls
        calls += 1
        res = search(q, service)
        if not isinstance(res, dict):
            raise RuntimeError(f"Unexpected search_logs result: {str(res)[:200]}")
        return res

    def add(rows: Any) -> None:
        for entry in rows or []:
            if isinstance(entry, dict):
                entries.setdefault(_key(entry), entry)

    def budget_left() -> bool:
        return calls < max_calls and time.monotonic() - started < time_budget_seconds

    root = run(query.strip())
    total = int(root.get("total", 0) or 0)
    add(root.get("entries"))

    def descend(prefix: str) -> None:
        if not budget_left():
            incomplete.append(prefix)
            return
        res = run(f"{query} {prefix}".strip())
        node_total = int(res.get("total", 0) or 0)
        rows = res.get("entries") or []
        add(rows)
        if node_total == 0 or node_total <= len(rows):
            return
        children = _next_chars(prefix)
        if not children:
            incomplete.append(prefix)          # identical timestamps beyond the page size
            return
        for ch in children:
            descend(prefix + ch)
            if len(entries) >= total:
                return

    if total > len(root.get("entries") or []):
        years = sorted({str(e.get("ts", ""))[:4] for e in root.get("entries") or [] if str(e.get("ts", ""))[:4].isdigit()},
                       reverse=True)
        newest = int(years[0]) if years else time.gmtime().tm_year
        for year in range(newest, newest - 6, -1):
            if len(entries) >= total:
                break
            descend(f"{year}-")

    def order(entry: Dict[str, Any]):
        ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        seq = ctx.get("seq")
        return (0, seq, "") if isinstance(seq, (int, float)) else (1, 0, str(entry.get("ts", "")))

    ordered = sorted(entries.values(), key=order)
    return {"query": query, "service": service, "total": total, "unique": len(ordered),
            "complete": len(ordered) >= total and not incomplete, "calls": calls,
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "incompletePartitions": incomplete[:50], "entries": ordered}
