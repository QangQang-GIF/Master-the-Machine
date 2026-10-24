"""Reliability / latency benchmark for the MTM competition runtime.

Measures (wall-clock, this machine) the paths that matter during a live match:

* process-tree kill latency (parent + grandchild)
* WorkerSupervisor preempt / timeout quiescence latency (cooperative worker + real subprocess tree)
* un-quiesced (hung) worker handling: bounded quiesce time + pending-cleanup release
* CodexRuntime mid-communicate cancellation latency (shim CLI)
* TournamentScheduler.select_next_task throughput with N queued tasks
* TaskStore write throughput and crash-recovery (`reconcile_orphaned_tasks`) time
* atomic TASK.md guarded-write cost

Usage:  python scripts/bench_reliability.py [--json out.json] [--repeat N]
Exit code is non-zero if any budget in BUDGETS_MS is exceeded (so it can gate CI / release).
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from mtm_core.contracts import TaskEnvelope, TaskState, WorkerResult, WorkerStatus  # noqa: E402
from mtm_core.runtime import ExecutionTrace  # noqa: E402
from mtm_core.runtime.codex import CodexRuntime  # noqa: E402
from mtm_core.runtime.process import is_pid_alive, terminate_process_tree  # noqa: E402
from mtm_competition.scheduling.scheduler import TournamentScheduler  # noqa: E402
from mtm_competition.tasks.store import TaskStore  # noqa: E402
from mtm_competition.workers.launcher import _write_task_file_guarded  # noqa: E402
from mtm_competition.workers.supervisor import WorkerSupervisor  # noqa: E402

# p95 latency budgets (milliseconds). Generous on purpose: they catch regressions, not noise.
BUDGETS_MS = {
    "process_tree_kill": 3000,
    "supervisor_preempt_cooperative": 1500,
    "supervisor_preempt_real_tree": 4000,
    "supervisor_hung_quiesce_bounded": 1500,
    "runtime_mid_communicate_cancel": 3000,
    "scheduler_select_next_task_1000": 1500,
    "store_add_task_per_task": 5,
    "store_reconcile_100_orphans": 1000,
    "task_md_guarded_write": 50,
}

_TREE_CODE = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
    "time.sleep(60)\n"
)


def _ms(t0: float) -> float:
    return (time.perf_counter() - t0) * 1000.0


def _env(task_id: str, tmp: Path, budget: int = 60) -> TaskEnvelope:
    return TaskEnvelope(
        task_id=task_id, task_type="CR", title="b", description="b",
        repository_path=str(tmp), time_budget_seconds=budget, points=100,
    )


def bench_process_tree_kill(tmp: Path) -> float:
    proc = subprocess.Popen([sys.executable, "-c", _TREE_CODE], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, start_new_session=True)
    time.sleep(0.3)
    t0 = time.perf_counter()
    terminate_process_tree(proc)
    dt = _ms(t0)
    assert proc.poll() is not None and not is_pid_alive(proc.pid)
    return dt


def bench_supervisor_preempt_cooperative(tmp: Path) -> float:
    sup = WorkerSupervisor(max_workers=1)
    started = threading.Event()

    def worker(env, attempt_number=1, on_process_start=None, cancel_event=None):
        started.set()
        while not (cancel_event and cancel_event.is_set()):
            time.sleep(0.005)
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.TIMED_OUT), ExecutionTrace(), tmp

    sup.launch_async(worker, _env("BENCH-COOP", tmp))
    started.wait(3)
    t0 = time.perf_counter()
    proc = sup.preempt_worker("BENCH-COOP")
    dt = _ms(t0)
    assert proc is not None and proc.quiesced
    sup.shutdown_all()
    return dt


def bench_supervisor_preempt_real_tree(tmp: Path) -> float:
    sup = WorkerSupervisor(max_workers=1)
    started = threading.Event()

    def worker(env, attempt_number=1, on_process_start=None, cancel_event=None):
        p = subprocess.Popen([sys.executable, "-c", _TREE_CODE], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        if on_process_start:
            on_process_start(p)
        started.set()
        try:
            p.communicate(timeout=60)
        except Exception:
            pass
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.TIMED_OUT), ExecutionTrace(), tmp

    sup.launch_async(worker, _env("BENCH-TREE", tmp))
    started.wait(3)
    time.sleep(0.3)
    t0 = time.perf_counter()
    proc = sup.preempt_worker("BENCH-TREE")
    dt = _ms(t0)
    assert proc is not None and proc.quiesced
    sup.shutdown_all()
    return dt


def bench_supervisor_hung_quiesce_bounded(tmp: Path) -> float:
    sup = WorkerSupervisor(max_workers=1, quiesce_timeout=0.5)
    release, started = threading.Event(), threading.Event()

    def hung(env, attempt_number=1, on_process_start=None, cancel_event=None):
        started.set()
        release.wait(30)
        return WorkerResult(task_id=env.task_id, status=WorkerStatus.TIMED_OUT), ExecutionTrace(), tmp

    sup.launch_async(hung, _env("BENCH-HUNG", tmp))
    started.wait(3)
    t0 = time.perf_counter()
    proc = sup.preempt_worker("BENCH-HUNG")
    dt = _ms(t0)
    assert proc is not None and proc.quiesced is False and sup.has_pending_cleanup("BENCH-HUNG")
    release.set()
    deadline = time.monotonic() + 3
    while sup.has_pending_cleanup("BENCH-HUNG") and time.monotonic() < deadline:
        sup.reap_pending_cleanup()
        time.sleep(0.01)
    assert not sup.has_pending_cleanup("BENCH-HUNG")
    sup.shutdown_all()
    return dt


def bench_runtime_mid_communicate_cancel(tmp: Path) -> float:
    bin_dir = tmp / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "sleeper.py"
    script.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    if sys.platform == "win32":
        (bin_dir / "codex.cmd").write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        w = bin_dir / "codex"
        w.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{script}" "$@"\n', encoding="utf-8")
        w.chmod(0o755)
    old_path = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{bin_dir}{os.pathsep}{old_path}"
    try:
        rt = CodexRuntime(default_backend="codex")
        cancel = threading.Event()
        t_cancel = {}

        def on_start(_p):
            def _fire():
                time.sleep(0.15)
                t_cancel["t"] = time.perf_counter()
                cancel.set()
            threading.Thread(target=_fire, daemon=True).start()

        res = rt.run(workspace=tmp / "ws", prompt="x", timeout=60, on_process_start=on_start, cancel_event=cancel)
        dt = (time.perf_counter() - t_cancel["t"]) * 1000.0
        assert res.timed_out and res.exit_code == -1
        return dt
    finally:
        os.environ["PATH"] = old_path


def bench_scheduler_select(tmp: Path, n: int = 1000) -> float:
    store = TaskStore(":memory:")
    for i in range(n):
        store.add_task(TaskEnvelope(
            task_id=f"T-{i}", task_type=("PI" if i % 50 == 0 else "BUG"), title="t", description="d",
            repository_path=str(tmp), points=100 + (i % 400),
        ))
    sched = TournamentScheduler(store, concurrency_limit=1)
    t0 = time.perf_counter()
    nxt = sched.select_next_task()
    dt = _ms(t0)
    assert nxt is not None
    store.close()
    return dt


def bench_store_add(tmp: Path, n: int = 300) -> float:
    store = TaskStore(str(tmp / "bench_add.db"))
    t0 = time.perf_counter()
    for i in range(n):
        store.add_task(TaskEnvelope(task_id=f"A-{i}", task_type="BUG", title="t", description="d",
                                    repository_path=str(tmp), points=100))
    dt = _ms(t0) / n
    store.close()
    return dt


def bench_store_reconcile(tmp: Path, n: int = 100) -> float:
    store = TaskStore(str(tmp / "bench_rec.db"))
    for i in range(n):
        tid = f"R-{i}"
        store.add_task(TaskEnvelope(task_id=tid, task_type="BUG", title="t", description="d",
                                    repository_path=str(tmp), points=100))
        store.update_task_status(tid, TaskState.RUNNING)
    t0 = time.perf_counter()
    out = store.reconcile_orphaned_tasks()
    dt = _ms(t0)
    assert len(out) == n
    store.close()
    return dt


def bench_task_md_write(tmp: Path) -> float:
    target = tmp / "TASK.md"
    t0 = time.perf_counter()
    assert _write_task_file_guarded(target, "x" * 20_000, threading.Event())
    return _ms(t0)


BENCHES = [
    ("process_tree_kill", bench_process_tree_kill),
    ("supervisor_preempt_cooperative", bench_supervisor_preempt_cooperative),
    ("supervisor_preempt_real_tree", bench_supervisor_preempt_real_tree),
    ("supervisor_hung_quiesce_bounded", bench_supervisor_hung_quiesce_bounded),
    ("runtime_mid_communicate_cancel", bench_runtime_mid_communicate_cancel),
    ("scheduler_select_next_task_1000", bench_scheduler_select),
    ("store_add_task_per_task", bench_store_add),
    ("store_reconcile_100_orphans", bench_store_reconcile),
    ("task_md_guarded_write", bench_task_md_write),
]


def _p95(values: list[float]) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, int(round(0.95 * (len(s) - 1))))]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=5)
    ap.add_argument("--json", type=str, default="")
    args = ap.parse_args()

    results = {}
    failures = []
    for name, fn in BENCHES:
        samples = []
        for _ in range(args.repeat):
            with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
                samples.append(fn(Path(d)))
        row = {
            "n": len(samples),
            "min_ms": round(min(samples), 2),
            "median_ms": round(statistics.median(samples), 2),
            "p95_ms": round(_p95(samples), 2),
            "max_ms": round(max(samples), 2),
            "budget_p95_ms": BUDGETS_MS[name],
        }
        row["ok"] = row["p95_ms"] <= BUDGETS_MS[name]
        results[name] = row
        if not row["ok"]:
            failures.append(name)
        print(f"{'OK  ' if row['ok'] else 'FAIL'} {name:36s} median={row['median_ms']:9.2f}ms "
              f"p95={row['p95_ms']:9.2f}ms budget={BUDGETS_MS[name]}ms")

    if args.json:
        Path(args.json).write_text(json.dumps(results, indent=2), encoding="utf-8")
    if failures:
        print("BUDGET EXCEEDED:", ", ".join(failures))
        return 1
    print("All benchmark budgets satisfied.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
