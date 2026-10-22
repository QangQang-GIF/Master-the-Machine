"""Priority Queue sorted by Expected Value."""
from __future__ import annotations

import heapq
from typing import Any, Dict, List, Optional

from mtm_competition.tasks.scoring import calculate_expected_value


class PriorityQueue:
    """Max-heap priority queue ordered by calculated Expected Value."""

    def __init__(self):
        self._items: List[Dict[str, Any]] = []

    def push(self, task: Dict[str, Any], match_remaining_seconds: float = 2400.0) -> None:
        ev = calculate_expected_value(task, match_remaining_seconds=match_remaining_seconds)
        task["calculated_ev"] = ev
        # Use negative EV for max-heap behavior
        heapq.heappush(self._items, (-ev, task["task_id"], task))

    def pop(self) -> Optional[Dict[str, Any]]:
        if not self._items:
            return None
        _, _, task = heapq.heappop(self._items)
        return task

    def peek(self) -> Optional[Dict[str, Any]]:
        if not self._items:
            return None
        return self._items[0][2]

    def __len__(self) -> int:
        return len(self._items)
