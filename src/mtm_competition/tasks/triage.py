"""V2 lightweight task triage (metadata-first, no repository scan, no LLM call).

Output follows Upgrade Plan V2 section 5.2. Every number is a *forecast*, never a verified result.
Official metadata (explicit flags/tags) wins; keyword heuristics are a lower-confidence fallback.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

WORKLOAD_SUBTYPES = (
    "DATA_FAST", "LOGIC_FIX", "EVIDENCE_HEAVY", "PI_FAST", "PI_COMPLEX", "FEATURE_STANDARD", "HEAVY_DAG",
)
OFFICIAL_TYPES = ("TASK", "CR", "BUG", "PI")

_UI_WORDS = re.compile(r"\b(ui|screen ?shot|browser|playwright|css|render|page|layout|chart|dashboard|responsive|form)\b", re.I)
_DATA_WORDS = re.compile(r"\b(data|csv|json|compute|calculate|aggregate|report|export|parse|checksum|totals?)\b", re.I)
_MULTI_WORDS = re.compile(r"\b(multiple|several|across|end[- ]to[- ]end|migration|refactor|rebuild|redesign|integration|subsystem|modules?)\b", re.I)
_PI_COMPLEX_WORDS = re.compile(r"\b(intermittent|race|deadlock|memory leak|corruption|cascad|outage|data loss|unknown|root cause)\b", re.I)

_BASE_SECONDS = {"TASK": 150.0, "BUG": 180.0, "CR": 300.0, "PI": 150.0}
_BASE_EVIDENCE = {"TASK": 30.0, "BUG": 45.0, "CR": 60.0, "PI": 30.0}


@dataclass(frozen=True)
class TriageResult:
    backlogItemKey: str
    officialType: str
    workloadSubtype: str
    complexity: str
    confidence: float
    estimatedWorkSeconds: int
    estimatedEvidenceSeconds: int
    parallelizable: bool
    recommendedModelTier: str
    reason: str
    estimateKind: str = "forecast"  # never a verified measurement

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _text(task: Dict[str, Any]) -> str:
    return " ".join(str(task.get(k) or "") for k in ("title", "name", "description", "summary"))


def _mvs_count(task: Dict[str, Any]) -> int:
    for k in ("minimumVerificationScenarios", "mvs", "testScenarios"):
        v = task.get(k)
        if isinstance(v, list):
            return len(v)
    return 0


def triage_task(task: Dict[str, Any], history: Optional[Dict[str, float]] = None) -> TriageResult:
    """Classify one backlog item from announced metadata only.

    `history` may carry observed mean seconds per official type from earlier tasks in this match
    (e.g. {"BUG": 210.0}); it only adjusts forecasts, never confidence upward beyond the metadata.
    """
    key = str(task.get("key") or task.get("backlogItemKey") or "")
    otype = str(task.get("type") or task.get("officialType") or "TASK").upper()
    if otype not in OFFICIAL_TYPES:
        otype = "TASK"
    text = _text(task)
    mvs = _mvs_count(task)
    ui = bool(_UI_WORDS.search(text))
    multi = bool(_MULTI_WORDS.search(text))
    reasons: List[str] = []
    confidence = 0.55

    heavy_flag = task.get("heavy") is True or text.strip().lower().startswith("[heavy task]")
    explicit = task.get("workloadSubtype")
    tags = {str(t).lower() for t in (task.get("tags") or []) if isinstance(t, str)}

    if explicit in WORKLOAD_SUBTYPES:
        subtype, confidence = explicit, 0.95
        reasons.append("official workloadSubtype provided")
    elif heavy_flag or "heavy" in tags:
        subtype, confidence = "HEAVY_DAG", 0.85
        reasons.append("official heavy flag/prefix")
    elif otype == "PI":
        if _PI_COMPLEX_WORDS.search(text) or mvs >= 5:
            subtype, confidence = "PI_COMPLEX", 0.65
            reasons.append("incident wording or many scenarios suggests investigation")
        else:
            subtype, confidence = "PI_FAST", 0.7
            reasons.append("incident with limited surface")
    elif ui and mvs >= 3:
        subtype, confidence = "EVIDENCE_HEAVY", 0.7
        reasons.append("UI keywords with several verification scenarios")
    elif multi and mvs >= 5:
        subtype, confidence = "HEAVY_DAG", 0.55
        reasons.append("multi-part wording with many scenarios")
    elif otype == "TASK" and _DATA_WORDS.search(text) and not ui:
        subtype, confidence = "DATA_FAST", 0.65
        reasons.append("data/compute wording without UI")
    elif otype == "BUG" and not ui and mvs <= 3:
        subtype, confidence = "LOGIC_FIX", 0.65
        reasons.append("bug with few scenarios and no UI")
    else:
        subtype = "FEATURE_STANDARD"
        reasons.append("no stronger signal")

    work = _BASE_SECONDS[otype]
    evidence = _BASE_EVIDENCE[otype]
    work *= 1.0 + 0.1 * min(mvs, 8)
    if ui:
        evidence *= 2.0
        reasons.append("UI evidence capture expected")
    if subtype == "HEAVY_DAG":
        work *= 1.8
    if subtype == "DATA_FAST":
        work *= 0.7
        evidence *= 0.5
    if subtype == "EVIDENCE_HEAVY":
        evidence *= 1.5
    if history and otype in history and history[otype] > 0:
        work = 0.5 * work + 0.5 * float(history[otype])
        reasons.append("blended with observed match history")

    if subtype in ("HEAVY_DAG", "PI_COMPLEX") or work >= 400:
        complexity = "HIGH"
    elif subtype in ("DATA_FAST", "LOGIC_FIX", "PI_FAST") and work < 220:
        complexity = "LOW"
    else:
        complexity = "MEDIUM"

    tier = {"HIGH": "strong", "MEDIUM": "standard", "LOW": "fast"}[complexity]
    if subtype == "PI_FAST":
        tier = "standard"  # speed matters but a wrong fast fix is costly; avoid the weakest tier
    if confidence < 0.6 and tier == "fast":
        tier = "standard"  # low confidence: prefer the safer tier
        reasons.append("low confidence -> safer model tier")

    return TriageResult(
        backlogItemKey=key, officialType=otype, workloadSubtype=subtype, complexity=complexity,
        confidence=round(confidence, 2), estimatedWorkSeconds=int(round(work)),
        estimatedEvidenceSeconds=int(round(evidence)), parallelizable=subtype == "HEAVY_DAG",
        recommendedModelTier=tier, reason="; ".join(reasons),
    )
