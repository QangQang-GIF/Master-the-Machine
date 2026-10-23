"""Phase 3: Game Master Contract Tests covering all endpoints and HTTP status flows."""
from unittest.mock import MagicMock, patch
import pytest

from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterAPIError,
    GameMasterAuthError,
    GameMasterConflictError,
    GameMasterRateLimitError,
    MVSLockedError,
)


def test_gm_contract_match_state_server_clock_calibration():
    """Phase 3: get_match_state does not fabricate server_epoch when missing."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")

    # 1. Server returns server_epoch
    with patch.object(adapter, "_request", return_value={"status": "RUNNING", "server_epoch": 1710000000.0}):
        st = adapter.get_match_state()
        assert st["server_epoch"] == 1710000000.0
        assert st["server_calibrated"] is True

    # 2. Server does not provide server_epoch
    with patch.object(adapter, "_request", return_value={"status": "RUNNING"}):
        st = adapter.get_match_state()
        assert st["server_epoch"] is None
        assert st["server_calibrated"] is False


def test_gm_contract_mvs_classification():
    """Phase 3: get_mvs distinguishes 200 OK, 404 No MVS, and 403/423 Locked."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")

    # 1. 200 OK with MVS list
    with patch.object(adapter, "_request", return_value=[{"id": "MVS-1", "description": "test_ok"}]):
        res = adapter.get_mvs("BUG-01")
        assert len(res) == 1
        assert res[0]["id"] == "MVS-1"

    # 2. 404 Not Found -> returns None (server has no MVS for this task)
    with patch.object(adapter, "_request", side_effect=GameMasterAPIError(404, "Not Found")):
        res = adapter.get_mvs("BUG-01")
        assert res is None

    # 3. 403 Forbidden / Locked -> raises MVSLockedError
    with patch.object(adapter, "_request", side_effect=GameMasterAuthError(403, "Forbidden: task not started")):
        with pytest.raises(MVSLockedError, match="MVS is locked"):
            adapter.get_mvs("BUG-01")


def test_gm_contract_submit_retry_policy():
    """Phase 3: submit disables retry without idempotency_key, and supports idempotency retry."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000", max_retries=3)

    with patch.object(adapter, "_request") as mock_req:
        mock_req.return_value = {"status": "ACCEPTED", "submission_id": "sub-1"}

        # Without idempotency key -> retries=1
        adapter.submit("TASK-1", "rev-sha", evidence=["e1"])
        mock_req.assert_called_with(
            "POST",
            "/tasks/TASK-1/submit",
            data={"task_id": "TASK-1", "git_revision": "rev-sha", "evidence": ["e1"]},
            retries=1,
        )

        # With idempotency key -> retries=3
        adapter.submit("TASK-1", "rev-sha", evidence=["e1"], idempotency_key="idemp-key-xyz")
        mock_req.assert_called_with(
            "POST",
            "/tasks/TASK-1/submit",
            data={"task_id": "TASK-1", "git_revision": "rev-sha", "evidence": ["e1"], "idempotency_key": "idemp-key-xyz"},
            retries=3,
        )


def test_gm_contract_error_classification():
    """Phase 3: HTTP status code classification for 401, 409, 429."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")

    import urllib.error
    import io

    # Test 401 raises GameMasterAuthError
    err_401 = urllib.error.HTTPError(
        url="http://gm.local:8000/backlog",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"detail": "Invalid API Key"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err_401):
        with pytest.raises(GameMasterAuthError, match="Invalid API Key"):
            adapter.list_backlog()

    # Test 409 raises GameMasterConflictError
    err_409 = urllib.error.HTTPError(
        url="http://gm.local:8000/tasks/T1/submit",
        code=409,
        msg="Conflict",
        hdrs={},
        fp=io.BytesIO(b'{"detail": "Task already submitted"}'),
    )
    with patch("urllib.request.urlopen", side_effect=err_409):
        with pytest.raises(GameMasterConflictError, match="Task already submitted"):
            adapter.submit("T1", "rev")

    # Test 429 raises GameMasterRateLimitError
    def raise_429(*args, **kwargs):
        raise urllib.error.HTTPError(
            url="http://gm.local:8000/backlog",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "0"},
            fp=io.BytesIO(b'{"detail": "Rate limit exceeded"}'),
        )
    with patch("urllib.request.urlopen", side_effect=raise_429):
        with pytest.raises(GameMasterRateLimitError, match="Rate limit exceeded"):
            adapter.list_backlog()
