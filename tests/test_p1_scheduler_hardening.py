import pytest
from mtm_competition.scheduling.native import NativeTaskCoordinator


def test_p1_scheduler_priority_tiers(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")
    import subprocess
    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.local"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    c = NativeTaskCoordinator(
        tmp_path / "state", repo, repo / "source", "source", "MATCH",
        max_workers=4, normal_workers=3
    )

    now = 1_000_000
    items = [
        {"key": "CR-01", "type": "CR", "points": 140, "status": "OPEN"},      # High score CR
        {"key": "BUG-02", "type": "BUG", "points": 20, "status": "OPEN"},      # Low score BUG
        {"key": "PI-01", "type": "PI", "points": 100, "status": "OPEN"},       # Urgent PI (Tier 1)
        {"key": "TASK-03", "type": "TASK", "points": 50, "deadline_epoch": now + 120, "status": "OPEN"}, # Emergency (Tier 0)
    ]

    c.register_projection({
        "status": "RUNNING",
        "server_epoch": now,
        "remaining_seconds": 2400,
        "items": items,
    })

    plan = c.plan_tasks()
    selected_keys = [t["key"] for t in plan["selected"]]

    # Selected order must be:
    # 1. PI-01 (Tier 0: Product Incident, a speed race timed from reveal)
    # 2. TASK-03 (Tier 1: emergency deadline <= 180s)
    # 3. BUG-02 (ordinary items are treated alike: same time left -> key order; points are ignored)
    assert selected_keys[0] == "PI-01"
    assert selected_keys[1] == "TASK-03"
    assert selected_keys[2] == "BUG-02"
