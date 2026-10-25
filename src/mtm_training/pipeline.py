"""Offline learning candidate workflow, dataset persistence, A/B evaluation, and human-reviewed promotion."""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .failure_analysis import FailureClassifier, FailureType
from .lesson_extractor import LessonExtractor
from .promotion import PromotionDecision, PromotionGate
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


@dataclass(frozen=True)
class ABPipelineReport:
    evaluation: PairedEvaluation
    proposal: SkillEvolutionProposal | None
    proposed_diff: str
    promoted: bool
    promotion_decision: PromotionDecision | None
    reviewer: str | None
    backup_content: str | None = None


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


def generate_proposal_diff(
    proposal: SkillEvolutionProposal,
    skills_dir: Path | str = ".agents/skills",
) -> str:
    """Generate a unified diff for a proposed SKILL.md change without mutating disk."""
    skill_path = Path(skills_dir).resolve() / proposal.base_skill / "SKILL.md"
    original = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""
    diff_lines = difflib.unified_diff(
        original.splitlines(keepends=True),
        proposal.candidate_content.splitlines(keepends=True),
        fromfile=f"{proposal.base_skill}/SKILL.md@{proposal.base_version}",
        tofile=f"{proposal.base_skill}/SKILL.md@{proposal.candidate_version}",
    )
    return "".join(diff_lines)


def save_paired_dataset(
    path: Path | str,
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]] | None = None,
) -> Path:
    """Persist held-out paired evaluation records to JSON."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "baseline": baseline,
        "candidate": candidate,
        "failures": failures or [],
    }
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return target


def load_paired_dataset(path: Path | str) -> dict[str, Any]:
    """Load held-out paired evaluation records from JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "baseline" not in data or "candidate" not in data:
        raise ValueError("Invalid paired dataset format")
    return data


def run_ab_training_pipeline(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    failures: list[dict[str, Any]],
    skill_name: str,
    current_version: str = "1.0.0",
    skills_dir: Path | str = ".agents/skills",
    registry_manifest: Path | str = "skills_registry/manifest.json",
    promote: bool = False,
    reviewer: str | None = None,
    min_tasks: int = 5,
    max_token_ratio: float = 1.10,
) -> ABPipelineReport:
    """Execute held-out A/B evaluation and failure-driven proposal generation.

    Never mutates live SKILL.md unless `promote=True`, an independent `reviewer` is provided,
    and `evaluate_paired_runs` certifies eligibility.
    """
    evaluation = evaluate_paired_runs(
        baseline=baseline,
        candidate=candidate,
        min_tasks=min_tasks,
        max_token_ratio=max_token_ratio,
    )
    proposal = propose_from_failures(
        skill_name=skill_name,
        version=current_version,
        failures=failures,
        skills_dir=str(skills_dir),
    )
    diff_text = generate_proposal_diff(proposal, skills_dir=skills_dir) if proposal else ""

    if not promote or not proposal:
        return ABPipelineReport(
            evaluation=evaluation,
            proposal=proposal,
            proposed_diff=diff_text,
            promoted=False,
            promotion_decision=None,
            reviewer=reviewer,
            backup_content=None,
        )

    if not reviewer or not str(reviewer).strip():
        raise ValueError("Independent human reviewer identifier is required for skill promotion")

    if not evaluation.eligible:
        decision = PromotionDecision(
            promoted=False,
            skill_name=skill_name,
            from_version=current_version,
            to_version=proposal.candidate_version,
            reason=evaluation.reason,
        )
        return ABPipelineReport(
            evaluation=evaluation,
            proposal=proposal,
            proposed_diff=diff_text,
            promoted=False,
            promotion_decision=decision,
            reviewer=reviewer,
            backup_content=None,
        )

    skill_path = Path(skills_dir).resolve() / skill_name / "SKILL.md"
    backup = skill_path.read_text(encoding="utf-8") if skill_path.exists() else None

    gate = PromotionGate(
        skills_dir=skills_dir,
        registry_manifest=registry_manifest,
        max_token_increase_ratio=max_token_ratio,
    )
    decision = gate.evaluate_and_promote(
        proposal=proposal,
        baseline_pass_rate=evaluation.baseline_pass_rate,
        candidate_pass_rate=evaluation.candidate_pass_rate,
        candidate_tokens=evaluation.candidate_tokens,
        baseline_tokens=evaluation.baseline_tokens,
    )
    return ABPipelineReport(
        evaluation=evaluation,
        proposal=proposal,
        proposed_diff=diff_text,
        promoted=decision.promoted,
        promotion_decision=decision,
        reviewer=reviewer,
        backup_content=backup,
    )


def rollback_skill_promotion(
    skill_name: str,
    previous_version: str,
    backup_content: str,
    skills_dir: Path | str = ".agents/skills",
    registry_manifest: Path | str = "skills_registry/manifest.json",
) -> None:
    """Restore SKILL.md and registry manifest version to their pre-promotion state."""
    skill_path = Path(skills_dir).resolve() / skill_name / "SKILL.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(backup_content, encoding="utf-8")

    manifest_path = Path(registry_manifest).resolve()
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if "skills" in manifest and skill_name in manifest["skills"]:
            manifest["skills"][skill_name]["version"] = previous_version
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
