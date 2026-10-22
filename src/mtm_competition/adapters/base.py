"""Abstract Base Class for Game Master communication."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class GameMasterAdapter(ABC):
    """Interface defining contracts with the Tournament Game Master."""

    @abstractmethod
    def get_match_state(self) -> Dict[str, Any]:
        """Fetch current match state (duration, remaining time, points, teams)."""
        pass

    @abstractmethod
    def list_backlog(self) -> List[Dict[str, Any]]:
        """List currently visible backlog items."""
        pass

    @abstractmethod
    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Fetch detailed description and metadata for a specific task."""
        pass

    @abstractmethod
    def start_working(self, task_id: str) -> Dict[str, Any]:
        """Signal to Game Master that the team has started working on this item."""
        pass

    @abstractmethod
    def get_mvs(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve Minimum Viable Solution tests once unlocked."""
        pass

    @abstractmethod
    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Submit the solution revision for scoring."""
        pass

    @abstractmethod
    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        """Check grading status (ACCEPTED, PASSED, FAILED)."""
        pass
