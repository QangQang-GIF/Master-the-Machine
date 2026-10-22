"""OfficialGameMasterAdapter: Production connector to the official BTC tournament platform."""
from __future__ import annotations

import os
import urllib.request
import json
from typing import Any, Dict, List, Optional

from .base import GameMasterAdapter


class OfficialGameMasterAdapter(GameMasterAdapter):
    """Adapter for official BTC REST / WebSocket endpoints.

    Active once BTC releases official endpoints and credentials.
    """

    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        team_id: Optional[str] = None,
    ):
        self.api_base_url = api_base_url or os.environ.get("MTM_GM_URL", "http://localhost:8000")
        self.api_key = api_key or os.environ.get("MTM_GM_API_KEY", "")
        self.team_id = team_id or os.environ.get("MTM_TEAM_ID", "")

    def get_match_state(self) -> Dict[str, Any]:
        return {"status": "CONNECTED", "api_url": self.api_base_url, "team_id": self.team_id}

    def list_backlog(self) -> List[Dict[str, Any]]:
        # Placeholder pending official BTC contract release on 26/10
        return []

    def get_task(self, task_id: str) -> Dict[str, Any]:
        return {"task_id": task_id}

    def start_working(self, task_id: str) -> Dict[str, Any]:
        return {"status": "ACKNOWLEDGED", "task_id": task_id}

    def get_mvs(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        return None

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {"status": "PENDING", "task_id": task_id, "git_revision": git_revision}

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        return {"status": "PENDING", "task_id": task_id}
