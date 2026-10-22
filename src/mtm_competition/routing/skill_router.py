"""Deterministic Skill Router based on Task Type and Context Requirements."""
from __future__ import annotations

from typing import Any, Dict, List

PRIMARY_SKILL_MAP = {
    "BUG": "mtm-bug-worker@1.0.0",
    "CR": "mtm-cr-worker@1.0.0",
    "TASK": "mtm-task-worker@1.0.0",
    "PI": "mtm-pi-worker@1.0.0",
}


class SkillRouter:
    """Selects the optimal skill bundle for a given TaskEnvelope."""

    @staticmethod
    def route_skills(task_envelope: Dict[str, Any]) -> List[str]:
        task_type = str(task_envelope.get("task_type", "BUG")).upper()
        primary = PRIMARY_SKILL_MAP.get(task_type, "mtm-task-worker@1.0.0")

        selected = ["mtm-init@1.0.0", primary]

        # Add support skill for evidence if required
        evidence_required = task_envelope.get("evidence_required") or []
        if evidence_required:
            selected.append("mtm-evidence@1.0.0")

        # Add support skill for verification if MVS is present
        mvs = task_envelope.get("mvs") or []
        if mvs:
            selected.append("mtm-verification@1.0.0")

        return selected
