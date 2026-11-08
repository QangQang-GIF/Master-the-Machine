"""Regression tests for the Solution v2 hardening (submit error taxonomy, lease, mojibake, QA gate)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mtm_competition.verification.encoding_check import (  # noqa: E402
    MojibakeError, assert_no_mojibake, find_mojibake, repair_mojibake,
)
from mtm_competition.verification.qa_gate import map_mvs_to_test_command  # noqa: E402
from runner.mcp_server import (  # noqa: E402
    SUBMIT_ALREADY_SUBMITTED, SUBMIT_FIXABLE, SUBMIT_TERMINAL, SUBMIT_UNKNOWN, classify_submit_error,
)


@pytest.mark.parametrize("status,code,msg,expected", [
    (409, "backlog_item_closed", "item closed", SUBMIT_TERMINAL),
    (409, "already_submitted", "", SUBMIT_ALREADY_SUBMITTED),
    (404, "", "not found", SUBMIT_TERMINAL),
    (410, "", "gone", SUBMIT_TERMINAL),
    (400, "", "bad payload", SUBMIT_FIXABLE),
    (422, "", "schema mismatch", SUBMIT_FIXABLE),
    (500, "", "boom", SUBMIT_UNKNOWN),
    (429, "", "slow down", SUBMIT_UNKNOWN),
    (None, "", "timeout", SUBMIT_UNKNOWN),
])
def test_classify_submit_error(status, code, msg, expected):
    assert classify_submit_error(status, code, msg) == expected


def test_mojibake_detected_and_repaired():
    bad = "Váº­n chuyá»ƒn"
    assert repair_mojibake(bad) == "Vận chuyển"
    assert find_mojibake({"a": [bad]})
    with pytest.raises(MojibakeError):
        assert_no_mojibake({"summary": {"Quá»¹ dá»± phÃ²ng": 1}})


def test_clean_text_not_flagged():
    for text in ("Vận chuyển", "café", "naïve", "日本語", "plain ascii"):
        assert not find_mojibake({"k": text})
    assert_no_mojibake({"k": "Quỹ dự phòng"})


def test_mvs_never_maps_to_bare_pytest():
    assert map_mvs_to_test_command("") == ""
    assert map_mvs_to_test_command("test_x") == "pytest -k test_x"


# ---- NativeTaskCoordinator ------------------------------------------------------------------------
import subprocess  # noqa: E402
import time  # noqa: E402

from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator  # noqa: E402


def _coordinator(tmp_path, items, remaining=2000, epoch=1000):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for args in (["init"], ["config", "user.email", "t@t"], ["config", "user.name", "T"], ["add", "."],
                 ["commit", "-m", "init"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH",
                              max_workers=4, normal_workers=3)
    c.register_projection({"status": "RUNNING", "server_epoch": epoch, "remaining_seconds": remaining, "items": items})
    return c


def _result(key, worker, **over):
    base = {"task_id": key, "worker_id": worker, "status": "READY_FOR_REVIEW", "skill_ids": ["s"],
            "changed_files": ["main.py"], "test_results": [{"command": "pytest tests/x.py", "passed": True, "exit_code": 0}],
            "evidence_paths": [], "elapsed_seconds": 5}
    base.update(over)
    return base


def _ready(c, key, worker):
    c.register_started(key, {})
    c.claim_task(key, worker)
    c.record_worker_result(key, _result(key, worker))


def test_in_flight_submit_not_stolen_at_lease_but_reclaimed_after_ttl(tmp_path):
    c = _coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100},
                                {"key": "CR-02", "type": "CR", "status": "OPEN", "points": 100}])
    _ready(c, "CR-01", "w1")
    _ready(c, "CR-02", "w2")
    c.begin_integration("CR-01")
    c.mark_submit_in_flight("CR-01")
    with c._db() as db:
        c._set_meta(db, "integration_acquired_at", time.monotonic() - 9.0)
    c.plan_tasks()                            # reclaim pass
    with pytest.raises(NativeCoordinationError):
        c.begin_integration("CR-02")          # lease expired but Submit outcome unknown: keep the lock
    with c._db() as db:
        meta = dict(c._meta(db, "integration_submit_inflight"))
        meta["since"] = time.monotonic() - 121.0
        c._set_meta(db, "integration_submit_inflight", meta)
    c.plan_tasks()
    assert c.begin_integration("CR-02")["phase"] == "INTEGRATING"


def test_touch_integration_renews_lease(tmp_path):
    c = _coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100},
                                {"key": "CR-02", "type": "CR", "status": "OPEN", "points": 100}])
    _ready(c, "CR-01", "w1")
    _ready(c, "CR-02", "w2")
    c.begin_integration("CR-01")
    with c._db() as db:
        c._set_meta(db, "integration_acquired_at", time.monotonic() - 7.5)
    assert c.touch_integration("CR-01") is True
    time.sleep(1.0)
    with pytest.raises(NativeCoordinationError):
        c.begin_integration("CR-02")


def test_blocked_result_is_not_promoted_to_partial(tmp_path):
    c = _coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100}])
    c.register_started("CR-01", {})
    c.claim_task("CR-01", "w1")
    c.record_worker_result("CR-01", _result("CR-01", "w1", status="BLOCKED"))
    assert c.task_details("CR-01")["workerResult"]["status"] != "PARTIAL"


def test_result_without_tests_or_evidence_rejected(tmp_path):
    c = _coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100}])
    c.register_started("CR-01", {})
    c.claim_task("CR-01", "w1")
    with pytest.raises(NativeCoordinationError):
        c.record_worker_result("CR-01", _result("CR-01", "w1", test_results=[]))


def test_emergency_flush_marks_result_unverified(tmp_path):
    c = _coordinator(tmp_path, [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100,
                                 "deadline_epoch": 1090}])
    c.register_started("BUG-02", {})
    claim = c.claim_task("BUG-02", "w1")
    (Path(claim["worktreeRoot"]) / "source" / "main.py").write_text("print(2)\n")
    c.plan_tasks()
    res = c.task_details("BUG-02")["workerResult"]
    assert res["test_results"] == [] and res["verification_status"] == "UNVERIFIED"


def test_pi_dispatched_before_emergency_deadline_task(tmp_path):
    c = _coordinator(tmp_path, [
        {"key": "BUG-09", "type": "BUG", "status": "OPEN", "points": 80, "deadline_epoch": 1170},
        {"key": "PI-02", "type": "PI", "status": "OPEN", "points": 90},
    ])
    plan = c.plan_tasks()
    assert plan["selected"][0]["key"] == "PI-02"
