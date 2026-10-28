"""Phase 3: Game Master Contract Tests covering Digital Cuder wire contract and legacy deprecation."""
from unittest.mock import MagicMock, patch
import pytest

from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterAPIError,
    GameMasterAuthError,
    GameMasterConflictError,
    GameMasterRateLimitError,
)


def test_gm_contract_legacy_endpoints_raise_not_implemented():
    """Phase 3: Legacy pre-2026 endpoints raise NotImplementedError per Digital Cuder contract."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")

    with pytest.raises(NotImplementedError, match="Digital Cuder match state is accessed via get_match_projection"):
        adapter.get_match_state()

    with pytest.raises(NotImplementedError, match="Digital Cuder does not provide /tasks/{id}/mvs"):
        adapter.get_mvs("BUG-01")

    with pytest.raises(NotImplementedError, match="Digital Cuder does not provide /backlog"):
        adapter.list_backlog()

    with pytest.raises(NotImplementedError, match="Digital Cuder does not provide /tasks/{id}"):
        adapter.get_task("BUG-01")

    with pytest.raises(NotImplementedError, match="Digital Cuder does not provide /tasks/{id}/submission"):
        adapter.get_submission_status("BUG-01")


def test_gm_contract_submit_wire_contract_enforcement():
    """Phase 3: submit enforces Digital Cuder wire contract and eliminates legacy /tasks/{id}/submit route."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")

    # 1. Missing match_uuid or answer -> raises ValueError
    with pytest.raises(ValueError, match="Digital Cuder wire contract requires 'match_uuid' and 'answer' dictionary"):
        adapter.submit("TASK-1", "rev-sha", evidence=["e1"])

    # 2. 8-char Match Code -> raises ValueError
    with pytest.raises(ValueError, match="8-character Match Code cannot be used for API endpoints"):
        adapter.submit("TASK-1", "rev-sha", match_uuid="ABCDEFGH", answer={"status": "SUBMITTED"})

    # 3. Valid parameters -> delegates to submit_work
    match_uuid = "8c0e1b22-1111-4222-8333-444444444444"
    with patch.object(adapter, "_request") as mock_req:
        mock_req.return_value = {
            "submission": {
                "id": "sub-1",
                "status": "ACCEPTED",
                "scoreStatus": "PENDING",
            }
        }
        res = adapter.submit(
            task_id="TASK-1",
            git_revision="a" * 40,
            match_uuid=match_uuid,
            answer={"status": "SUBMITTED"},
            idempotency_key="idemp-key-xyz",
        )
        assert res["submission"]["status"] == "ACCEPTED"
        mock_req.assert_called_with(
            "POST",
            f"/api/v1/matches/{match_uuid}/submissions",
            data={
                "clientSubmissionId": mock_req.call_args[1]["data"]["clientSubmissionId"],
                "backlogItemKey": "TASK-1",
                "gitRevision": "a" * 40,
                "answer": {"status": "SUBMITTED"},
            },
            headers={"Idempotency-Key": "idemp-key-xyz"},
            raw_body=None,
            retries=1,
        )


def test_gm_contract_error_classification():
    """Phase 3: HTTP status code classification for 401, 409, 429 on wire endpoints."""
    adapter = OfficialGameMasterAdapter("http://gm.local:8000")
    match_uuid = "8c0e1b22-1111-4222-8333-444444444444"

    import urllib.error
    import io

    # Test 401 raises GameMasterAuthError
    err_401 = urllib.error.HTTPError(
        url=f"http://gm.local:8000/api/v1/matches/{match_uuid}/backlogs/start",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b'{"error": {"code": "INVALID_API_KEY", "message": "Invalid API Key"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=err_401):
        with pytest.raises(GameMasterAuthError, match="Invalid API Key"):
            adapter.start_working(match_uuid, "TASK-1")

    # Test 409 raises GameMasterConflictError
    err_409 = urllib.error.HTTPError(
        url=f"http://gm.local:8000/api/v1/matches/{match_uuid}/submissions",
        code=409,
        msg="Conflict",
        hdrs={},
        fp=io.BytesIO(b'{"error": {"code": "ALREADY_SUBMITTED", "message": "Task already submitted"}}'),
    )
    with patch("urllib.request.urlopen", side_effect=err_409):
        with pytest.raises(GameMasterConflictError, match="Task already submitted"):
            adapter.submit("T1", "a" * 40, match_uuid=match_uuid, answer={"status": "SUBMITTED"})

    # Test 429 raises GameMasterRateLimitError
    def raise_429(*args, **kwargs):
        raise urllib.error.HTTPError(
            url=f"http://gm.local:8000/api/v1/matches/{match_uuid}/backlogs/start",
            code=429,
            msg="Too Many Requests",
            hdrs={"Retry-After": "0"},
            fp=io.BytesIO(b'{"error": {"code": "RATE_LIMITED", "message": "Rate limit exceeded"}}'),
        )
    with patch("urllib.request.urlopen", side_effect=raise_429):
        with pytest.raises(GameMasterRateLimitError, match="Rate limit exceeded"):
            adapter.start_working(match_uuid, "TASK-1")
