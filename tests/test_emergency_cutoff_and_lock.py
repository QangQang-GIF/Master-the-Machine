import time
import subprocess
import pytest
from pathlib import Path
from mtm_competition.scheduling.native import NativeTaskCoordinator, NativeCoordinationError


def test_emergency_cutoff_at_120s(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")

    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH")
    
    # Task 1: deadline slack = 150s (> 120s) -> Should NOT emergency flush
    # Task 2: deadline slack = 90s (<= 120s) -> Should emergency flush
    server_now = 1000
    items = [
        {"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 80, "deadline_epoch": 1300},
        {"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100, "deadline_epoch": 1200},
    ]
    c.register_projection({
        "status": "RUNNING",
        "server_epoch": server_now,
        "remaining_seconds": 2400,
        "items": items,
    })
    
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "worker-1")
    
    c.register_started("BUG-02", {})
    claim_2 = c.claim_task("BUG-02", "worker-2")

    # Add a modified file in BUG-02 worktree so emergency cutoff detects changes
    wt_2 = Path(claim_2["worktreeRoot"]) / "source"
    (wt_2 / "main.py").write_text("print(2)\n")

    # 60 s later: BUG-01 has 240 s left (> 150 s item flush), BUG-02 has 140 s left (<= 150 s)
    c.register_projection({"status": "RUNNING", "server_epoch": server_now + 60, "remaining_seconds": 2340,
                           "items": items})
    plan = c.plan_tasks()

    # BUG-01 stays RUNNING
    assert c.task_details("BUG-01")["phase"] == "RUNNING"
    # BUG-02 is auto-promoted to READY_TO_INTEGRATE with the deadline flush
    assert c.task_details("BUG-02")["phase"] == "READY_TO_INTEGRATE"
    res = c.task_details("BUG-02")["workerResult"]
    assert res["status"] == "PARTIAL"
    assert "Hard cutoff triggered at " in res["note"]


def test_endgame_lock_release_after_8s(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")

    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH")

    # Match in endgame: remaining_seconds = 120s (<= 180s)
    c.register_projection({
        "status": "RUNNING",
        "server_epoch": 2280,
        "remaining_seconds": 120,
        "items": [
            {"key": "CR-03", "type": "CR", "status": "OPEN", "points": 100},
            {"key": "BUG-04", "type": "BUG", "status": "OPEN", "points": 80},
        ],
    })

    w_res = {
        "task_id": "CR-03",
        "worker_id": "worker-1",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-cr-worker"],
        "changed_files": ["main.py"],
        "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}],
        "evidence_paths": [],
        "elapsed_seconds": 10,
    }
    c.register_started("CR-03", {})
    c.claim_task("CR-03", "worker-1")
    c.record_worker_result("CR-03", w_res)
    c.begin_integration("CR-03")

    # CR-03 acquired lock 9 seconds ago
    with c._db() as db:
        c._set_meta(db, "integration_acquired_at", time.monotonic() - 9.0)

    # BUG-04 is ready to integrate
    w_res_bug = dict(w_res, task_id="BUG-04", worker_id="worker-2")
    c.register_started("BUG-04", {})
    c.claim_task("BUG-04", "worker-2")
    c.record_worker_result("BUG-04", w_res_bug)

    # In endgame (rem_clk <= 180), lock held > 8s must be forcefully stolen by BUG-04
    integ = c.begin_integration("BUG-04")
    assert integ["phase"] == "INTEGRATING"
    with c._db() as db:
        assert c._meta(db, "integration_owner") == "BUG-04"
    assert c.task_details("CR-03")["phase"] == "READY_TO_INTEGRATE"


def test_global_8s_lock_and_ready_sorting(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    (repo / "source").mkdir()
    (repo / "source" / "main.py").write_text("print(1)\n")

    subprocess.run(["git", "-C", str(repo), "init"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], check=True, capture_output=True)

    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH")

    # Match in NORMAL phase (remaining_seconds = 2000s, far from endgame)
    c.register_projection({
        "status": "RUNNING",
        "server_epoch": 1000,
        "remaining_seconds": 2000,
        "items": [
            {"key": "CR-01", "type": "CR", "status": "OPEN", "points": 140},
            {"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 80},
        ],
    })

    w_res_cr = {
        "task_id": "CR-01", "worker_id": "w1", "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-cr-worker"], "changed_files": ["main.py"],
        "test_results": [{"command": "pytest", "passed": True, "exit_code": 0}],
        "evidence_paths": [], "elapsed_seconds": 10,
    }
    c.register_started("CR-01", {})
    c.claim_task("CR-01", "w1")
    c.record_worker_result("CR-01", w_res_cr)
    c.begin_integration("CR-01")

    # CR-01 acquired lock 9s ago (stalled lock during normal match time)
    with c._db() as db:
        c._set_meta(db, "integration_acquired_at", time.monotonic() - 9.0)

    # BUG-01 finishes
    w_res_bug = dict(w_res_cr, task_id="BUG-01", worker_id="w2")
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w2")
    c.record_worker_result("BUG-01", w_res_bug)

    # Check plan_tasks():
    # 1. Stalled CR-01 (>8s) is auto-demoted in plan_tasks()
    plan = c.plan_tasks()
    assert plan["integrationOwner"] is None

    # 2. readyToIntegrate sorts BUG before CR (fast-track logic)
    ready_keys = [r["key"] for r in plan["readyToIntegrate"]]
    assert ready_keys == ["BUG-01", "CR-01"]

    # 3. BUG-01 acquires lock immediately
    integ = c.begin_integration("BUG-01")
    assert integ["phase"] == "INTEGRATING"
    with c._db() as db:
        assert c._meta(db, "integration_owner") == "BUG-01"

