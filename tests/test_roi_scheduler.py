import pytest
from mtm_competition.scheduling.native import NativeTaskCoordinator


def test_roi_difficulty_calculation(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=4, normal_workers=3)

    # TASK-04: 70 points, TASK, logic only
    t_task = {"key": "TASK-04", "type": "TASK", "points": 70, "title": "Trace log data"}
    diff_task = c._calculate_task_difficulty(t_task)
    assert diff_task == 1.0
    assert (t_task["points"] / diff_task) == 70.0

    # BUG-04: 80 points, BUG, logic only
    t_bug = {"key": "BUG-04", "type": "BUG", "points": 80, "title": "Fix invoice calculation"}
    diff_bug = c._calculate_task_difficulty(t_bug)
    assert diff_bug == 1.5
    assert (t_bug["points"] / diff_bug) == pytest.approx(53.333, rel=1e-3)

    # CR-01: 140 points, CR, UI layout / csv export
    t_cr1 = {
        "key": "CR-01",
        "type": "CR",
        "points": 140,
        "title": "Export CSV",
        "description": "Download CSV from layout sidebar",
    }
    diff_cr1 = c._calculate_task_difficulty(t_cr1)
    # base 2.5 + ui_penalty 1.0 = 3.5
    assert diff_cr1 == 3.5
    assert (t_cr1["points"] / diff_cr1) == 40.0

    # CR-02: 120 points, CR, UI table
    t_cr2 = {
        "key": "CR-02",
        "type": "CR",
        "points": 120,
        "title": "Customer Table",
        "description": "Render customer table component",
    }
    diff_cr2 = c._calculate_task_difficulty(t_cr2)
    # base 2.5 + ui_penalty 1.0 = 3.5
    assert diff_cr2 == 3.5
    assert (t_cr2["points"] / diff_cr2) == pytest.approx(34.2857, rel=1e-3)


def test_roi_prioritization_order(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=4, normal_workers=3)

    # 4 tasks registered into projection
    tasks = [
        {
            "key": "CR-01",
            "type": "CR",
            "points": 140,
            "title": "Export CSV",
            "description": "sidebar layout",
            "status": "OPEN",
        },
        {
            "key": "CR-02",
            "type": "CR",
            "points": 120,
            "title": "Customer Table",
            "description": "component table",
            "status": "OPEN",
        },
        {
            "key": "TASK-04",
            "type": "TASK",
            "points": 70,
            "title": "Trace log data",
            "status": "OPEN",
        },
        {
            "key": "BUG-04",
            "type": "BUG",
            "points": 80,
            "title": "Fix invoice calculation",
            "status": "OPEN",
        },
    ]
    c.register_projection(
        {
            "status": "RUNNING",
            "server_epoch": 1000,
            "remaining_seconds": 2400,
            "items": tasks,
        }
    )
    plan = c.plan_tasks()
    selected_keys = [t["key"] for t in plan["selected"]]
    # Normal workers capacity is 3 (1 slot reserved for urgent/PI).
    # Top 3 selected by ROI descending: TASK-04 (70.0) -> BUG-04 (53.3) -> CR-01 (40.0)
    assert selected_keys == ["TASK-04", "BUG-04", "CR-01"]

    # When 4 normal slots are available (max_workers=5, normal_workers=4), CR-02 is selected 4th:
    c_4 = NativeTaskCoordinator(tmp_path / "state2", repo, repo / "source", "source", "MATCH", max_workers=5, normal_workers=4)
    c_4.register_projection(
        {
            "status": "RUNNING",
            "server_epoch": 1000,
            "remaining_seconds": 2400,
            "items": tasks,
        }
    )
    plan_4 = c_4.plan_tasks()
    selected_keys_4 = [t["key"] for t in plan_4["selected"]]
    assert selected_keys_4 == ["TASK-04", "BUG-04", "CR-01", "CR-02"]
