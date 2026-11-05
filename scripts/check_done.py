#!/usr/bin/env python3
"""check_done: tell a worker, while it still has time, exactly what stands between its answer and DONE.

Run from anywhere:  python check_done.py --match-root <worker match root> --key BUG-01 [--cutoff-epoch N]

Exit 0  -> answer is DONE-ready (every test PASS, every required evidence file present and non-empty).
Exit 1  -> not ready; stdout is JSON with a ``todo`` list. Keep working while ``secondsLeft`` > 0.
Exit 2  -> cannot evaluate (missing submit.json / unreadable).

It only reads files. It never edits submit.json and never creates evidence.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
try:  # same parser the submission gate uses, so a green check_done means a green gate
    from mtm_competition.integration.git_guard import extract_required_evidence
except Exception:  # pragma: no cover - defensive: still useful without the parser
    def extract_required_evidence(text: str) -> set[str]:
        return set(re.findall(r"evidence/`?([A-Za-z0-9_.-]+\.(?:png|jpg|csv|txt|log|json))`?", text, re.IGNORECASE))


def _scenario_files(match_root: Path, key: str) -> list[Path]:
    return [match_root / "challenge" / f"{key}-minimum-verification-scenarios.md",
            match_root / "challenge" / f"{key}.md",
            match_root / f"{key}-minimum-verification-scenarios.md"]


def evaluate(match_root: Path, key: str, cutoff_epoch: float | None = None, now: float | None = None) -> dict:
    now = time.time() if now is None else now
    submit = match_root / "answers" / key / "submit.json"
    evidence_dir = match_root / "answers" / key / "evidence"
    out: dict = {"key": key, "ready": False, "todo": [], "tests": [], "secondsLeft": None}
    if cutoff_epoch is not None:
        out["secondsLeft"] = round(float(cutoff_epoch) - now, 1)
    if not submit.is_file():
        out["error"] = f"missing {submit}"
        return out
    try:
        answer = json.loads(submit.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        out["error"] = f"unreadable submit.json: {exc}"
        return out

    for row in answer.get("testResults") or []:
        if not isinstance(row, dict):
            continue
        tid, res = row.get("id"), row.get("result")
        out["tests"].append({"id": tid, "result": res})
        if res != "PASS":
            out["todo"].append(f"test {tid} is {res}: make it genuinely pass, then record PASS with its evidence")
        for ev in row.get("evidence") or []:
            if not (match_root / str(ev)).is_file() and not (evidence_dir / Path(str(ev)).name).is_file():
                out["todo"].append(f"test {tid} cites evidence '{ev}' which does not exist on disk")

    required: set[str] = set()
    for f in _scenario_files(match_root, key):
        if f.is_file():
            try:
                required |= extract_required_evidence(f.read_text(encoding="utf-8"))
            except (OSError, UnicodeError):
                pass
    for name in sorted(required):
        p = evidence_dir / name
        if not p.is_file() or p.stat().st_size == 0:
            out["todo"].append(f"required evidence file '{name}' is missing or empty in answers/{key}/evidence/")

    if not out["tests"]:
        out["todo"].append("submit.json has no testResults rows (copy them from the Start Work template)")
    out["ready"] = not out["todo"]
    out["status"] = "DONE" if out["ready"] else "NOT_READY"
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--match-root", required=True)
    ap.add_argument("--key", required=True)
    ap.add_argument("--cutoff-epoch", type=float, default=None, help="local epoch from taskEnvelope.evidence_cutoff_local_epoch")
    a = ap.parse_args(argv)
    res = evaluate(Path(a.match_root), a.key, a.cutoff_epoch)
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if res.get("error"):
        return 2
    return 0 if res["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
