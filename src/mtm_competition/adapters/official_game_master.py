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


class GameMasterAuthError(GameMasterAPIError):
    """401/403: Authentication or authorization failed."""
    pass


class GameMasterConflictError(GameMasterAPIError):
    """409: Conflict or duplicate submission."""
    pass


class GameMasterRateLimitError(GameMasterAPIError):
    """429: Rate limited by Game Master."""
    pass


class MVSLockedError(GameMasterAPIError):
    """403/423: MVS is locked or not yet unlocked by start_working."""
    pass


class OfficialGameMasterAdapter(GameMasterAdapter):
    """Production HTTP REST connector for the official BTC tournament platform.

    Handles authentication, timeouts, exponential backoff retries, rate limiting, and strict schema contracts.
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

                msg = parsed_error.get("detail") or parsed_error.get("error") or str(e)

                # HTTP error classification (Phase 3)
                if e.code in (401, 403):
                    raise GameMasterAuthError(e.code, msg, parsed_error)
                elif e.code == 409:
                    raise GameMasterConflictError(e.code, msg, parsed_error)
                elif e.code == 429:
                    retry_after = e.headers.get("Retry-After") if hasattr(e, "headers") else None
                    sleep_time = float(retry_after) if retry_after and retry_after.isdigit() else (0.5 * (2 ** (attempt - 1)))
                    if attempt < max_attempts:
                        time.sleep(sleep_time)
                        continue
                    raise GameMasterRateLimitError(e.code, msg, parsed_error)
                elif 400 <= e.code < 500:
                    raise GameMasterAPIError(e.code, msg, parsed_error)

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
                # Do not retry unsafe POST operations unless explicitly configured
                if method.upper() == "POST" and max_attempts == 1:
                    break
                backoff = 0.5 * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise last_error or GameMasterError(f"Request to {url} failed after {max_attempts} attempts")

    def get_match_state(self) -> Dict[str, Any]:
        """Fetch current match state (duration, remaining time, status, clock)."""
        res = self._request("GET", "/match/state")
        # Do not fabricate server_epoch; distinguish synced vs uncalibrated clock
        if "server_epoch" not in res:
            res["server_epoch"] = None
            res["server_calibrated"] = False
        else:
            res["server_calibrated"] = True
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
        """Retrieve Minimum Viable Solution test specifications.

        Differentiates:
        - 200 OK: Returns List of MVS items.
        - 404 Not Found: Server has no MVS for this task -> returns None.
        - 403 / 423 Locked: MVS not unlocked -> raises MVSLockedError.
        """
        encoded_id = urllib.parse.quote(task_id)
        try:
            res = self._request("GET", f"/tasks/{encoded_id}/mvs")
            if isinstance(res, list):
                return res
            return res.get("mvs") if isinstance(res, dict) else None
        except GameMasterAuthError as e:
            raise MVSLockedError(e.status_code, f"MVS is locked or not unlocked: {e.message}", e.payload)
        except GameMasterAPIError as e:
            if e.status_code == 404:
                return None
            if e.status_code in (403, 423):
                raise MVSLockedError(e.status_code, f"MVS is locked: {e.message}", e.payload)
            raise

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Submit completed solution revision for scoring.

        P0.3 / Phase 3: Single-shot submission without automatic retry unless idempotency key provided.
        """
        encoded_id = urllib.parse.quote(task_id)
        payload = {
            "task_id": task_id,
            "git_revision": git_revision,
            "evidence": evidence or [],
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key

        retries = self.max_retries if idempotency_key else 1
        return self._request("POST", f"/tasks/{encoded_id}/submit", data=payload, retries=retries)

    def get_submission_status(self, task_id: str) -> Dict[str, Any]:
        """Check grading status (ACCEPTED, GRADING, PASSED, FAILED)."""
        encoded_id = urllib.parse.quote(task_id)
        return self._request("GET", f"/tasks/{encoded_id}/submission")
