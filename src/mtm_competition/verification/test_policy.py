"""Test policy definitions and adaptive verification levels."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, Optional


class VerificationLevel(str, Enum):
    TARGETED = "TARGETED"
    MVS = "MVS"
    IMPACT_REGRESSION = "IMPACT_REGRESSION"
    FULL = "FULL"


class VerificationStatus(str, Enum):
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    TOO_EXPENSIVE = "TOO_EXPENSIVE"


class TestPolicyManager:
    """Selects appropriate verification policy based on task type and remaining budget."""

    @staticmethod
    def select_policy(
        task_type: str,
        time_budget_seconds: float,
        remaining_match_seconds: float,
    ) -> VerificationLevel:
        # High urgency / low time budget defaults to TARGETED
        if task_type == "PI" or remaining_match_seconds < 300 or time_budget_seconds < 120:
            return VerificationLevel.TARGETED
        elif time_budget_seconds >= 300 and remaining_match_seconds >= 900:
            return VerificationLevel.IMPACT_REGRESSION
        else:
            return VerificationLevel.MVS
