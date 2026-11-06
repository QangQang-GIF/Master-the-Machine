"""Heavy items: detection, priority, sol/high envelope, window-sized budget."""
import pytest

from mtm_competition.scheduling.native import NativeTaskCoordinator as N
from test_native_scheduler import coordinator, projection, item, start  # noqa: F401  (fixture reuse)


@pytest.mark.parametrize("task", [
    {"key": "HEAVY-01", "type": "HEAVY"},
    {"key": "CR-09", "type": "CR", "backlogType": "HEAVY_CR"},
    {"key": "CR-09", "type": "CR", "heavy": True},
    {"key": "CR-09", "type": "CR", "tags": ["Heavy"]},
    {"key": "CR-09", "type": "CR", "title": "[Heavy] Multi-tenant billing"},
    {"key": "CR-09", "type": "CR", "descriptionMd": "# Heavy task\nBuild ..."},
    {"key": "CR-09", "type": "CR", "points": 300},
])
def test_detects_heavy(task):
    assert N.is_heavy(task)


@pytest.mark.parametrize("task", [
    {"key": "CR-01", "type": "CR", "points": 140, "title": "Export the filtered invoice list as CSV"},
    {"key": "PI-01", "type": "PI", "points": 160},
    {"key": "BUG-02", "type": "BUG", "points": 100, "descriptionMd": "Amounts are 100 times too large"},
])
def test_normal_items_are_not_heavy(task):
    assert not N.is_heavy(task)


def test_points_threshold_can_be_disabled(monkeypatch):
    monkeypatch.setenv("MTM_HEAVY_MIN_POINTS", "0")
    assert not N.is_heavy({"key": "CR-09", "type": "CR", "points": 900})


def test_heavy_starts_before_other_timeless_items(coordinator):
    c = coordinator
    projection(c, item("CR-01", points=140), item("HEAVY-01", type="HEAVY", points=120))
    assert [t["key"] for t in c.plan_tasks()["selected"]][0] == "HEAVY-01"


def test_heavy_envelope_uses_sol_high_and_window_budget(coordinator, monkeypatch):
    monkeypatch.delenv("MTM_HEAVY_MODEL", raising=False)
    monkeypatch.delenv("MTM_HEAVY_EFFORT", raising=False)
    c = coordinator
    projection(c, item("HEAVY-01", type="HEAVY"))          # 2400 s remaining
    start(c, "HEAVY-01")
    env = c.claim_task("HEAVY-01", "w-heavy", 420)["taskEnvelope"]
    assert env["heavy"] is True and env["agent_profile"] == "team_heavy_worker"
    assert env["model"] == "gpt-6.1-sol" and env["reasoning_effort"] == "high"
    assert 2000 <= env["time_budget_seconds"] <= 2100        # whole window minus the 300 s hand-off
    assert "mtm-cr-worker" in env["selected_skills"]        # an existing skill even for type HEAVY


def test_normal_envelope_is_unchanged(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_WORKER_MODEL", "gpt-6.1-sol")
    monkeypatch.setenv("MTM_WORKER_EFFORT", "medium")
    c = coordinator
    projection(c, item("BUG-01"))
    start(c, "BUG-01")
    env = c.claim_task("BUG-01", "w1", 420)["taskEnvelope"]
    assert env["heavy"] is False and env["agent_profile"] == "team_feature_worker"
    assert env["reasoning_effort"] == "medium"
    assert 2100 <= env["time_budget_seconds"] <= 2160        # match window (2400 s) minus the 240 s hand-off
    assert "mtm-bug-worker" in env["selected_skills"]


def _five(c):
    return N(c.db_path.parent, c.checkout_root, c.match_root, "source", "MATCH", max_workers=5, normal_workers=4)


def test_strong_lane_takes_heavy_first(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_STRONG_SLOT", "1")
    c = _five(coordinator)
    projection(c, item("BUG-01"), item("HEAVY-01", type="HEAVY"))
    start(c, "HEAVY-01")
    env = c.claim_task("HEAVY-01", "w-h", 420)["taskEnvelope"]
    assert env["lane"] == "strong" and env["reasoning_effort"] == "high" and env["agent_profile"] == "team_heavy_worker"


def test_strong_lane_runs_a_normal_item_only_when_normal_lanes_are_full(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_STRONG_SLOT", "1")
    monkeypatch.setenv("MTM_WORKER_EFFORT", "medium")
    c = _five(coordinator)
    keys = ["BUG-01", "BUG-02", "BUG-03", "BUG-04", "BUG-05"]
    projection(c, *[item(k) for k in keys])
    lanes = []
    for i, k in enumerate(keys):
        start(c, k)
        env = c.claim_task(k, f"w{i}", 420)["taskEnvelope"]
        lanes.append((env["lane"], env["reasoning_effort"], env["agent_profile"]))
    assert lanes[:4] == [("normal", "medium", "team_feature_worker")] * 4
    assert lanes[4] == ("strong", "high", "team_strong_worker")       # 5th normal item uses the strong worker
    assert c.plan_tasks()["availableSlots"] == 0


def test_without_strong_flag_the_fifth_slot_stays_urgent_only(coordinator, monkeypatch):
    monkeypatch.delenv("MTM_STRONG_SLOT", raising=False)
    c = _five(coordinator)
    projection(c, *[item(f"BUG-0{i}") for i in range(1, 6)])
    assert len(c.plan_tasks()["selected"]) == 4


def test_deadline_item_budget_stops_before_its_deadline(coordinator):
    c = coordinator
    projection(c, item("BUG-02", deadline_epoch=1_000_000 + 18 * 60))   # 18 min deadline, 40 min match
    start(c, "BUG-02")
    env = c.claim_task("BUG-02", "w1", 300)["taskEnvelope"]
    assert 18 * 60 - 160 <= env["time_budget_seconds"] <= 18 * 60 - 150   # deadline minus the 150 s hand-off


def test_estimate_mode_restores_the_old_budget(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_WORKER_BUDGET_MODE", "estimate")
    c = coordinator
    projection(c, item("BUG-01"))
    start(c, "BUG-01")
    assert c.claim_task("BUG-01", "w1", 300)["taskEnvelope"]["time_budget_seconds"] == 300
