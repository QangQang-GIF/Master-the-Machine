"""Model and Reasoning Effort Router based on Task Complexity and Budgets."""
from __future__ import annotations

from typing import Any, Dict

from mtm_core.config import load_model_profiles
from mtm_core.contracts import RoutingDecision


class ModelRouter:
    """Assigns the appropriate model, effort level, and timeout budget."""

    def __init__(self):
        self.profiles_config = load_model_profiles()
        self.profiles = self.profiles_config.get("profiles", {})

    def route(self, task_envelope: Dict[str, Any], attempt_number: int = 1) -> RoutingDecision:
        task_id = task_envelope.get("task_id", "UNKNOWN")
        task_type = str(task_envelope.get("task_type", "BUG")).upper()
        points = int(task_envelope.get("points") or 100)

        # Profile selection logic:
        # 1. PI or high points (>= 250) or retry attempt -> deep_reasoning
        # 2. Minor bug or task (< 200 points) -> fast
        # 3. Default -> standard
        if task_type == "PI" or attempt_number > 1:
            profile_name = "deep_reasoning"
            timeout = 480
        elif points >= 250 or task_type == "CR":
            profile_name = "standard"
            timeout = 420
        else:
            profile_name = "fast"
            timeout = 300

        profile = self.profiles.get(profile_name, self.profiles.get("standard", {}))
        model = profile.get("codex_model", "gpt-6.1-sol")
        effort = profile.get("codex_effort", "medium")

        from .skill_router import SkillRouter
        skills = SkillRouter.route_skills(task_envelope)

        return RoutingDecision(
            task_id=task_id,
            selected_skills=skills,
            model=model,
            reasoning_effort=effort,
            timeout_seconds=timeout,
            rationale=f"Assigned {profile_name} profile based on type={task_type}, points={points}, attempt={attempt_number}",
        )
