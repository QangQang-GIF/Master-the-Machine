"""Match each reported MVS row to the evidence file its scenario requires.

39PSWT5C: the MVS documents name the graded evidence per scenario ("## BUG-11-TC-01 ..." then
"Required evidence: `mvs-tests.log`."). A case scores only when it is reported PASS *with its evidence*.
BUG-11's worker cited per-case logs (evidence/tc-01.log ...) although mvs-tests.log existed, so every PASS
row pointed at the wrong file. This module parses the requirement and

* adds the required file to a PASS row when it exists under answers/<key>/evidence/ (keeping other files),
* lists PASS rows whose required file does not exist at all (the caller decides whether to refuse).
"""
from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Tuple

_HEADING = re.compile(r"^#{2,4}\s+`?([A-Za-z0-9][A-Za-z0-9_.-]*-(?:TC|MVS|SCENARIO|TEST)-[A-Za-z0-9_.-]+)`?")
_REQUIRED = re.compile(r"required evidence\s*[:\-]\s*(.*)", re.IGNORECASE)
_FILE = re.compile(r"`([^`\s]+\.[A-Za-z0-9]{1,8})`|([A-Za-z0-9_.-]+\.(?:png|jpe?g|log|csv|json|txt|md|html|pdf))\b")


def mvs_documents(match_root: Path, key: str) -> List[Path]:
    challenge = Path(match_root) / "challenge"
    if not challenge.is_dir():
        return []
    names = [f"{key}-minimum-verification-scenarios.md", f"{key}-mvs.md"]
    found = [challenge / n for n in names if (challenge / n).is_file()]
    return found or sorted(p for p in challenge.glob(f"{key}*scenario*.md") if p.is_file())


def required_evidence(text: str) -> Dict[str, List[str]]:
    """{scenario id: [file basenames]} from '## <ID> ...' sections with a 'Required evidence:' line."""
    out: Dict[str, List[str]] = {}
    current = None
    collecting = False
    for line in text.splitlines():
        heading = _HEADING.match(line.strip())
        if heading:
            current, collecting = heading.group(1), False
            continue
        if line.startswith("#"):
            current, collecting = (current if line.startswith("####") else None), False
            continue
        if current is None:
            continue
        m = _REQUIRED.search(line)
        if m:
            collecting = True
            rest = m.group(1)
        elif collecting and re.match(r"^\s*[-*]\s+", line):
            rest = line
        else:
            collecting = False
            continue
        for a, b in _FILE.findall(rest):
            name = PurePosixPath((a or b).replace("\\", "/")).name
            if name and name not in out.setdefault(current, []):
                out[current].append(name)
    return {k: v for k, v in out.items() if v}


def _row_id(row: Dict[str, Any]) -> str:
    return str(row.get("id") or row.get("testCaseId") or row.get("scenarioId") or "")


def _matches(row_id: str, scenario: str) -> bool:
    return row_id == scenario or row_id.endswith("-" + scenario) or scenario.endswith("-" + row_id)


def reconcile(answer: Dict[str, Any], requirements: Dict[str, List[str]], evidence_dir: Path,
              key: str) -> Tuple[Dict[str, Any], List[Dict[str, str]], List[Dict[str, str]]]:
    """Return (answer, fixes, missing). Only PASS rows are touched; nothing is ever removed."""
    fixes: List[Dict[str, str]] = []
    missing: List[Dict[str, str]] = []
    rows = answer.get("testResults") if isinstance(answer, dict) else None
    if not isinstance(rows, list) or not requirements:
        return answer, fixes, missing
    available: Dict[str, str] = {}
    if evidence_dir.is_dir():
        for path in sorted(evidence_dir.rglob("*")):
            if path.is_file() and path.stat().st_size > 0:
                available.setdefault(path.name, path.relative_to(evidence_dir).as_posix())
    top = answer.get("evidenceFiles")
    for row in rows:
        if not isinstance(row, dict) or str(row.get("result", "")).upper() != "PASS":
            continue
        rid = _row_id(row)
        needed = next((files for sc, files in requirements.items() if _matches(rid, sc)), None)
        if not needed:
            continue
        cited = row.get("evidence")
        if isinstance(cited, str):
            cited = [cited]
        if not isinstance(cited, list):
            cited = []
        style = next((c for c in cited if isinstance(c, str)), "evidence/x")
        prefix = (f"answers/{key}/evidence/" if style.startswith("answers/") else
                  "evidence/" if style.startswith("evidence/") or "/" not in style else "")
        names = {PurePosixPath(str(c).replace("\\", "/")).name for c in cited}
        for name in needed:
            if name in names:
                continue
            if name in available:
                ref = prefix + available[name]
                cited = [ref, *cited]
                fixes.append({"id": rid, "added": ref})
                if isinstance(top, list) and not any(PurePosixPath(str(t)).name == name for t in top):
                    top.append(ref if ref.startswith(("evidence/", "answers/")) else f"evidence/{available[name]}")
            else:
                missing.append({"id": rid, "file": name})
        row["evidence"] = cited
    return answer, fixes, missing
