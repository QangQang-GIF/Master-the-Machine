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


def validate_match_projection_schema(data: Any) -> Dict[str, Any]:
    """Strictly validate match projection schema per Digital Cuder wire contract.

    Requires:
    - Non-empty dictionary / JSON object
    - matchUuid (or id) as a non-empty string
    - matchCode as a non-empty string
    - backlogItems as a list

    Raises:
        ValueError: if empty, non-dict, or missing mandatory fields.
    Returns:
        The validated projection dictionary.
    """
    if not isinstance(data, dict) or not data:
        raise ValueError("Match projection must be a non-empty JSON object / dictionary")

    match_uuid = data.get("matchUuid") or data.get("id")
    if not match_uuid or not isinstance(match_uuid, str) or not match_uuid.strip():
        raise ValueError("Match projection missing required 'matchUuid' or 'id'")

    match_code = data.get("matchCode")
    if not match_code or not isinstance(match_code, str) or not match_code.strip():
        raise ValueError("Match projection missing required 'matchCode'")

    backlog_items = data.get("backlogItems")
    if backlog_items is None or not isinstance(backlog_items, list):
        raise ValueError("Match projection missing required 'backlogItems' as a list")

    return data


class GameMasterAdapter(ABC):
    """Interface defining contracts with the Tournament Game Master."""

    def get_match_state(self) -> Dict[str, Any]:
        """Fetch current match state (duration, remaining time, points, teams)."""
        raise NotImplementedError("Digital Cuder match state is accessed via get_match_projection().")

    def get_match_projection(self, match_id_or_code: Optional[str] = None) -> Dict[str, Any]:
        """Fetch match projection for spectator / match view."""
        return self.get_match_state()

    def login_session(
        self,
        username: str,
        password: str,
        match_page_url: str,
        allowed_origins: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Authenticate browser/cookie session for PREPARE_GAME.

        Optional for mock adapters, implemented by official Game Master adapter.
        """
        raise NotImplementedError("login_session must be implemented by adapter supporting session authentication")

    def list_backlog(self) -> List[Dict[str, Any]]:
        """List currently visible backlog items."""
        raise NotImplementedError("Digital Cuder does not provide /backlog. Use get_match_projection().")

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Fetch detailed description and metadata for a specific task."""
        raise NotImplementedError("Digital Cuder does not provide /tasks/{id}. Use get_match_projection().")

    @abstractmethod
    def start_working(
        self,
        match_uuid_or_task_id: str,
        backlog_item_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Signal to Game Master that the team has started working on this item."""
        pass

    def get_mvs(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve Minimum Viable Solution tests once unlocked."""
        raise NotImplementedError("Digital Cuder does not provide /tasks/{id}/mvs. MVS is in start_work().")

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
        match_uuid: Optional[str] = None,
        answer: Optional[Dict[str, Any]] = None,
        client_submission_id: Optional[str] = None,
        artifact_urls: Optional[List[str]] = None,
        serialized_body_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Submit the solution revision for scoring."""
        raise NotImplementedError("submit must be implemented or delegated to submit_work.")

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

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        """Check grading status (ACCEPTED, PASSED, FAILED)."""
        raise NotImplementedError("Digital Cuder does not provide /tasks/{id}/submission.")


__all__ = [
    "validate_match_uuid",
    "validate_match_projection_schema",
    "GameMasterAdapter",
    "UUID_REGEX",
]

