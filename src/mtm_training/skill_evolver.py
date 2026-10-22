"""SkillEvolver: Proposes structured, versioned improvements to skills."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .lesson_extractor import GeneralizedLesson


@dataclass
class SkillEvolutionProposal:
    base_skill: str
    base_version: str
    candidate_version: str
    proposed_rule: str
    rationale: str
    candidate_content: str


class SkillEvolver:
    """Generates candidate skill revisions incorporating validated procedural lessons."""

    def __init__(self, skills_dir: Path | str = ".agents/skills"):
        self.skills_dir = Path(skills_dir).resolve()

    def propose_candidate(
        self,
        skill_name: str,
        current_version: str,
        lesson: GeneralizedLesson,
    ) -> SkillEvolutionProposal:
        skill_path = self.skills_dir / skill_name / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8") if skill_path.exists() else ""

        # Bump minor version for new procedural rule
        major, minor, patch = [int(p) for p in current_version.split(".")]
        candidate_version = f"{major}.{minor + 1}.0"

        # Append procedural rule to Operational Guidelines
        addition = f"\n\n### Learned Procedural Guideline (v{candidate_version})\n- {lesson.rule_statement} (Rationale: {lesson.rationale})\n"
        updated_content = content + addition

        return SkillEvolutionProposal(
            base_skill=skill_name,
            base_version=current_version,
            candidate_version=candidate_version,
            proposed_rule=lesson.rule_statement,
            rationale=lesson.rationale,
            candidate_content=updated_content,
        )
