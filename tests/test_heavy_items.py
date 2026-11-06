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
    assert env["reasoning_effort"] == "medium" and env["time_budget_seconds"] == 420
    assert "mtm-bug-worker" in env["selected_skills"]
