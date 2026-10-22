"""Tournament Simulator: Evaluates scheduler and orchestrator across a 40-minute virtual match."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_arena.metrics import CompetitionMetrics, MatchScorecard


@dataclass
class WaveItem:
    reveal_seconds: float
    task_data: Dict[str, Any]


class MatchSimulator:
    """Simulates multi-wave backlog arrivals and evaluates team score within virtual time."""

    def __init__(
        self,
        timeline: List[WaveItem],
        match_duration_seconds: float = 2400.0,
        time_speedup: float = 1.0,
    ):
        self.timeline = sorted(timeline, key=lambda w: w.reveal_seconds)
        self.match_duration = match_duration_seconds
        self.speedup = max(1.0, time_speedup)

    def run_simulation(
        self,
        orchestrator_factory: Callable[[MockGameMasterAdapter], CompetitionOrchestrator],
    ) -> MatchScorecard:
        gm = MockGameMasterAdapter(tasks=[], duration_seconds=self.match_duration)
        orchestrator = orchestrator_factory(gm)

        virtual_now = 0.0
        step_delta = 5.0  # 5-second ticks
        revealed_index = 0

        while virtual_now < self.match_duration:
            # Reveal scheduled wave items
            while revealed_index < len(self.timeline) and self.timeline[revealed_index].reveal_seconds <= virtual_now:
                item = self.timeline[revealed_index].task_data
                gm.tasks[item["task_id"]] = item
                revealed_index += 1

            # Sync orchestrator backlog
            orchestrator.sync_backlog()

            # Execute step if workers are idle
            if orchestrator.scheduler.has_capacity:
                orchestrator.step()

            virtual_now += step_delta

        # Collect metrics from TaskStore
        submissions = [dict(r) for r in orchestrator.store.conn.execute("SELECT * FROM submissions").fetchall()]
        attempts = [dict(r) for r in orchestrator.store.conn.execute("SELECT * FROM attempts").fetchall()]

        return CompetitionMetrics.compute_scorecard(
            submissions=submissions,
            attempts=attempts,
            match_duration_minutes=self.match_duration / 60.0,
        )
