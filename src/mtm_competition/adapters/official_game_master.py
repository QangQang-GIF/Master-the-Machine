"""OfficialGameMasterAdapter: Production connector to the official BTC tournament platform."""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .base import GameMasterAdapter

logger = logging.getLogger("mtm.game_master")


class GameMasterError(Exception):
    """Base exception for Game Master communication errors."""
    pass


class GameMasterConnectionError(GameMasterError):
    """Raised when Game Master server cannot be reached."""
    pass


class GameMasterAPIError(GameMasterError):
    """Raised when Game Master returns an HTTP error status."""
    def __init__(self, status_code: int, message: str, payload: Optional[Dict[str, Any]] = None):
        super().__init__(f"Game Master API Error {status_code}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}


class OfficialGameMasterAdapter(GameMasterAdapter):
    """Production HTTP REST connector for the official BTC tournament platform.

    Handles authentication, timeouts, exponential backoff retries, and strict schema validation.
    """

    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        team_id: Optional[str] = None,
        timeout: float = 8.0,
        max_retries: int = 3,
    ):
        base = api_base_url or os.environ.get("MTM_GM_URL", "http://localhost:8000")
        self.api_base_url = base.rstrip("/")
        self.api_key = api_key or os.environ.get("MTM_GM_API_KEY", "")
        self.team_id = team_id or os.environ.get("MTM_TEAM_ID", "")
        self.timeout = timeout
        self.max_retries = max_retries

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
    ) -> Any:
        url = f"{self.api_base_url}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "User-Agent": "MTM-Codex-Agent/1.0.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        if self.team_id:
            headers["X-Team-ID"] = self.team_id

        encoded_data = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            encoded_data = json.dumps(data).encode("utf-8")

        max_attempts = retries if retries is not None else self.max_retries
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                req = urllib.request.Request(
                    url=url,
                    data=encoded_data,
                    headers=headers,
                    method=method.upper(),
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_body = resp.read().decode("utf-8")
                    if not resp_body.strip():
                        return {}
                    return json.loads(resp_body)

            except urllib.error.HTTPError as e:
                error_body = e.read().decode("utf-8", errors="replace")
                parsed_error = {}
                try:
                    parsed_error = json.loads(error_body)
                except Exception:
                    pass

                # 4xx errors (client error / duplicate / bad request) should not retry
                if 400 <= e.code < 500:
                    raise GameMasterAPIError(
                        status_code=e.code,
                        message=parsed_error.get("detail") or parsed_error.get("error") or str(e),
                        payload=parsed_error,
                    )

                last_error = GameMasterAPIError(
                    status_code=e.code,
                    message=f"HTTP {e.code}: {e.reason}",
                    payload=parsed_error,
                )

            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = GameMasterConnectionError(f"Connection to {url} failed: {e}")

            except Exception as e:
                last_error = GameMasterError(f"Unexpected request failure: {e}")

            if attempt < max_attempts:
                backoff = 0.5 * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise last_error or GameMasterError(f"Request to {url} failed after {max_attempts} attempts")

    def get_match_state(self) -> Dict[str, Any]:
        """Fetch current match state (duration, remaining time, status, clock)."""
        res = self._request("GET", "/match/state")
        # Ensure standard keys
        if "server_epoch" not in res:
            res["server_epoch"] = time.time()
        return res

    def list_backlog(self) -> List[Dict[str, Any]]:
        """List currently available backlog items."""
        res = self._request("GET", "/backlog")
        if isinstance(res, list):
            return res
        return res.get("tasks", []) if isinstance(res, dict) else []

    def get_task(self, task_id: str) -> Dict[str, Any]:
        """Fetch full metadata for a task."""
        encoded_id = urllib.parse.quote(task_id)
        return self._request("GET", f"/tasks/{encoded_id}")

    def start_working(self, task_id: str) -> Dict[str, Any]:
        """Signal to Game Master that work has started on task."""
        encoded_id = urllib.parse.quote(task_id)
        return self._request("POST", f"/tasks/{encoded_id}/start")

    def get_mvs(self, task_id: str) -> Optional[List[Dict[str, Any]]]:
        """Retrieve Minimum Viable Solution test specifications."""
        encoded_id = urllib.parse.quote(task_id)
        try:
            res = self._request("GET", f"/tasks/{encoded_id}/mvs")
            if isinstance(res, list):
                return res
            return res.get("mvs") if isinstance(res, dict) else None
        except GameMasterAPIError as e:
            if e.status_code == 404:
                return None
            raise

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Submit completed solution revision for scoring."""
        encoded_id = urllib.parse.quote(task_id)
        payload = {
            "task_id": task_id,
            "git_revision": git_revision,
            "evidence": evidence or [],
        }
        return self._request("POST", f"/tasks/{encoded_id}/submit", data=payload)

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        """Check grading status (ACCEPTED, GRADING, PASSED, FAILED)."""
        encoded_id = urllib.parse.quote(task_id)
        return self._request("GET", f"/tasks/{encoded_id}/submission")
