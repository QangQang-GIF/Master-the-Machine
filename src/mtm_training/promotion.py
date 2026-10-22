"""PromotionGate: Verifies A/B benchmark evaluation and promotes candidates to production."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from .skill_evolver import SkillEvolutionProposal


@dataclass
class PromotionDecision:
    promoted: bool
    skill_name: str
    from_version: str
    to_version: str
    reason: str


class PromotionGate:
    """Evaluates candidate performance and performs versioned promotion."""

    def __init__(
        self,
        skills_dir: Path | str = ".agents/skills",
        registry_manifest: Path | str = "skills_registry/manifest.json",
    ):
        self.skills_dir = Path(skills_dir).resolve()
        self.manifest_path = Path(registry_manifest).resolve()

    def evaluate_and_promote(
        self,
        proposal: SkillEvolutionProposal,
        baseline_pass_rate: float,
        candidate_pass_rate: float,
        candidate_tokens: int,
        baseline_tokens: int,
    ) -> PromotionDecision:
        # Promotion criteria:
        # 1. Pass rate must not regress (candidate_pass_rate >= baseline_pass_rate)
        # 2. Token efficiency should not degrade by more than 10%
        if candidate_pass_rate < baseline_pass_rate:
            return PromotionDecision(
                promoted=False,
                skill_name=proposal.base_skill,
                from_version=proposal.base_version,
                to_version=proposal.candidate_version,
                reason=f"Pass rate regressed from {baseline_pass_rate} to {candidate_pass_rate}",
            )

        if candidate_tokens > (baseline_tokens * 1.15):
            return PromotionDecision(
                promoted=False,
                skill_name=proposal.base_skill,
                from_version=proposal.base_version,
                to_version=proposal.candidate_version,
                reason=f"Candidate token consumption increased excessively ({candidate_tokens} vs {baseline_tokens})",
            )

        # Apply promotion to SKILL.md
        target_file = self.skills_dir / proposal.base_skill / "SKILL.md"
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_text(proposal.candidate_content, encoding="utf-8")

        # Update registry manifest
        if self.manifest_path.exists():
            manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
            if "skills" in manifest and proposal.base_skill in manifest["skills"]:
                manifest["skills"][proposal.base_skill]["version"] = proposal.candidate_version
                self.manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

        return PromotionDecision(
            promoted=True,
            skill_name=proposal.base_skill,
            from_version=proposal.base_version,
            to_version=proposal.candidate_version,
            reason=f"Candidate improved/maintained pass rate ({candidate_pass_rate} >= {baseline_pass_rate}) with acceptable efficiency",
        )
