"""Model and Reasoning Effort Router based on Task Complexity and Budgets."""
from __future__ import annotations

from typing import Any, Dict

from mtm_core.config import load_model_profiles
from mtm_core.contracts import RoutingDecision


class ModelRouter:
    """Assigns the appropriate model, effort level, backend, and timeout budget."""

    def __init__(self, default_backend: str = "codex"):
        self.profiles_config = load_model_profiles()
        self.profiles = self.profiles_config.get("profiles", {})
        self.default_backend = default_backend

    def route(
        self,
        task_envelope: Dict[str, Any],
        attempt_number: int = 1,
        backend: Optional[str] = None,
    ) -> RoutingDecision:
        task_id = task_envelope.get("task_id", "UNKNOWN")
        task_type = str(task_envelope.get("task_type", "BUG")).upper()
        points = int(task_envelope.get("points") or 100)
        selected_backend = (
            backend
            or task_envelope.get("backend")
            or self.default_backend
        ).lower()

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

        # Select model and effort based on active backend
        if selected_backend == "antigravity":
            routed_model = profile.get("agy_model", "gemini-3.8-flash-high")
            routed_effort = profile.get("agy_effort", "medium")
        else:
            selected_backend = "codex"
            routed_model = profile.get("codex_model", "gpt-6.1-sol")
            routed_effort = profile.get("codex_effort", "medium")

        # Explicit overrides in task_envelope take precedence if specified
        final_model = task_envelope.get("model") or routed_model
        final_effort = task_envelope.get("reasoning_effort") or routed_effort
        final_timeout = int(task_envelope.get("time_budget_seconds") or timeout)

        from .skill_router import SkillRouter
        skills = SkillRouter.route_skills(task_envelope)

        return RoutingDecision(
            task_id=task_id,
            selected_skills=skills,
            model=final_model,
            reasoning_effort=final_effort,
            timeout_seconds=final_timeout,
            backend=selected_backend,
            rationale=f"Assigned {profile_name} profile on backend={selected_backend} (type={task_type}, points={points}, attempt={attempt_number})",
        )
