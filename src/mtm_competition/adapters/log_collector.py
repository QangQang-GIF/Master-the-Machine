"""Collect EVERY entry of a production-log search, not just the newest 20.

The company ``search_logs`` tool returns at most ~20 newest matching entries per call together with the
``total`` number of matches ("every word must appear in the entry"), and asks the caller to narrow the query.
Counting questions (first bad entry, distinct transactions, sums) need the whole result set.

Two search semantics have to be handled, and the collector detects which one the server uses:

* **word match** (observed on the real server, 39PSWT5C/5FW1TZ38/XD7IARR1): the query and the entry are split
  into alphanumeric words and every query word must be one of the entry's words. ``2025-1`` then means the
  words ``2025`` and ``1``; a timestamp *prefix* narrows nothing. The previous collectors were built on prefixes:
  the first one wasted its budget on impossible months, the second stopped after 20 calls with 40 of 755
  entries, and PI-02 scored 0 in XD7IARR1.
  The timestamp ``2025-10-17T08:17:14.421Z`` holds the words 2025, 10, 17T08, 17, 14, 421Z; the day-hour word
  ``DDTHH`` is unique per entry, so ``query + DDTHH`` over its 744 values covers the whole result (scanned from
  the newest entry backwards, stopping once the total is reached); a crowded hour is split by minute, second
  and millisecond words. If the day-hour word does not work, minute/second words (00..59) are used.
* **substring match**: timestamp prefixes (``2025-10-1``, ``2025-10-15T0`` ...) partition the result.

A partition is complete when the entries collected for it (checked locally with the same word rule) reach the
server's ``total`` for it; the collection is complete when the union reaches the query's ``total``.
Only the same public tool is called.
"""
from __future__ import annotations

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Sequence

SearchFn = Callable[[str, str], Dict[str, Any]]

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_SEPARATORS = {4: "-", 7: "-", 10: "T", 13: ":", 16: ":"}
_DIGITS = "0123456789"
_TS_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})")
SIXTY = [f"{i:02d}" for i in range(60)]


def words(text: str) -> List[str]:
    return [w.lower() for w in _WORD_RE.findall(text or "")]


def entry_words(entry: Dict[str, Any]) -> frozenset:
    return frozenset(words(json.dumps(entry, ensure_ascii=False)))


def _key(entry: Dict[str, Any]) -> str:
    ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
    if "seq" in ctx:
        return f"seq:{ctx['seq']}"
    for field in ("id", "eventId", "logId"):
        if field in entry:
            return f"{field}:{entry[field]}"
    return "raw:" + json.dumps(entry, sort_keys=True, ensure_ascii=False)


# ---- substring semantics: valid timestamp prefixes -----------------------------------------------------------
def _valid_chars(prefix: str) -> str:
    """Characters that can follow a timestamp prefix YYYY-MM-DDTHH:MM:SS[.fff]Z and still form a real date."""
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


def _with_separators(prefix: str, seps: Optional[Dict[int, str]] = None) -> str:
    seps = seps or _SEPARATORS
    while len(prefix) in seps:
        prefix += seps[len(prefix)]
    return prefix


def _children(prefix: str, seps: Optional[Dict[int, str]] = None) -> List[str]:
    return [_with_separators(prefix + ch, seps) for ch in _valid_chars(prefix)]


class _Collector:
    def __init__(self, search: SearchFn, query: str, service: str, max_calls: int, time_budget: float,
                 workers: int):
        self.search, self.query, self.service = search, query.strip(), service
        self.max_calls, self.time_budget, self.workers = max_calls, time_budget, max(1, workers)
        self.started = time.monotonic()
        self.lock = threading.Lock()
        self.calls = 0
        self.entries: Dict[str, Dict[str, Any]] = {}
        self.entry_words: Dict[str, frozenset] = {}
        self.incomplete: List[str] = []
        self.query_words = words(self.query)

    # -- plumbing --------------------------------------------------------------------------------------------
    def budget_left(self, extra: int = 1) -> bool:
        return (self.calls + extra <= self.max_calls
                and time.monotonic() - self.started < self.time_budget)

    def run(self, extra: str) -> Dict[str, Any]:
        with self.lock:
            self.calls += 1
        q = f"{self.query} {extra}".strip()
        res = self.search(q, self.service)
        if not isinstance(res, dict):
            raise RuntimeError(f"Unexpected search_logs result: {str(res)[:200]}")
        self.add(res.get("entries"))
        return res

    def add(self, rows: Any) -> None:
        with self.lock:
            for entry in rows or []:
                if isinstance(entry, dict):
                    k = _key(entry)
                    if k not in self.entries:
                        self.entries[k] = entry
                        self.entry_words[k] = entry_words(entry)

    def local_count(self, extra_words: Sequence[str]) -> int:
        need = set(self.query_words) | {w.lower() for w in extra_words}
        with self.lock:
            return sum(1 for ws in self.entry_words.values() if need <= ws)

    # -- semantics detection ---------------------------------------------------------------------------------
    def prefix_search_works(self, sample: List[Dict[str, Any]]) -> bool:
        """True when a timestamp prefix narrows the result (substring search).

        The probe is a prefix of a sample timestamp cut inside a word, chosen so that its last, partial word
        (e.g. "18T2" of "2025-10-18T2") is not a word of the entry: word matching then cannot return it,
        substring matching must."""
        for e in sample[::-1][:3]:
            ts = str(e.get("ts", ""))
            if not _TS_RE.match(ts):
                continue
            own = entry_words(e)
            for cut in (12, 15, 18, 9):
                prefix = ts[:cut]
                partial = words(prefix)[-1] if words(prefix) else ""
                if not partial or partial in own or any(ch.isspace() for ch in prefix):
                    continue
                try:
                    res = self.run(prefix)
                except Exception:
                    return False
                rows = [r for r in res.get("entries") or [] if isinstance(r, dict)]
                return (int(res.get("total", 0) or 0) > 0 and bool(rows)
                        and all(str(r.get("ts", "")).startswith(prefix) and partial not in entry_words(r)
                                for r in rows))
        return False

    # -- word semantics ---------------------------------------------------------------------------------------
    def _visit(self, path: tuple) -> Optional[tuple]:
        """Query the partition `path`; return (path, total) when it still needs splitting."""
        try:
            res = self.run(" ".join(path))
        except Exception:
            return (path, -1)
        node_total = int(res.get("total", 0) or 0)
        if node_total == 0 or node_total <= len(res.get("entries") or []):
            return None
        if self.local_count(path) >= node_total:
            return None
        return (path, node_total)

    def _refine(self, path: tuple, node_total: int, families: List[List[str]], pool: ThreadPoolExecutor) -> bool:
        """Split `path` by the next word family until the entries collected for it reach its total.
        Returns False when the budget ran out."""
        if node_total >= 0 and self.local_count(path) >= node_total:
            return True
        if not families:
            self.incomplete.append(" ".join(path))
            return True
        family, rest = families[0], families[1:]
        kids = [path + (w,) for w in family if w not in path]
        for i in range(0, len(kids), self.workers):
            batch = kids[i:i + self.workers]
            if not self.budget_left(len(batch)):
                self.incomplete.append(" ".join(path))
                return False
            for out in pool.map(self._visit, batch):
                if out and not self._refine(out[0], out[1], rest, pool):
                    return False
            if node_total >= 0 and self.local_count(path) >= node_total:
                return True
        if node_total < 0 or self.local_count(path) < node_total:
            self.incomplete.append(" ".join(path))
        return True

    def _scan(self, total: int, tokens: List[str], families: List[List[str]], pool: ThreadPoolExecutor) -> None:
        """Query `query + token` for each token (a cover of the result), splitting crowded ones further;
        stop as soon as the collected entries reach the query's total."""
        step = self.workers * 2
        for i in range(0, len(tokens), step):
            if self.local_count(()) >= total:
                return
            batch = [(t,) for t in tokens[i:i + step]]
            if not self.budget_left(len(batch)):
                self.incomplete.extend(t for (t,) in batch)
                return
            for out in pool.map(self._visit, batch):
                if out and not self._refine(out[0], out[1], families, pool):
                    return

    def dayhour_word_works(self, sample: List[Dict[str, Any]]) -> Optional[str]:
        """The real server splits entries into alphanumeric words, so "2025-10-17T08:17:14.421Z" holds the
        words 2025, 10, 17T08, 17, 14, 421Z: day and hour form ONE word (XD7IARR1: all 83 entries of a
        worker's "2025-10-17" query matched through a minute or second 17, none through the day).
        Probe that word; return it when it selects exactly that day and hour."""
        for e in sample[::-1][:3]:
            m = _TS_RE.match(str(e.get("ts", "")))
            if not m:
                continue
            word = f"{m.group(3)}T{m.group(4)}"
            try:
                res = self.run(word)
            except Exception:
                return None
            rows = [r for r in res.get("entries") or [] if isinstance(r, dict)]
            ok = int(res.get("total", 0) or 0) > 0 and rows and all(
                str(r.get("ts", ""))[8:13].upper() == word.upper() and word.lower() in entry_words(r)
                for r in rows)
            return word if ok else None
        return None

    def collect_by_dayhour(self, total: int, newest: str, pool: ThreadPoolExecutor) -> None:
        """Every entry has exactly one day-hour word (DDTHH, 31 x 24 values). Scan them from the newest entry
        backwards in time (so a short incident window finishes after a few dozen calls), split a crowded hour
        by minute, then second, then millisecond words."""
        m = _TS_RE.match(newest) if newest else None
        start = (int(m.group(3)), int(m.group(4))) if m else (31, 23)
        order: List[str] = []
        d, h = start
        for _ in range(31 * 24):
            order.append(f"{d:02d}T{h:02d}")
            h -= 1
            if h < 0:
                h, d = 23, d - 1
                if d < 1:
                    d = 31
        millis = [f"{i:03d}Z" for i in range(1000)]
        self._scan(total, order, [SIXTY, SIXTY, millis], pool)

    def collect_by_words(self, total: int, pool: ThreadPoolExecutor) -> None:
        """Fallback for word search without a usable day-hour word: minute and second words (00..59) cover
        every entry; crowded partitions are split by a second and a third such word (day and hour are words
        too when the timestamp is written "2025-10-17 08:17:14")."""
        self._scan(total, list(SIXTY), [SIXTY, SIXTY], pool)

    # -- substring semantics: valid timestamp prefixes -------------------------------------------------------
    def collect_by_prefix(self, total: int, root_rows: List[Dict[str, Any]], pool: ThreadPoolExecutor) -> None:
        def visit(prefix: str) -> List[str]:
            try:
                res = self.run(prefix)
            except Exception:
                return [prefix]
            node_total = int(res.get("total", 0) or 0)
            rows = res.get("entries") or []
            if node_total == 0 or node_total <= len(rows):
                return []
            children = _children(prefix, seps)
            stamps = [str(e.get("ts", "")) for e in rows if isinstance(e, dict)]
            if stamps and all(stamps) and stamps == sorted(stamps, reverse=True):
                oldest = stamps[-1]                   # the page holds the newest matches
                children = [c for c in children if c <= oldest[:len(c)]]
            if not children:
                with self.lock:
                    self.incomplete.append(prefix)
            return children

        sample_ts = next((str(e.get("ts", "")) for e in root_rows if _TS_RE.match(str(e.get("ts", "")))), "")
        seps = dict(_SEPARATORS)
        if len(sample_ts) > 10:
            seps[10] = sample_ts[10]               # "2025-10-17T08" or "2025-10-17 08"
        sampled = sorted({str(e.get("ts", ""))[:4] for e in root_rows if str(e.get("ts", ""))[:4].isdigit()},
                         reverse=True)
        newest = int(sampled[0]) if sampled else time.gmtime().tm_year
        rounds = [[f"{y}-" for y in sampled] or [f"{newest}-"],
                  [f"{y}-" for y in range(newest + 1, newest - 7, -1) if str(y) not in sampled]]
        for level in rounds:
            frontier = list(level)
            while frontier and len(self.entries) < total:
                if not self.budget_left(len(frontier)):
                    allowed = max(0, self.max_calls - self.calls) if time.monotonic() - self.started < self.time_budget else 0
                    self.incomplete.extend(frontier[allowed:])
                    frontier = frontier[:allowed]
                    if not frontier:
                        break
                nxt: List[str] = []
                for kids in pool.map(visit, frontier):
                    nxt.extend(kids)
                frontier = nxt
            if len(self.entries) >= total:
                break


def collect_all(search: SearchFn, query: str = "", service: str = "", *, max_calls: int = 2000,
                time_budget_seconds: float = 420.0, workers: int = 6) -> Dict[str, Any]:
    """Return {"total", "unique", "complete", "calls", "entries", "strategy", "incompletePartitions", "advice"}."""
    c = _Collector(search, query, service, max_calls, time_budget_seconds, workers)
    root = c.run("")
    total = int(root.get("total", 0) or 0)
    root_rows = [e for e in root.get("entries") or [] if isinstance(e, dict)]
    strategy = "single-page"
    if total > len(root_rows):
        with ThreadPoolExecutor(max_workers=c.workers) as pool:
            if c.prefix_search_works(root_rows):
                strategy = "timestamp-prefix"
                c.collect_by_prefix(total, root_rows, pool)
            elif c.dayhour_word_works(root_rows):
                strategy = "dayhour-words"
                c.collect_by_dayhour(total, str(root_rows[0].get("ts", "")) if root_rows else "", pool)
            else:
                strategy = "timestamp-words"
                c.collect_by_words(total, pool)

    def order(entry: Dict[str, Any]):
        ctx = entry.get("context") if isinstance(entry.get("context"), dict) else {}
        seq = ctx.get("seq")
        return (0, seq, "") if isinstance(seq, (int, float)) else (1, 0, str(entry.get("ts", "")))

    # entries that do not match the query words (a superset partition) are not part of the result
    if strategy in ("timestamp-words", "dayhour-words") and c.query_words:
        need = set(c.query_words)
        keep = {k: e for k, e in c.entries.items() if need <= c.entry_words[k]}
    else:
        keep = c.entries
    ordered = sorted(keep.values(), key=order)
    complete = len(ordered) >= total
    return {"query": query, "service": service, "total": total, "unique": len(ordered),
            "complete": complete, "calls": c.calls, "strategy": strategy,
            "elapsedSeconds": round(time.monotonic() - c.started, 1),
            "incompletePartitions": [] if complete else sorted(set(c.incomplete))[:50],
            "advice": None if complete else (
                "Incomplete: narrow with words every wanted entry has (service, release, rule id, message "
                "words) and collect each part. A date such as 2025-10-17 is NOT a day filter: search matches "
                "whole words, so it also matches entries whose minute or second is 17. If time is short, give "
                "a best estimate from these entries plus the missing count instead of an empty answer."),
            "entries": ordered}
