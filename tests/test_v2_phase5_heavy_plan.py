"""V2 Phase 5: Heavy DAG contract, validation, critical path, spawn gate."""
import copy

import pytest

from mtm_competition.tasks.heavy_plan import (
    HeavyPlan, HeavyPlanError, SpawnGate, check_child_diff, critical_path, ready_subtasks, validate, worth_splitting)

BASE = {
    "planVersion": 1, "parentBacklogItemKey": "CR-05", "parentAgentId": "heavy-CR-05", "baseRevision": "abc123",
    "deadlineEpoch": 2_000_000,
    "subtasks": [
        {"subtaskId": "A", "description": "backend", "dependsOn": [], "ownedPaths": ["src/server/"],
         "expectedOutputs": ["svc"], "estimatedSeconds": 180},
        {"subtaskId": "B", "description": "ui", "dependsOn": [], "ownedPaths": ["src/components/"],
         "expectedOutputs": ["ui"], "estimatedSeconds": 210},
        {"subtaskId": "C", "description": "integrate", "dependsOn": ["A", "B"], "ownedPaths": ["src/app/"],
         "expectedOutputs": ["tests"], "estimatedSeconds": 120},
    ],
}


def plan(**mut):
    d = copy.deepcopy(BASE)
    d.update(mut)
    return HeavyPlan.from_dict(d)


def test_valid_plan_and_critical_path():
    p = plan()
    validate(p)
    cp = critical_path(p)
    assert cp["path"] == ["B", "C"] and cp["seconds"] == 330
    after_b = critical_path(p, done=["B"])
    assert after_b["path"] == ["A", "C"] and after_b["seconds"] == 300
    assert critical_path(p, done=["A", "B", "C"]) == {"seconds": 0.0, "path": []}


def test_critical_path_recomputed_when_a_child_is_late():
    p = plan()
    late = critical_path(p, overrides={"A": 500})
    assert late["path"][0] == "A" and late["seconds"] == 620


def test_rejects_cycle_unknown_dep_duplicate_and_bad_version():
    d = copy.deepcopy(BASE)
    d["subtasks"][0]["dependsOn"] = ["C"]
    with pytest.raises(HeavyPlanError, match="cycle"):
        validate(HeavyPlan.from_dict(d))
    d = copy.deepcopy(BASE)
    d["subtasks"][2]["dependsOn"] = ["Z"]
    with pytest.raises(HeavyPlanError, match="dependency"):
        validate(HeavyPlan.from_dict(d))
    d = copy.deepcopy(BASE)
    d["subtasks"][1]["subtaskId"] = "A"
    with pytest.raises(HeavyPlanError, match="duplicate"):
        validate(HeavyPlan.from_dict(d))
    with pytest.raises(HeavyPlanError, match="planVersion"):
        HeavyPlan.from_dict({**BASE, "planVersion": 99})


def test_concurrent_subtasks_must_not_share_paths_but_ordered_ones_may():
    d = copy.deepcopy(BASE)
    d["subtasks"][1]["ownedPaths"] = ["src/server/api/"]
    with pytest.raises(HeavyPlanError, match="concurrently"):
        validate(HeavyPlan.from_dict(d))
    d = copy.deepcopy(BASE)
    d["subtasks"][2]["ownedPaths"] = ["src/server/"]  # C runs after A, so sharing is safe
    validate(HeavyPlan.from_dict(d))


def test_path_escape_and_missing_ownership_rejected():
    d = copy.deepcopy(BASE)
    d["subtasks"][0]["ownedPaths"] = ["../outside"]
    with pytest.raises(HeavyPlanError, match="inside the workspace"):
        validate(HeavyPlan.from_dict(d))
    d = copy.deepcopy(BASE)
    d["subtasks"][0]["ownedPaths"] = []
    with pytest.raises(HeavyPlanError, match="ownership"):
        validate(HeavyPlan.from_dict(d))


def test_ready_subtasks_wait_for_accepted_dependencies():
    p = plan()
    assert ready_subtasks(p, accepted=[]) == ["A", "B"]
    assert ready_subtasks(p, accepted=["A"], running=["B"]) == []  # C needs B accepted too
    assert ready_subtasks(p, accepted=["A", "B"]) == ["C"]
    assert ready_subtasks(p, accepted=["A"], failed=["B"]) == []


def test_split_only_when_it_beats_sequential():
    assert worth_splitting(plan(), per_child_overhead_seconds=10)["split"] is True
    seq = copy.deepcopy(BASE)
    seq["subtasks"][1]["dependsOn"] = ["A"]
    seq["subtasks"][2]["dependsOn"] = ["B"]
    out = worth_splitting(HeavyPlan.from_dict(seq), per_child_overhead_seconds=30)
    assert out["split"] is False and "sequentially" in out["reason"]


def test_spawn_gate_never_exceeds_normal_capacity_or_uses_pi_reserve():
    p = plan()
    gate = SpawnGate(normal_capacity=3, active_normal_agents=2)  # the parent + one normal worker
    assert gate.decide(p, "A")["allow"] is True
    denied = gate.decide(p, "B")
    assert denied["allow"] is False and denied["degraded"] == "sequential" and "PI reserve" in denied["reason"]


def test_spawn_gate_is_idempotent_and_respects_dependencies_depth_and_deadline():
    p = plan()
    gate = SpawnGate(normal_capacity=5, active_normal_agents=0)
    assert gate.decide(p, "A")["allow"] is True
    dup = gate.decide(p, "A")
    assert dup["allow"] is False and dup["idempotent"] is True
    assert gate.decide(p, "C", accepted=["A"])["allow"] is False
    assert gate.decide(p, "B", depth=3)["allow"] is False
    assert gate.decide(p, "B", deadline_remaining=10)["allow"] is False
    assert gate.decide(p, "C", accepted=["A", "B"])["allow"] is True


def test_slot_released_only_when_child_confirmed_stopped():
    p = plan()
    gate = SpawnGate(normal_capacity=1, active_normal_agents=0)
    assert gate.decide(p, "A")["allow"]
    assert gate.release(p, "A", confirmed_stopped=False) is False  # result file alone is not enough
    assert gate.decide(p, "B")["allow"] is False
    assert gate.release(p, "A", confirmed_stopped=True) is True
    assert gate.decide(p, "B")["allow"] is True


def test_child_diff_outside_owned_paths_is_reported():
    p = plan()
    assert check_child_diff(p, "A", ["src/server/x.py", "src/server/sub/y.py"]) == []
    assert check_child_diff(p, "A", ["src/server/x.py", "src/components/Button.tsx"]) == ["src/components/Button.tsx"]
