"""E2E-D — Game Master Staging Integration Suite.

Per Upgrade2.md Phase 4:
"Chỉ thực hiện khi có endpoint hoặc môi trường thử nghiệm do ban tổ chức cung cấp.
- Kết nối bằng thông tin xác thực thử nghiệm.
- Đọc backlog và match state.
- Xác minh Start Working và MVS.
- Sử dụng repository và dữ liệu thử nghiệm.
- Kiểm tra nộp và grading khi staging cho phép.
- Không tự động gửi bài vào hệ thống thi chính thức để kiểm thử.
Done khi: Contract, credentials, thời gian server và trạng thái grading được xác nhận trên môi trường tích hợp thật."
"""
from __future__ import annotations

import os
import pytest

from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterAuthError,
)


def _check_staging_gate(pytestconfig):
    """Enforce staging_game_master marker and environment presence."""
    mark_expr = pytestconfig.getoption("-m", default="")
    if "staging_game_master" not in mark_expr and os.environ.get("MTM_STAGING_GM") != "1":
        pytest.skip("E2E-D Staging GM test. Skipped in default test run (run with -m staging_game_master).")

    staging_url = os.environ.get("MTM_GM_STAGING_URL") or os.environ.get("MTM_GM_URL")
    if not staging_url:
        pytest.skip("BLOCKED: MTM_GM_STAGING_URL / MTM_GM_URL not set in environment.")


@pytest.mark.staging_game_master
def test_e2e_d_staging_match_state_and_clock(pytestconfig):
    """Verify live connection, credentials, and server clock calibration against staging GM."""
    _check_staging_gate(pytestconfig)

    url = os.environ.get("MTM_GM_STAGING_URL") or os.environ.get("MTM_GM_URL")
    token = os.environ.get("MTM_GM_TOKEN", "staging_token")

    adapter = OfficialGameMasterAdapter(base_url=url, auth_token=token)
    state = adapter.get_match_state()

    assert "status" in state
    assert state["status"] in ("NOT_STARTED", "RUNNING", "PAUSED", "FINISHED")
    # Verify server clock calibration
    assert state.get("server_epoch") is not None
    assert state.get("server_calibrated") is True


@pytest.mark.staging_game_master
def test_e2e_d_staging_backlog_retrieval(pytestconfig):
    """Verify backlog retrieval contract against live staging GM."""
    _check_staging_gate(pytestconfig)

    url = os.environ.get("MTM_GM_STAGING_URL") or os.environ.get("MTM_GM_URL")
    token = os.environ.get("MTM_GM_TOKEN", "staging_token")

    adapter = OfficialGameMasterAdapter(base_url=url, auth_token=token)
    backlog = adapter.list_backlog()

    assert isinstance(backlog, list)
    for task in backlog:
        assert "task_id" in task
        assert "task_type" in task
        assert "points" in task


@pytest.mark.staging_game_master
def test_e2e_d_staging_start_working_and_mvs(pytestconfig):
    """Verify Start Working call and MVS reveal against live staging GM."""
    _check_staging_gate(pytestconfig)

    url = os.environ.get("MTM_GM_STAGING_URL") or os.environ.get("MTM_GM_URL")
    token = os.environ.get("MTM_GM_TOKEN", "staging_token")

    adapter = OfficialGameMasterAdapter(base_url=url, auth_token=token)
    backlog = adapter.list_backlog()
    if not backlog:
        pytest.skip("No staging backlog items available to verify start_working.")

    test_task = backlog[0]["task_id"]
    start_resp = adapter.start_working(test_task)
    assert start_resp is not None

    mvs = adapter.get_mvs(test_task)
    # MVS can be None or list of check items
    assert mvs is None or isinstance(mvs, list)


@pytest.mark.staging_game_master
def test_e2e_d_staging_safe_target_verification():
    """Safety check: Staging tests must never target official production tournament hosts."""
    url = (os.environ.get("MTM_GM_STAGING_URL") or os.environ.get("MTM_GM_URL") or "").lower()
    production_hosts = ["prod.mtm2026.org", "contest.mtm2026.org", "official.mtm2026.org"]
    for host in production_hosts:
        assert host not in url, f"Dangerous configuration: Staging tests pointing to production host {host}!"
