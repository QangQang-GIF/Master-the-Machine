"""Abstract Base Class for Game Master communication."""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

UUID_REGEX = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def validate_match_uuid(match_uuid: str) -> None:
    """Pre-flight validation ensuring match_uuid is a valid 36-char UUID.

    Rejects 8-character Match Codes with ValueError before network dispatch (API-03).
    """
    if not match_uuid or not isinstance(match_uuid, str):
        raise ValueError(f"Invalid match_uuid: expected 36-character UUID string, got {match_uuid!r}")
    cleaned = match_uuid.strip()
    if len(cleaned) == 8:
        raise ValueError(
            f"Invalid match_uuid '{match_uuid}': 8-character Match Code cannot be used for API endpoints. "
            "Match UUID (36-char UUID) is required."
        )
    if not UUID_REGEX.match(cleaned):
        raise ValueError(
            f"Invalid match_uuid '{match_uuid}': must be a 36-character UUID format (8-4-4-4-12)."
        )


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
    def start_working(
        self,
        match_uuid_or_task_id: str,
        backlog_item_key: Optional[str] = None,
    ) -> Dict[str, Any]:
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

    def submit_work(
        self,
        match_uuid: str,
        client_submission_id: str,
        idempotency_key: str,
        backlog_item_key: str,
        git_revision: str,
        answer: Dict[str, Any],
        artifact_urls: Optional[List[str]] = None,
        serialized_body_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Submit completed solution revision for scoring under Digital Cuder wire contract.

        Optional for legacy mock adapters, implemented by official Digital Cuder wire adapter.
        """
        raise NotImplementedError("submit_work must be implemented by adapter supporting Digital Cuder wire contract")

    @abstractmethod
    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        """Check grading status (ACCEPTED, PASSED, FAILED)."""
        pass
