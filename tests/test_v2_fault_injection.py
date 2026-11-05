"""V2 Phase 6 fault injection: races, repeated dispatch, crash + requeue."""
import subprocess
import threading

from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator


def _coord(tmp_path, items, **kw):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    opts = dict(max_workers=5, normal_workers=4, reserved_workers=1)
    opts.update(kw)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", **opts)
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400, "items": items})
    return c


def test_concurrent_claims_of_one_task_allow_exactly_one_winner(tmp_path):
    c = _coord(tmp_path, [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}])
    c.register_started("BUG-01", {})
    results, errors = [], []

    def go(i):
        try:
            results.append(c.claim_task("BUG-01", f"w{i}"))
        except NativeCoordinationError as exc:
            errors.append(str(exc))

    threads = [threading.Thread(target=go, args=(i,)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(results) == 1 and len(errors) == 5  # no duplicate agent for one item
    assert c.task_details("BUG-01")["phase"] == "RUNNING"


def test_one_worker_cannot_hold_two_tasks_under_concurrency(tmp_path):
    items = [{"key": f"BUG-0{i}", "type": "BUG", "status": "OPEN", "points": 10} for i in (1, 2, 3)]
    c = _coord(tmp_path, items)
    for i in (1, 2, 3):
        c.register_started(f"BUG-0{i}", {})
    wins = []

    def go(key):
        try:
            c.claim_task(key, "same-worker")
            wins.append(key)
        except NativeCoordinationError:
            pass

    ts = [threading.Thread(target=go, args=(f"BUG-0{i}",)) for i in (1, 2, 3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(wins) == 1


def test_concurrent_claims_never_exceed_total_capacity(tmp_path):
    items = [{"key": f"BUG-{i:02d}", "type": "BUG", "status": "OPEN", "points": 10} for i in range(1, 9)]
    c = _coord(tmp_path, items)
    for it in items:
        c.register_started(it["key"], {})
    ok = []

    def go(i, key):
        try:
            c.claim_task(key, f"w{i}")
            ok.append(key)
        except NativeCoordinationError:
            pass

    ts = [threading.Thread(target=go, args=(i, it["key"])) for i, it in enumerate(items)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) <= 4  # normal lane capacity (4); the PI reserve is never used by BUG work
    assert len(c.plan_tasks()["active"]) == len(ok)


def test_worker_crash_then_requeue_frees_slot_and_uses_a_fresh_attempt_path(tmp_path):
    c = _coord(tmp_path, [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}])
    c.register_started("BUG-01", {})
    first = c.claim_task("BUG-01", "w1")
    before = c.plan_tasks()["availableSlots"]
    c.reconcile_worker("BUG-01", confirmed_stopped=True, reason="worker crashed", requeue=True)
    assert c.plan_tasks()["availableSlots"] == before + 1
    second = c.claim_task("BUG-01", "w2")
    assert second["worktreeRoot"] != first["worktreeRoot"] and "attempt-2" in second["worktreeRoot"]


def test_reconcile_without_confirmed_stop_is_refused(tmp_path):
    c = _coord(tmp_path, [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}])
    c.register_started("BUG-01", {})
    c.claim_task("BUG-01", "w1")
    try:
        c.reconcile_worker("BUG-01", confirmed_stopped=False, reason="timeout", requeue=True)
        raise AssertionError("must not free a slot for a worker that may still be writing")
    except NativeCoordinationError:
        pass
    assert c.task_details("BUG-01")["phase"] == "RUNNING"


def test_dispatch_ready_is_idempotent_and_does_not_double_spawn(tmp_path):
    from tests.test_dispatch_finalize import build, item
    s = build(tmp_path, [item("BUG-01"), item("PI-01")])
    first = s.dispatch_ready()["spawn"]
    second = s.dispatch_ready()["spawn"]
    assert {e["backlogItemKey"] for e in first} == {"BUG-01", "PI-01"}
    assert second == []  # already running -> nothing new is spawned
