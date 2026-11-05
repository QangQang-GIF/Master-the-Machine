"""V2 Heavy execution plan: versioned DAG contract, validation, critical path, Root spawn gate.

Depth is fixed at Root -> Heavy Parent -> Child. The parent plans; Root spawns and owns global slots.
Nothing here spawns a process: `SpawnGate` only decides whether Root may spawn, so the decision is
testable without the native runtime (whose spawn/status behaviour is still unverified).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set

PLAN_VERSION = 1
MAX_DEPTH = 2  # parent depth 1, child depth 2; no deeper level is ever created


class HeavyPlanError(Exception):
    pass


@dataclass
class Subtask:
    subtaskId: str
    description: str
    dependsOn: List[str]
    ownedPaths: List[str]
    expectedOutputs: List[str]
    estimatedSeconds: float
    modelProfile: str = "standard"
    reasoningEffort: str = "medium"


@dataclass
class HeavyPlan:
    parentBacklogItemKey: str
    parentAgentId: str
    baseRevision: str
    deadlineEpoch: float
    subtasks: List[Subtask]
    planVersion: int = PLAN_VERSION

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "HeavyPlan":
        if d.get("planVersion") != PLAN_VERSION:
            raise HeavyPlanError(f"unsupported planVersion {d.get('planVersion')!r}")
        try:
            subs = [Subtask(
                subtaskId=str(s["subtaskId"]), description=str(s["description"]),
                dependsOn=list(s.get("dependsOn", [])), ownedPaths=list(s["ownedPaths"]),
                expectedOutputs=list(s.get("expectedOutputs", [])), estimatedSeconds=float(s["estimatedSeconds"]),
                modelProfile=str(s.get("modelProfile", "standard")), reasoningEffort=str(s.get("reasoningEffort", "medium")),
            ) for s in d["subtasks"]]
            return HeavyPlan(str(d["parentBacklogItemKey"]), str(d["parentAgentId"]), str(d["baseRevision"]),
                             float(d["deadlineEpoch"]), subs)
        except (KeyError, TypeError, ValueError) as exc:
            raise HeavyPlanError(f"malformed plan: {exc}") from exc


def _norm(path: str) -> str:
    return path.replace("\\", "/").strip("/") + "/"


def _paths_overlap(a: Iterable[str], b: Iterable[str]) -> Optional[str]:
    for x in map(_norm, a):
        for y in map(_norm, b):
            if x.startswith(y) or y.startswith(x):
                return x
    return None


def validate(plan: HeavyPlan) -> None:
    """Raise HeavyPlanError unless the DAG is safe to execute."""
    if not plan.subtasks:
        raise HeavyPlanError("plan has no subtasks")
    ids = [s.subtaskId for s in plan.subtasks]
    if len(set(ids)) != len(ids):
        raise HeavyPlanError("duplicate subtaskId")
    known = set(ids)
    for s in plan.subtasks:
        if not s.ownedPaths:
            raise HeavyPlanError(f"{s.subtaskId}: file ownership must be explicit")
        if any(".." in p.replace("\\", "/").split("/") or p.startswith(("/", "\\")) for p in s.ownedPaths):
            raise HeavyPlanError(f"{s.subtaskId}: ownedPaths must stay inside the workspace")
        if s.estimatedSeconds <= 0:
            raise HeavyPlanError(f"{s.subtaskId}: estimatedSeconds must be positive")
        bad = [d for d in s.dependsOn if d not in known or d == s.subtaskId]
        if bad:
            raise HeavyPlanError(f"{s.subtaskId}: invalid dependency {bad}")
    _assert_acyclic(plan)
    # Subtasks that may run at the same time must not own overlapping paths.
    for i, a in enumerate(plan.subtasks):
        for b in plan.subtasks[i + 1:]:
            if not _ordered(plan, a.subtaskId, b.subtaskId):
                hit = _paths_overlap(a.ownedPaths, b.ownedPaths)
                if hit:
                    raise HeavyPlanError(f"{a.subtaskId} and {b.subtaskId} can run concurrently but both own {hit}")


def _deps(plan: HeavyPlan) -> Dict[str, Set[str]]:
    return {s.subtaskId: set(s.dependsOn) for s in plan.subtasks}


def _assert_acyclic(plan: HeavyPlan) -> None:
    deps, state = _deps(plan), {}

    def visit(n: str) -> None:
        if state.get(n) == 1:
            raise HeavyPlanError("dependency cycle detected")
        if state.get(n) == 2:
            return
        state[n] = 1
        for d in deps[n]:
            visit(d)
        state[n] = 2
    for n in deps:
        visit(n)


def _ancestors(plan: HeavyPlan, node: str) -> Set[str]:
    deps, out, stack = _deps(plan), set(), [node]
    while stack:
        for d in deps[stack.pop()]:
            if d not in out:
                out.add(d)
                stack.append(d)
    return out


def _ordered(plan: HeavyPlan, a: str, b: str) -> bool:
    return a in _ancestors(plan, b) or b in _ancestors(plan, a)


def critical_path(plan: HeavyPlan, done: Iterable[str] = (), overrides: Optional[Dict[str, float]] = None) -> Dict[str, Any]:
    """Longest remaining dependency chain. Recomputed whenever a child is late, fails or completes."""
    validate(plan)
    done_set, est = set(done), {s.subtaskId: float((overrides or {}).get(s.subtaskId, s.estimatedSeconds)) for s in plan.subtasks}
    deps, memo = _deps(plan), {}

    def length(n: str) -> float:
        if n in done_set:
            return 0.0
        if n not in memo:
            memo[n] = est[n] + max((length(d) for d in deps[n]), default=0.0)
        return memo[n]
    remaining = [s.subtaskId for s in plan.subtasks if s.subtaskId not in done_set]
    if not remaining:
        return {"seconds": 0.0, "path": []}
    end = max(remaining, key=length)
    path, cur = [], end
    while cur not in done_set:
        path.append(cur)
        nxt = [d for d in deps[cur] if d not in done_set]
        if not nxt:
            break
        cur = max(nxt, key=length)
    return {"seconds": length(end), "path": list(reversed(path))}


def ready_subtasks(plan: HeavyPlan, accepted: Iterable[str], running: Iterable[str] = (), failed: Iterable[str] = ()) -> List[str]:
    """Subtasks whose dependencies are all *accepted* and that are not already running/accepted/failed."""
    acc, run, bad = set(accepted), set(running), set(failed)
    return [s.subtaskId for s in plan.subtasks
            if s.subtaskId not in acc | run | bad and set(s.dependsOn) <= acc]


def worth_splitting(plan: HeavyPlan, per_child_overhead_seconds: float = 30.0, max_parallel: int = 2) -> Dict[str, Any]:
    """Split only when estimated parallel time (incl. overhead) beats sequential time."""
    validate(plan)
    sequential = sum(s.estimatedSeconds for s in plan.subtasks)
    cp = critical_path(plan)["seconds"]
    width = max(1, min(max_parallel, len(plan.subtasks)))
    # Lower bound of a width-limited schedule: max(critical path, total work / width), plus overhead.
    parallel = max(cp, sequential / width) + per_child_overhead_seconds * len(plan.subtasks)
    return {"split": parallel < sequential, "sequentialSeconds": sequential, "parallelSeconds": parallel,
            "criticalPathSeconds": cp, "reason": "parallel estimate beats sequential" if parallel < sequential
            else "overhead exceeds parallel benefit; run sequentially"}


@dataclass
class SpawnGate:
    """Root-side admission control for child spawn requests (Plan 8.4). No process is started here."""
    normal_capacity: int          # slots usable by Normal/Heavy work (never the PI reserve)
    active_normal_agents: int     # running Normal workers + Heavy parents + children
    spawned: Set[str] = field(default_factory=set)  # "<parentKey>/<subtaskId>"

    def decide(self, plan: HeavyPlan, subtask_id: str, depth: int = 2, deadline_remaining: Optional[float] = None,
               accepted: Iterable[str] = (), pi_waiting: bool = False) -> Dict[str, Any]:
        sub = next((s for s in plan.subtasks if s.subtaskId == subtask_id), None)
        ident = f"{plan.parentBacklogItemKey}/{subtask_id}"
        if sub is None:
            return {"allow": False, "reason": "unknown subtask"}
        if depth > MAX_DEPTH:
            return {"allow": False, "reason": f"depth {depth} exceeds maximum {MAX_DEPTH}"}
        if ident in self.spawned:
            return {"allow": False, "reason": "duplicate spawn request", "idempotent": True}
        if not set(sub.dependsOn) <= set(accepted):
            return {"allow": False, "reason": "dependencies not accepted yet"}
        if deadline_remaining is not None and deadline_remaining < sub.estimatedSeconds:
            return {"allow": False, "reason": "cannot finish before the deadline"}
        if self.active_normal_agents >= self.normal_capacity:
            return {"allow": False, "reason": "no normal slot free (PI reserve is not available to children)",
                    "degraded": "sequential"}
        self.spawned.add(ident)
        self.active_normal_agents += 1
        return {"allow": True, "reason": "admitted", "childId": f"child-{plan.parentBacklogItemKey}-{subtask_id}",
                "worktree": f"{plan.parentBacklogItemKey}-{subtask_id}", "baseRevision": plan.baseRevision}

    def release(self, plan: HeavyPlan, subtask_id: str, confirmed_stopped: bool) -> bool:
        """A slot is released only after the agent is confirmed stopped (never on a result file alone)."""
        if not confirmed_stopped:
            return False
        if f"{plan.parentBacklogItemKey}/{subtask_id}" in self.spawned and self.active_normal_agents > 0:
            self.active_normal_agents -= 1
            return True
        return False


def check_child_diff(plan: HeavyPlan, subtask_id: str, changed_files: Iterable[str]) -> List[str]:
    """Files a child changed outside its owned paths (parent must reject the merge if non-empty)."""
    sub = next(s for s in plan.subtasks if s.subtaskId == subtask_id)
    owned = [_norm(p) for p in sub.ownedPaths]
    return [f for f in changed_files if not any((_norm(f)).startswith(o) or _norm(f) == o for o in owned)]
