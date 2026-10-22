"""LessonExtractor: Synthesizes procedural lessons from failure clusters."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from .failure_analysis import FailureType


@dataclass
class GeneralizedLesson:
    failure_type: FailureType
    target_skill: str
    rule_statement: str
    confidence: float
    cluster_size: int
    rationale: str


class LessonExtractor:
    """Extracts generalizable rules from repeated failure patterns.

    Enforces that lessons contain procedural rules, never benchmark answers or gold patches.
    """

    @staticmethod
    def extract_from_cluster(
        failure_type: FailureType,
        target_skill: str,
        occurrences: List[Dict[str, Any]],
    ) -> GeneralizedLesson:
        count = len(occurrences)
        confidence = min(0.95, 0.5 + (count * 0.05))

        if failure_type == FailureType.TIMEOUT:
            rule = "Bound initial investigation: if root cause is not identified within 60s, narrow search to top stack frame."
            rationale = f"Observed {count} timeouts due to unbounded search"
        elif failure_type == FailureType.OVER_EDIT:
            rule = "Strict surgical scope: limit changes to the single failing function/file unless multi-module change is explicitly demanded."
            rationale = f"Observed {count} regressions caused by modifying auxiliary files"
        elif failure_type == FailureType.TOKEN_WASTE:
            rule = "Use targeted ripgrep with line bounds and pipe noisy test outputs through tail."
            rationale = f"Observed {count} runs exceeding 100k context tokens"
        else:
            rule = "Run focused unit test before claiming completion."
            rationale = f"Observed {count} unverified claim failures"

        return GeneralizedLesson(
            failure_type=failure_type,
            target_skill=target_skill,
            rule_statement=rule,
            confidence=confidence,
            cluster_size=count,
            rationale=rationale,
        )
