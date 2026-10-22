"""Arena Competition Metrics v2: Score@40m, efficiency, deadline misses, and latency."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class MatchScorecard:
    total_score: int = 0
    max_possible_score: int = 1000
    match_duration_minutes: float = 40.0
    tasks_submitted: int = 0
    tasks_passed: int = 0
    tasks_failed: int = 0
    deadline_misses: int = 0
    pi_latency_seconds: List[float] = field(default_factory=list)
    billable_tokens: int = 0
    wasted_attempts: int = 0
    worker_utilization: float = 0.0

    @property
    def pass_rate(self) -> float:
        if not self.tasks_submitted:
            return 0.0
        return round(self.tasks_passed / self.tasks_submitted, 4)

    @property
    def points_per_minute(self) -> float:
        if self.match_duration_minutes <= 0:
            return 0.0
        return round(self.total_score / self.match_duration_minutes, 2)

    @property
    def average_pi_latency(self) -> float:
        if not self.pi_latency_seconds:
            return 0.0
        return round(sum(self.pi_latency_seconds) / len(self.pi_latency_seconds), 1)


class CompetitionMetrics:
    """Calculates tournament scorecards from orchestrator attempt and submission logs."""

    @staticmethod
    def compute_scorecard(
        submissions: List[Dict[str, Any]],
        attempts: List[Dict[str, Any]],
        match_duration_minutes: float = 40.0,
    ) -> MatchScorecard:
        card = MatchScorecard(match_duration_minutes=match_duration_minutes)

        passed = 0
        failed = 0
        total_pts = 0
        tokens = 0
        pi_latencies = []

        for sub in submissions:
            pts = int(sub.get("score") or 0)
            status = sub.get("status", "")
            if status in ("ACCEPTED", "PASSED"):
                passed += 1
                total_pts += pts
            else:
                failed += 1

        for att in attempts:
            tokens += int(att.get("billable_tokens") or 0)
            if att.get("status") in ("FAILED", "TIMED_OUT"):
                card.wasted_attempts += 1

        card.total_score = total_pts
        card.tasks_submitted = len(submissions)
        card.tasks_passed = passed
        card.tasks_failed = failed
        card.billable_tokens = tokens

        return card
