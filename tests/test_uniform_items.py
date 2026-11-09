"""Every ordinary item is treated alike; only PI has its own worker flow. One model for every worker."""
from test_native_scheduler import coordinator, projection, item, start  # noqa: F401  (fixture reuse)


def _wide(c):
    c.max_workers, c.normal_workers = 6, 5
    return c


def _claim(c, key, worker):
    start(c, key)
    return c.claim_task(key, worker, 420)["taskEnvelope"]


def test_every_worker_uses_the_same_model_and_profile(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_WORKER_MODEL", "gpt-6-luna")
    monkeypatch.setenv("MTM_WORKER_EFFORT", "low")
    monkeypatch.setenv("MTM_HEAVY_MODEL", "gpt-6.1-sol")      # ignored: no Heavy class any more
    monkeypatch.setenv("MTM_STRONG_SLOT", "1")                 # ignored: no strong lane any more
    c = _wide(coordinator)
    projection(c, item("HEAVY-01", type="HEAVY", points=900), item("CR-01", points=140), item("PI-01", type="PI"))
    envs = [_claim(c, k, f"w{i}") for i, k in enumerate(("HEAVY-01", "CR-01", "PI-01"))]
    assert {(e["model"], e["reasoning_effort"], e["agent_profile"]) for e in envs} == {("gpt-6-luna", "low", "team_feature_worker")}
    assert [e["lane"] for e in envs] == ["normal", "normal", "pi"]
    assert all("heavy" not in e for e in envs)


def test_ordinary_items_are_not_ranked_by_type_points_or_size(coordinator):
    c = _wide(coordinator)
    projection(c, item("TASK-02", type="TASK", points=10), item("CR-01", points=900),
               item("BUG-03", type="BUG", points=50), item("HEAVY-01", type="HEAVY", points=300))
    assert [t["key"] for t in c.plan_tasks()["selected"]] == ["BUG-03", "CR-01", "HEAVY-01", "TASK-02"]


def test_pi_first_then_least_time_left(coordinator):
    c = _wide(coordinator)
    projection(c, item("CR-01"), item("BUG-02", deadline_epoch=1_000_000 + 18 * 60), item("PI-01", type="PI"))
    assert [t["key"] for t in c.plan_tasks()["selected"]][:3] == ["PI-01", "BUG-02", "CR-01"]


def test_deadline_item_budget_stops_before_its_deadline(coordinator):
    c = _wide(coordinator)
    projection(c, item("BUG-02", deadline_epoch=1_000_000 + 18 * 60))   # 18 min deadline, 40 min match
    env = _claim(c, "BUG-02", "w1")
    assert 18 * 60 - 160 <= env["time_budget_seconds"] <= 18 * 60 - 150   # deadline minus the 150 s hand-off


def test_estimate_mode_restores_the_old_budget(coordinator, monkeypatch):
    monkeypatch.setenv("MTM_WORKER_BUDGET_MODE", "estimate")
    c = _wide(coordinator)
    projection(c, item("BUG-01"))
    start(c, "BUG-01")
    assert c.claim_task("BUG-01", "w1", 300)["taskEnvelope"]["time_budget_seconds"] == 300
