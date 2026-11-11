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


_SEPARATORS = {4: "-", 7: "-", 10: "T", 13: ":", 16: ":"}
_DIGITS = "0123456789"


def _valid_chars(prefix: str) -> str:
    """Characters that can follow a timestamp prefix YYYY-MM-DDTHH:MM:SS[.fff]Z and still form a real date.

    39PSWT5C: the old collector tried every digit at every position ("2025-17-...", "2025-3...") and spent
    its 600-call budget on impossible months, so PI-02 recovered 2100 of 4752 entries and PI-03 846 of 924."""
    n = len(prefix)
    if n == 0:
        return "12"
    if n in (1, 2, 3):
        return _DIGITS
    if n == 5:
        return "01"
    if n == 6:
        return "123456789" if prefix[5] == "0" else "012"
    if n == 8:
        return "0123"
    if n == 9:
        return "123456789" if prefix[8] == "0" else ("01" if prefix[8] == "3" else _DIGITS)
    if n == 11:
        return "012"
    if n == 12:
        return "0123" if prefix[11] == "2" else _DIGITS
    if n in (14, 17):
        return "012345"
    if n in (15, 18):
        return _DIGITS
    if n == 19:
        return ".Z"
    if prefix.endswith("Z"):
        return ""
    frac = prefix.split(".", 1)[1] if "." in prefix else ""
    return _DIGITS + "Z" if frac else _DIGITS


def _with_separators(prefix: str) -> str:
    while len(prefix) in _SEPARATORS:
        prefix += _SEPARATORS[len(prefix)]
    return prefix


def _children(prefix: str) -> List[str]:
    return [_with_separators(prefix + ch) for ch in _valid_chars(prefix)]


def _key(entry: Dict[str, Any]) -> str:
    ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
    if "seq" in ctx:
        return f"seq:{ctx['seq']}"
    return "raw:" + json.dumps(entry, sort_keys=True, ensure_ascii=False)


def collect_all(search: SearchFn, query: str = "", service: str = "", *, max_calls: int = 2000,
                time_budget_seconds: float = 420.0, workers: int = 6) -> Dict[str, Any]:
    """Return {"total", "unique", "complete", "calls", "entries", "incompletePartitions"}.

    Breadth-first over valid timestamp prefixes, `workers` searches at a time. A partition is complete when it
    returns all of its matches; the collection is complete when the union reaches the root query's total."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    started = time.monotonic()
    lock = threading.Lock()
    state = {"calls": 0}
    entries: Dict[str, Dict[str, Any]] = {}
    incomplete: List[str] = []

    def run(q: str) -> Dict[str, Any]:
        with lock:
            state["calls"] += 1
        res = search(q, service)
        if not isinstance(res, dict):
            raise RuntimeError(f"Unexpected search_logs result: {str(res)[:200]}")
        return res

    def add(rows: Any) -> None:
        with lock:
            for entry in rows or []:
                if isinstance(entry, dict):
                    entries.setdefault(_key(entry), entry)

    def budget_left(extra: int = 0) -> bool:
        return state["calls"] + extra <= max_calls and time.monotonic() - started < time_budget_seconds

    root = run(query.strip())
    total = int(root.get("total", 0) or 0)
    add(root.get("entries"))

    def visit(prefix: str) -> List[str]:
        try:
            res = run(f"{query} {prefix}".strip())
        except Exception:
            return [prefix]                        # retried at the next level, never silently dropped
        node_total = int(res.get("total", 0) or 0)
        rows = res.get("entries") or []
        add(rows)
        if node_total == 0 or node_total <= len(rows):
            return []
        children = _children(prefix)
        stamps = [str(e.get("ts", "")) for e in rows if isinstance(e, dict)]
        if stamps and all(stamps) and stamps == sorted(stamps, reverse=True):
            # The page holds the newest matches, so every match newer than its oldest entry is already in hand:
            # only children that can hold older (or equal) timestamps need a search.
            oldest = stamps[-1]
            children = [c for c in children if c <= oldest[:len(c)]]
        if not children:
            with lock:
                incomplete.append(prefix)          # identical timestamps beyond the page size
        return children

    if total > len(root.get("entries") or []):
        sampled = sorted({str(e.get("ts", ""))[:4] for e in root.get("entries") or []
                          if str(e.get("ts", ""))[:4].isdigit()}, reverse=True)
        newest = int(sampled[0]) if sampled else time.gmtime().tm_year
        year_rounds = [[f"{y}-" for y in sampled] or [f"{newest}-"],
                       [f"{y}-" for y in range(newest + 1, newest - 7, -1) if str(y) not in sampled]]
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            for level in year_rounds:
                frontier = list(level)
                while frontier and len(entries) < total:
                    if not budget_left(len(frontier)):
                        allowed = max(0, max_calls - state["calls"]) if time.monotonic() - started < time_budget_seconds else 0
                        incomplete.extend(frontier[allowed:])
                        frontier = frontier[:allowed]
                        if not frontier:
                            break
                    nxt: List[str] = []
                    for kids in pool.map(visit, frontier):
                        nxt.extend(kids)
                    frontier = nxt
                if len(entries) >= total:
                    break

    def order(entry: Dict[str, Any]):
        ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        seq = ctx.get("seq")
        return (0, seq, "") if isinstance(seq, (int, float)) else (1, 0, str(entry.get("ts", "")))

    ordered = sorted(entries.values(), key=order)
    complete = len(ordered) >= total
    return {"query": query, "service": service, "total": total, "unique": len(ordered),
            "complete": complete, "calls": state["calls"],
            "elapsedSeconds": round(time.monotonic() - started, 1),
            "incompletePartitions": [] if complete else sorted(set(incomplete))[:50],
            "advice": None if complete else ("Incomplete: narrow the query (service, release, message, ids) and "
                                             "collect each part; do not compute answers from this file."),
            "entries": ordered}
