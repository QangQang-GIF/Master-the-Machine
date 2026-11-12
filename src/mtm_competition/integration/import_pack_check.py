"""Order-independence check for statement packs imported with Actual Budget's reconcileTransactions.

Why: a manifest that states "what the application does" with every file is graded by importing the pack with
the issued source. reconcileTransactions matches an imported line to an existing transaction first by
imported_id (OFX FITID), then fuzzily: same account, same amount, date within 7 days either way, and (strict id
checking) at least one of the two without an imported_id. When a line has several candidates, or two lines of
one file compete for one candidate, the result depends on processing order (file order, the dialog's preview
sort, payee ids...). A harness that measured in one order then disagrees with the grader (XD7IARR1 TASK-07:
"added 391, stated 395" on three OFX files, 0/14 points).

This tool replays the matching rules on the manifest itself and reports:
  * ambiguities: lines whose outcome depends on order; the generator must remove every one of them;
  * the predicted expectedAdded / expectedCount / expectedBalance, valid only when there is no ambiguity,
    to compare with what the manifest states and what the harness measured.

Manifest shape (TASK-07 pack): {"accounts": [{"name", "files": [{"path", "transactions": [{"date": "YYYY-MM-DD",
"amount": int cents, "importedId": str|null, ...}], "expectedAdded"}], "expectedCount", "expectedBalance"}]}

Usage: python import_pack_check.py <manifest.json> [--window-days 7] [--loose-ids]
Exit 0 when unambiguous and every stated figure equals the prediction, 1 otherwise.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any


def _day(value: str) -> int:
    return date.fromisoformat(str(value)[:10]).toordinal()


def check_manifest(manifest: dict[str, Any], window_days: int = 7, strict_ids: bool = True,
                   max_reports: int = 40) -> dict[str, Any]:
    ambiguities: list[dict[str, Any]] = []
    files_out: list[dict[str, Any]] = []
    accounts_out: list[dict[str, Any]] = []

    def report(kind: str, account: str, path: str, line: int, detail: str) -> None:
        if len(ambiguities) < max_reports:
            ambiguities.append({"kind": kind, "account": account, "file": path, "line": line, "detail": detail})
        report.total += 1
    report.total = 0  # type: ignore[attr-defined]

    for account in manifest.get("accounts", []):
        name = str(account.get("name"))
        existing: list[dict[str, Any]] = []          # {"day", "amount", "importedId"}
        for f in account.get("files", []):
            path = str(f.get("path"))
            rows = [{"day": _day(t["date"]), "amount": int(t["amount"]),
                     "importedId": t.get("importedId") or None} for t in f.get("transactions", [])]
            claimed: set[int] = set()                 # indexes of existing rows matched in this import
            match_of: dict[int, int] = {}             # incoming line -> existing index
            # pass 1: exact imported_id (no claim check in the application: several lines may hit one row)
            for i, r in enumerate(rows):
                if not r["importedId"]:
                    continue
                hits = [k for k, e in enumerate(existing) if e["importedId"] == r["importedId"]]
                if len(hits) > 1:
                    report("duplicate-imported-id", name, path, i + 1,
                           f"importedId {r['importedId']} is on {len(hits)} existing transactions")
                if hits:
                    match_of[i] = hits[0]
                    claimed.add(hits[0])
            # fuzzy candidate sets of the lines without an exact match
            cand: dict[int, list[int]] = {}
            for i, r in enumerate(rows):
                if i in match_of:
                    continue
                c = [k for k, e in enumerate(existing)
                     if k not in claimed and e["amount"] == r["amount"]
                     and abs(e["day"] - r["day"]) <= window_days
                     and (not strict_ids or e["importedId"] is None or r["importedId"] is None)]
                if c:
                    cand[i] = c
            users: dict[int, list[int]] = {}
            for i, c in cand.items():
                if len(c) > 1:
                    report("several-candidates", name, path, i + 1,
                           f"amount {rows[i]['amount']} on {date.fromordinal(rows[i]['day'])} could match "
                           f"{len(c)} existing transactions")
                for k in c:
                    users.setdefault(k, []).append(i)
            for k, lines in users.items():
                if len(lines) > 1:
                    report("competing-lines", name, path, lines[0] + 1,
                           f"lines {[n + 1 for n in lines[:6]]} compete for one existing transaction "
                           f"(amount {existing[k]['amount']} on {date.fromordinal(existing[k]['day'])})")
            for i, c in cand.items():
                match_of[i] = c[0]
            # apply: matched rows take the incoming imported_id (or lose theirs), the rest are added
            added = 0
            for i, r in enumerate(rows):
                if i in match_of:
                    existing[match_of[i]]["importedId"] = r["importedId"]
            for i, r in enumerate(rows):
                if i not in match_of:
                    existing.append(dict(r))
                    added += 1
            stated = f.get("expectedAdded")
            files_out.append({"account": name, "file": path, "lines": len(rows), "predictedAdded": added,
                              "statedAdded": stated, "ok": stated == added})
        count, balance = len(existing), sum(e["amount"] for e in existing)
        accounts_out.append({"account": name, "predictedCount": count, "statedCount": account.get("expectedCount"),
                             "predictedBalance": balance, "statedBalance": account.get("expectedBalance"),
                             "ok": account.get("expectedCount") == count and account.get("expectedBalance") == balance})

    total = report.total  # type: ignore[attr-defined]
    mismatched = [f for f in files_out if not f["ok"]] + [a for a in accounts_out if not a["ok"]]
    return {
        "ok": total == 0 and not mismatched,
        "ambiguityCount": total,
        "ambiguities": ambiguities,
        "mismatches": mismatched[:max_reports],
        "files": files_out,
        "accounts": accounts_out,
        "advice": ("" if total == 0 else
                   "Order-dependent lines found: the grader may import in another order than your harness. "
                   "Regenerate so that, within an account, distinct transactions with the same amount are more "
                   f"than {window_days} days apart unless both carry an OFX FITID; keep weekly series in OFX files "
                   "with one stable FITID per occurrence; repeat an overlapping transaction with the same date and "
                   "amount (and the same FITID in OFX)."),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("manifest")
    parser.add_argument("--window-days", type=int, default=7)
    parser.add_argument("--loose-ids", action="store_true",
                        help="fuzzy-match rows even when both carry imported ids (strictIdChecking=false)")
    args = parser.parse_args(argv)
    manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8-sig"))
    out = check_manifest(manifest, args.window_days, not args.loose_ids)
    summary = {k: v for k, v in out.items() if k not in ("files",)}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
