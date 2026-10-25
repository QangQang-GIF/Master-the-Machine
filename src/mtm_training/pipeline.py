"""Offline learning candidate workflow. Cannot mutate production Skills automatically."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from .failure_analysis import FailureClassifier, FailureType
from .lesson_extractor import LessonExtractor
from .skill_evolver import SkillEvolver, SkillEvolutionProposal


@dataclass(frozen=True)
class PairedEvaluation:
    eligible: bool
    baseline_pass_rate: float
    candidate_pass_rate: float
    baseline_tokens: int
    candidate_tokens: int
    matched_tasks: int
    reason: str


def evaluate_paired_runs(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    min_tasks: int = 5,
    max_token_ratio: float = 1.10,
) -> PairedEvaluation:
    """Require identical task IDs; no promotion on an unpaired or tiny held-out sample."""
    matched = sorted(set(baseline) & set(candidate))
    if not matched:
        return PairedEvaluation(False, 0, 0, 0, 0, 0, "No matched held-out tasks")
    n = len(matched)
    bpass = sum(baseline[t].get("passed") is True for t in matched) / n
    cpass = sum(candidate[t].get("passed") is True for t in matched) / n
    btok = sum(max(0, int(baseline[t].get("tokens", 0))) for t in matched)
    ctok = sum(max(0, int(candidate[t].get("tokens", 0))) for t in matched)
    if n < min_tasks:
        reason = "Not enough paired held-out tasks"
    elif cpass < bpass:
        reason = "Candidate pass rate regressed"
    elif btok > 0 and ctok > btok * max_token_ratio:
        reason = "Candidate token cost regressed"
    elif btok == 0 and ctok > 0:
        reason = "Baseline token accounting missing"
    else:
        reason = "Eligible for human-reviewed promotion gate"
    return PairedEvaluation(reason.startswith("Eligible"), bpass, cpass, btok, ctok, n, reason)


def propose_from_failures(
    skill_name: str,
    version: str,
    failures: list[dict[str, Any]],
    min_occurrences: int = 3,
    skills_dir: str = ".agents/skills",
) -> SkillEvolutionProposal | None:
    """Propose candidate; do NOT write SKILL.md or ingest benchmark gold patches."""
    clusters: dict[FailureType, list[dict[str, Any]]] = {}
    for event in failures:
        classification = FailureClassifier.classify(event)
        clusters.setdefault(classification, []).append(event)
    if not clusters:
        return None
    failure_type, examples = max(clusters.items(), key=lambda item: len(item[1]))
    if len(examples) < min_occurrences:
        return None
    lesson = LessonExtractor.extract_from_cluster(failure_type, skill_name, examples)
    return SkillEvolver(skills_dir).propose_candidate(skill_name, version, lesson)
