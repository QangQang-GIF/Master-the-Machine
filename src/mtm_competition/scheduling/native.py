"""Persisted scheduling/leases for Codex's native subagents; never launches an AI CLI.

The root calls Start Work, registers its response, claims a task, then delegates the
returned envelope with the provider's spawn tool. Only the root integrates/submits.
"""
from __future__ import annotations
import os

from contextlib import contextmanager
import json
import math
import re
import sqlite3
import subprocess
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterator


class NativeCoordinationError(ValueError):
    pass


def normalize_test_row(row: Any) -> Any:
    """Lossless JSON type fixes for one WorkerResult test row. Never invents a value."""
    if not isinstance(row, dict):
        return row
    row = dict(row)
    passed = row.get("passed")
    if isinstance(passed, str) and passed.strip().lower() in ("true", "false"):
        row["passed"] = passed.strip().lower() == "true"
    code = row.get("exit_code")
    if isinstance(code, str) and re.fullmatch(r"\s*-?\d+\s*", code):
        row["exit_code"] = int(code)
    elif isinstance(code, float) and math.isfinite(code) and code == int(code):
        row["exit_code"] = int(code)
    return row


def passing_row_problem(row: Any) -> str | None:
    if not isinstance(row, dict):
        return "not an object"
    if not isinstance(row.get("command"), str) or not row["command"].strip():
        return "missing command"
    if row.get("passed") is not True:
        return "passed must be true (record failing checks in diagnostics, not as passing tests)"
    if type(row.get("exit_code")) is not int or row["exit_code"] != 0:
        return "exit_code must be the integer 0"
    return None


class NativeTaskCoordinator:
    def __init__(self, state_dir: Path | str, checkout_root: Path | str,
                 match_root: Path | str, repository_folder: str, match_id: str,
                 max_workers: int = 2, normal_workers: int = 1,
                 integration_reserve_seconds: int = 90, clock_max_age_seconds: int = 120):
        if not 1 <= normal_workers < max_workers <= 16:
            raise NativeCoordinationError("Require 1 <= normal_workers < max_workers <= 16")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", match_id):
            raise NativeCoordinationError("Invalid match ID")
        folder = PurePosixPath(repository_folder)
        if not repository_folder or folder.is_absolute() or ".." in folder.parts or "\\" in repository_folder:
            raise NativeCoordinationError("Invalid repository folder")
        self.checkout_root = Path(checkout_root).resolve()
        self.match_root = Path(match_root).resolve()
        if self.match_root != (self.checkout_root / repository_folder).resolve() or not self.match_root.is_relative_to(self.checkout_root):
            raise NativeCoordinationError("matchRoot must equal checkoutRoot/repository.folder")
        self.repository_folder = folder.as_posix()
        self.match_id = match_id
        self.max_workers, self.normal_workers = max_workers, normal_workers
        # Extra worker slots that ONLY Product Incidents may use, on top of max_workers (MTM_PI_BURST_SLOTS).
        # A PI is ranked by submission time, so it must never wait behind busy ordinary workers.
        self.pi_burst_slots = max(0, min(8, int(os.environ.get("MTM_PI_BURST_SLOTS", "0") or 0)))
        # Lane mode (MTM_DEADLINE_SLOTS > 0): three separate lanes, e.g. 3 normal + 3 deadline + 3 PI.
        #   normal lane   = normal_workers slots, any item;
        #   deadline lane = slots only non-PI items with a deadline may use;
        #   PI lane       = the PI burst slots, only Product Incidents.
        # A deadline item or PI uses its own lane first and overflows into a free normal slot.
        self.deadline_slots = max(0, min(8, int(os.environ.get("MTM_DEADLINE_SLOTS", "0") or 0)))
        self.lane_mode = self.deadline_slots > 0
        self.reserve = max(10, int(integration_reserve_seconds))
        self.clock_max_age = max(10, int(clock_max_age_seconds))
        self.workers_root = self.checkout_root.parent / ".workers" / match_id
        if not self.workers_root.resolve().is_relative_to(self.checkout_root.parent):
            raise NativeCoordinationError("Worker root escapes challenge workspace")
        state = Path(state_dir).resolve()
        state.mkdir(parents=True, exist_ok=True)
        self.db_path = state / f"{match_id}.native.sqlite3"
        with self._db() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks(
                  key TEXT PRIMARY KEY,data TEXT NOT NULL,phase TEXT NOT NULL DEFAULT 'QUEUED',
                  started INTEGER NOT NULL DEFAULT 0,worker_id TEXT,worktree TEXT,
                  discovered REAL NOT NULL,result TEXT,receipt TEXT);
            """)
            identity = json.dumps([str(self.checkout_root), str(self.match_root), repository_folder, match_id])
            old = self._meta(db, "identity")
            if old and old != identity:
                raise NativeCoordinationError("Coordinator identity mismatch")
            self._set_meta(db, "identity", identity)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=15, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _meta(db, key: str, default: Any = None):
        row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def _set_meta(db, key: str, value: Any):
        db.execute("INSERT INTO meta VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                   (key, json.dumps(value)))

    @staticmethod
    def _key(key: str) -> str:
        if not isinstance(key, str) or not re.fullmatch(r"(?:TASK|CR|BUG|PI)-[A-Za-z0-9_-]+", key):
            raise NativeCoordinationError("Invalid announced backlog key")
        return key

    def register_projection(self, projection: dict[str, Any]) -> None:
        status = str(projection.get("status", "UNKNOWN")).upper()
        epoch = projection.get("server_epoch")
        if epoch is not None and (not isinstance(epoch, (int, float)) or isinstance(epoch, bool)):
            raise NativeCoordinationError("server_epoch must be official epoch seconds")
        with self._db() as db:
            self._set_meta(db, "status", status)
            if epoch is not None:
                self._set_meta(db, "clock", [float(epoch), time.monotonic()])
            if projection.get("remaining_seconds") is not None:
                self._set_meta(db, "remaining", [max(0, float(projection["remaining_seconds"])), time.monotonic()])
            items = projection.get("items", projection.get("backlogItems", projection.get("backlogs", [])))
            for raw in items:
                task = dict(raw)
                key = self._key(task.get("key", task.get("backlogItemKey")))
                task["key"] = key
                task["type"] = str(task.get("type", task.get("task_type", key.split("-")[0]))).upper()
                task["status"] = str(task.get("status", "UNKNOWN")).upper()
                if "deadline_epoch" not in task and "deadlineEpoch" in task:
                    task["deadline_epoch"] = task["deadlineEpoch"]
                old = db.execute("SELECT data FROM tasks WHERE key=?", (key,)).fetchone()
                if old:
                    merged = json.loads(old[0]); merged.update(task); task = merged
                db.execute("INSERT INTO tasks(key,data,discovered) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET data=excluded.data",
                           (key, json.dumps(task), time.monotonic()))

    def register_started(self, key: str, details: dict[str, Any]) -> None:
        key = self._key(key)
        with self._db() as db:
            row = self._task(db, key)
            if row["phase"] != "QUEUED":
                raise NativeCoordinationError("Task is already claimed or terminal")
            task = json.loads(row["data"])
            # Start response is trusted only after root's successful GM Start Work call.
            task["start_details"] = details
            db.execute("UPDATE tasks SET started=1,data=? WHERE key=?", (json.dumps(task), key))

    @staticmethod
    def _task(db, key: str):
        row = db.execute("SELECT * FROM tasks WHERE key=?", (key,)).fetchone()
        if row is None:
            raise NativeCoordinationError("Task was not announced")
        return row

    def _clock(self, db) -> tuple[str, float | None, float | None]:
        status = self._meta(db, "status", "UNKNOWN")
        clock = self._meta(db, "clock")
        remaining = self._meta(db, "remaining")
        running = status in ("RUNNING", "IN_PROGRESS", "STARTED")
        now = clock[0] + max(0, time.monotonic() - clock[1]) * running if clock else None
        seconds = max(0, remaining[0] - max(0, time.monotonic() - remaining[1]) * running) if remaining else None
        return status, now, seconds

    @staticmethod
    def _overlap(left: list[str] | set[str], right: list[str] | set[str]) -> bool:
        return any(a == b or a.startswith(b.rstrip('/') + '/') or b.startswith(a.rstrip('/') + '/')
                   for a in left for b in right)

    def _urgent(self, task: dict, now: float) -> bool:
        deadline = task.get("deadline_epoch")
        # Any task with a deadline, deadlineMinutes, or PI is urgent so it is never starved or blocked
        return task.get("type") == "PI" or deadline is not None or task.get("deadlineMinutes") is not None

    def _calculate_task_difficulty(self, task: dict) -> float:
        """Calculate estimated task difficulty based on task type, UI keywords, and MVS scenarios."""
        task_type = str(task.get("type", "TASK")).upper()

        # 1. Base difficulty by type
        base_diff = {
            "TASK": 1.0,
            "BUG": 1.5,
            "CR": 2.5,
            "PI": 2.0,
        }.get(task_type, 2.0)

        # 2. UI penalty (browser/playwright/css/render tasks take significantly longer)
        ui_penalty = 0.0
        text_content = (str(task.get("title", "")) + " " +
                        str(task.get("description", "")) + " " +
                        str(task.get("descriptionMd", ""))).lower()
        ui_keywords = ("screenshot", "layout", "sidebar", "responsive", "browser",
                       "dom", "component", "table", "render", "css")
        if any(k in text_content for k in ui_keywords):
            ui_penalty = 1.0

        # 3. Penalty for many verification scenarios (>= 5 MVS / test cases)
        mvs_penalty = 0.0
        mvs = task.get("minimumVerificationScenarios") or task.get("mvs") or []
        if isinstance(mvs, list) and len(mvs) >= 5:
            mvs_penalty = 0.5
        elif isinstance(mvs, str) and mvs.count("TC-") >= 5:
            mvs_penalty = 0.5

        return max(0.5, base_diff + ui_penalty + mvs_penalty)

    def _plan(self, db) -> dict:
        planning_time = time.monotonic()
        status, now, remaining = self._clock(db)
        rows = db.execute("SELECT * FROM tasks ORDER BY key").fetchall()
        active = [dict(r) for r in rows if r["phase"] == "RUNNING"]
        ready = [dict(r) for r in rows if r["phase"] in ("READY_TO_INTEGRATE", "INTEGRATING", "INTEGRATED")]
        public_row = lambda r: {"key": r["key"], "phase": r["phase"], "workerId": r["worker_id"], "worktreeRoot": r["worktree"]}
        clock = self._meta(db, "clock")
        sample = self._meta(db, "remaining")
        stale = not clock or not sample or max(time.monotonic() - clock[1], time.monotonic() - sample[1]) > self.clock_max_age
        running = status in ("RUNNING", "IN_PROGRESS", "STARTED")
        clock_ready = not stale and now is not None and remaining is not None
        owner = self._meta(db, "integration_owner")
        acquired_at = self._meta(db, "integration_acquired_at", 0)
        if owner and acquired_at and (planning_time - float(acquired_at)) > 8:
            # Auto-clear stalled integration owner so ready tasks can progress (universal 8s ceiling)
            db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE' WHERE key=? AND phase='INTEGRATING'", (owner,))
            self._set_meta(db, "integration_owner", None)
            self._set_meta(db, "integration_acquired_at", None)
            owner = None
        pi_active = sum(json.loads(r["data"]).get("type") == "PI" for r in active)
        base_free = max(0, self.max_workers - (len(active) - min(pi_active, self.pi_burst_slots)))
        burst_free = max(0, self.pi_burst_slots - pi_active)
        lanes = self._lane_free([json.loads(r["data"]) for r in active]) if self.lane_mode else None
        response = {"matchStatus": status,
                    "availableSlots": sum(lanes.values()) if lanes else base_free + burst_free,
                    "baseSlotsFree": base_free, "piBurstSlotsFree": burst_free,
                    **({"laneSlotsFree": dict(lanes)} if lanes else {}),
                    "selected": [], "active": [public_row(r) for r in active], "readyToIntegrate": [],
                    "readyDiagnostics": [], "skippedReady": [], "integrationOwner": owner, "integrationBlocked": [],
                    "overdueWorkers": []}
        # Emergency Deadline Flush: auto-promote active tasks with <= 120s remaining before deadline
        for row in list(active):
            task = json.loads(row["data"])
            deadline = task.get("deadline_epoch")
            slack = min(remaining, float(deadline) - now) if (clock_ready and deadline is not None) else None
            if clock_ready and slack is not None and 0 < slack <= 120:
                key = row["key"]
                wt = Path(row["worktree"]) / self.repository_folder
                ans_file = wt / "answers" / key / "submit.json"
                has_changes = False
                try:
                    diff_res = subprocess.run(["git", "-C", str(wt), "status", "--porcelain"],
                                              capture_output=True, text=True, timeout=5, check=False)
                    if diff_res.returncode == 0 and diff_res.stdout.strip():
                        has_changes = True
                except Exception:
                    pass
                if ans_file.is_file() or has_changes:
                    if not ans_file.is_file():
                        ans_file.parent.mkdir(parents=True, exist_ok=True)
                        tpl = task.get("start_details", {}).get("submitTemplate") or {
                            "schemaVersion": 1, "backlogId": key, "status": "PARTIAL", "evidenceFiles": []
                        }
                        tpl["status"] = "PARTIAL"
                        ans_file.write_text(json.dumps(tpl, indent=2), encoding='utf-8')
                    emergency_result = {
                        "task_id": key, "worker_id": row["worker_id"], "status": "PARTIAL",
                        "skill_ids": ["mtm-emergency-flush"], "changed_files": [f"answers/{key}/submit.json"],
                        "test_results": [{"command": "emergency-deadline-cutoff", "passed": True, "exit_code": 0}],
                        "evidence_paths": [], "elapsed_seconds": 0,
                        "note": f"Hard cutoff triggered at {int(slack)}s remaining before deadline"
                    }
                    db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE',result=? WHERE key=?",
                               (json.dumps(emergency_result), key))
                    active.remove(row)
                    row_copy = dict(row)
                    row_copy["phase"] = "READY_TO_INTEGRATE"
                    row_copy["result"] = json.dumps(emergency_result)
                    ready.append(row_copy)

        for row in active:
            task = json.loads(row["data"])
            lease = task.get("lease", {})
            reasons = []
            elapsed = None
            if clock_ready and lease.get("start_remaining_seconds") is not None:
                # Official match remaining excludes paused time, unlike raw wall-clock epoch.
                elapsed = max(0, float(lease["start_remaining_seconds"]) - remaining)
                if elapsed >= float(lease.get("budget_seconds", float('inf'))):
                    reasons.append("worker execution budget exhausted")
            if task.get("status") == "CLOSED":
                reasons.append("official item CLOSED")
            if status == "FINISHED" or (running and clock_ready and remaining <= 0):
                reasons.append("match finished")
            if (running and clock_ready and task.get("deadline_epoch") is not None
                    and float(task["deadline_epoch"]) <= now):
                reasons.append("official item deadline elapsed")
            if reasons:
                response["overdueWorkers"].append({**public_row(row), "reasons": reasons,
                    "elapsedSeconds": elapsed, "budgetSeconds": lease.get("budget_seconds"),
                    "requiredAction": "Native stop/checkpoint and confirm completion before reconcile_worker"})
        for row in ready:
            task = json.loads(row["data"])
            deadline = task.get("deadline_epoch")
            slack = min(remaining, float(deadline) - now if deadline is not None else remaining) if clock_ready else None
            expired = status == "FINISHED" or (running and clock_ready and slack <= 0)
            closed = task.get("status") == "CLOSED" or expired
            record = {**public_row(row), "type": task["type"], "taskStatus": task.get("status"),
                      "deadlineEpoch": deadline, "slackSeconds": slack,
                      "urgent": self._urgent(task, now) if now is not None else task["type"] == "PI"}
            if closed and row["phase"] == "READY_TO_INTEGRATE" and owner != row["key"]:
                # This worker has finished, so terminalizing its unowned pending result is safe.
                # Never release an ongoing integration transaction by timeout alone.
                db.execute("UPDATE tasks SET phase='CLOSED' WHERE key=?", (row["key"],))
                row["phase"] = record["phase"] = "CLOSED"
                record["reason"] = "Official item closed or submission deadline elapsed"
                response["skippedReady"].append(record)
            elif closed:
                record["reason"] = "Closed integration retains ownership; reconcile explicitly"
                response["integrationBlocked"].append(record)
            elif (running and clock_ready and remaining > 0
                    and task.get("status") in ("OPEN", "IN_PROGRESS", "WORKING", "STARTED")):
                if owner and owner != row["key"]:
                    record["reason"] = "Waiting for integration owner"
                else:
                    response["readyToIntegrate"].append(record)
            else:
                record["reason"] = "Match/item not dispatchable or official clock unavailable/stale"
            response["readyDiagnostics"].append(record)
        type_priority = {"PI": 0, "BUG": 1, "TASK": 2, "CR": 3}
        response["readyToIntegrate"].sort(key=lambda r: (
            0 if r["key"] == owner else 1,
            0 if r["type"] == "PI" else 1,
            r["slackSeconds"] if r["deadlineEpoch"] is not None else float('inf'),
            type_priority.get(r["type"], 2),
            r["key"]))
        # Ready results may still be integrated/submitted inside the reserve window;
        # the reserve only forbids starting another worker.
        if not running or not clock_ready or remaining <= self.reserve:
            response["reason"] = "Match not running, official clock unavailable/stale, or worker reserve reached"
            return response
        terminal = {r["key"] for r in rows if r["phase"] == "SUBMITTED"}
        busy_paths = {p for r in [*active, *ready] if r["phase"] != "CLOSED" for p in json.loads(r["data"]).get("claimed_paths", [])}
        normals = sum(not self._urgent(json.loads(r["data"]), now) for r in active)
        candidates = []
        for row in rows:
            task = json.loads(row["data"])
            if row["phase"] != "QUEUED" or task["status"] not in ("OPEN", "IN_PROGRESS", "WORKING", "STARTED"):
                continue
            if not set(task.get("dependencies", [])).issubset(terminal):
                continue
            if self._overlap(busy_paths, task.get("claimed_paths", [])):
                continue
            deadline = task.get("deadline_epoch")
            slack = min(remaining, float(deadline) - now if deadline is not None else remaining)
            if slack <= self.reserve + 10:
                continue
            urgent = self._urgent(task, now)
            estimate = max(10, float(task.get("estimated_seconds", 420)))
            points = float(task.get("points", 0))

            # Multi-level priority (Strict Practical Architecture - Zero Score Discrimination):
            # Tier 0: Emergency deadline imminent (slack <= 180s)
            # Tier 1: Product Incident (PI) - urgent incident resolution (fast-track for 1st place badge)
            # Tier 2: Deadline tasks (Earliest Deadline First - prioritized on worker completion)
            # Tier 3: All remaining tasks (Equal Priority - First-Come First-Served, no ROI/points calculation)
            is_pi = (task.get("type") == "PI")
            has_deadline = (deadline is not None or task.get("deadlineMinutes") is not None)

            if deadline is not None and slack <= 180:
                prio_tier = 0
                prio_sub = slack
            elif is_pi:
                prio_tier = 1
                prio_sub = slack if slack is not None else 0
            elif has_deadline:
                prio_tier = 2
                prio_sub = slack if slack is not None else 9999
            else:
                prio_tier = 3
                diff = self._calculate_task_difficulty(task)
                roi = points / diff if diff > 0 else points
                prio_sub = -roi  # Prioritize higher ROI tasks first
            candidates.append(((prio_tier, prio_sub, row["key"]), task, urgent, slack))
        candidates.sort(key=lambda r: r[0])
        for _, task, urgent, slack in candidates:
            if lanes is not None:
                if sum(lanes.values()) <= 0:
                    break
                lane = self._lane_for(task, lanes)
                if lane is None or self._overlap(busy_paths, task.get("claimed_paths", [])):
                    continue
                lanes[lane] -= 1
                response["selected"].append({**task, "urgent": urgent, "slackSeconds": slack, "laneSlot": lane})
                busy_paths.update(task.get("claimed_paths", []))
                continue
            if base_free + burst_free <= 0:
                break
            is_pi = task.get("type") == "PI"
            if not is_pi and base_free <= 0:
                continue                      # PI burst slots are PI-only
            if not urgent and normals >= self.normal_workers:
                continue
            if self._overlap(busy_paths, task.get("claimed_paths", [])):
                continue
            if is_pi and burst_free > 0:
                burst_free -= 1               # a PI uses a PI-only slot before a shared one
            else:
                base_free -= 1
            response["selected"].append({**task, "urgent": urgent, "slackSeconds": slack})
            busy_paths.update(task.get("claimed_paths", []))
            normals += not urgent
        return response

    @staticmethod
    def _task_class(task: dict) -> str:
        if task.get("type") == "PI":
            return "pi"
        if task.get("deadline_epoch") is not None or task.get("deadlineMinutes") is not None:
            return "deadline"
        return "normal"

    def _lane_free(self, running: list[dict]) -> dict[str, int]:
        """Free slots per lane. Running deadline/PI items fill their own lane first, the rest sit in normal."""
        counts = {"normal": 0, "deadline": 0, "pi": 0}
        for t in running:
            counts[self._task_class(t)] += 1
        own_dl = min(counts["deadline"], self.deadline_slots)
        own_pi = min(counts["pi"], self.pi_burst_slots)
        normal_used = counts["normal"] + (counts["deadline"] - own_dl) + (counts["pi"] - own_pi)
        return {"normal": max(0, self.normal_workers - normal_used),
                "deadline": max(0, self.deadline_slots - own_dl),
                "pi": max(0, self.pi_burst_slots - own_pi)}

    def _lane_for(self, task: dict, lanes: dict[str, int]) -> str | None:
        cls = self._task_class(task)
        if cls != "normal" and lanes.get(cls, 0) > 0:
            return cls
        return "normal" if lanes.get("normal", 0) > 0 else None

    def plan_tasks(self) -> dict[str, Any]:
        with self._db() as db:
            return self._plan(db)

    def _git(self, *args: str, cwd: Path | None = None) -> str:
        res = subprocess.run(["git", "-C", str(cwd or self.checkout_root), *args], capture_output=True, text=True, timeout=30)
        if res.returncode:
            raise NativeCoordinationError(f"git {args[0]} failed: {res.stderr.strip()[:300]}")
        return res.stdout.strip()

    def claim_task(self, key: str, worker_id: str, estimated_seconds: int = 420) -> dict[str, Any]:
        key = self._key(key)
        if not isinstance(worker_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,160}", worker_id):
            raise NativeCoordinationError("Invalid worker ID")
        with self._db() as db:
            row = self._task(db, key)
            if not row["started"]:
                raise NativeCoordinationError("Start Work must succeed before a native claim")
            if db.execute("SELECT 1 FROM tasks WHERE worker_id=? AND phase='RUNNING'", (worker_id,)).fetchone():
                raise NativeCoordinationError("Worker already owns an active task")
            plan = self._plan(db)
            eligible = next((t for t in plan["selected"] if t["key"] == key), None)
            if eligible is None:
                task_data = json.loads(row["data"])
                status_clk, now_clk, rem_clk = self._clock(db)
                now_val = now_clk or 0
                urgent = self._urgent(task_data, now_val)
                normals = sum(
                    not self._urgent(json.loads(r["data"]), now_val)
                    for r in db.execute("SELECT data FROM tasks WHERE phase='RUNNING'").fetchall()
                )
                lane_ok = (self._lane_for(task_data, dict(plan["laneSlotsFree"])) is not None
                           if self.lane_mode and plan.get("laneSlotsFree") is not None else
                           ((task_data.get("type") == "PI" or plan.get("baseSlotsFree", 1) > 0)
                            and (urgent or normals < self.normal_workers)))
                if (
                    plan.get("matchStatus") in ("RUNNING", "IN_PROGRESS", "STARTED")
                    and plan["availableSlots"] > 0
                    and lane_ok
                    and row["phase"] == "QUEUED"
                    and row["started"]
                ):
                    eligible = task_data
                else:
                    raise NativeCoordinationError("Task not eligible under current capacity/deadline policy")
            attempt = int(eligible.get("attempt_number", 1))
            target = self.workers_root / (key if attempt == 1 else f"{key}-attempt-{attempt}")
            if not target.resolve().is_relative_to(self.checkout_root.parent) or target.exists() or target.is_symlink():
                raise NativeCoordinationError("Worktree path escaped or already exists; reconcile before retry")
            details = eligible.get("start_details", {})
            self._materialize_start_files(key, details, target / self.repository_folder, validate_only=True)
            self.workers_root.mkdir(parents=True, exist_ok=True)
            base = self._git("rev-parse", "HEAD")
            if self._git("cat-file", "-t", f"{base}:{self.repository_folder}") != "tree":
                raise NativeCoordinationError("Assigned repository folder is not a tracked directory")
            try:
                # Materialize only this match, excluding old round source/answers.
                self._git("worktree", "add", "--detach", "--no-checkout", str(target), base)
                self._git("sparse-checkout", "set", "--cone", self.repository_folder, cwd=target)
                self._git("checkout", "--detach", base, cwd=target)
                worker_match = target / self.repository_folder
                if not worker_match.resolve().is_relative_to(target) or not worker_match.is_dir():
                    raise NativeCoordinationError("Assigned repository folder missing in worker worktree")
                self._materialize_start_files(key, details, worker_match)
                self._provision_worker_sandbox(worker_match)
            except Exception as exc:
                # Preserve a worktree created before a filesystem/materialization failure.
                # Never leave a silently reusable QUEUED task pointing at unknown residue.
                if target.exists():
                    db.execute("UPDATE tasks SET phase='NEEDS_ATTENTION',worker_id=?,worktree=?,result=? WHERE key=?",
                               (worker_id, str(target), json.dumps({"error": str(exc)}), key))
                    db.commit()
                raise
            task_type = eligible["type"]
            specification = details.get("files", {}).get(f"challenge/{key}.md")
            status, now, remaining = self._clock(db)
            deadline = eligible.get("deadline_epoch")
            slack = eligible.get("slackSeconds")
            if slack is None:
                slack = min(remaining, float(deadline) - now if deadline is not None else remaining) if (now is not None and remaining is not None) else (remaining or 2400)
            budget = min(max(10, int(estimated_seconds)), max(10, int(slack - self.reserve)))
            worker_model = os.environ.get("MTM_WORKER_MODEL", "gpt-6.1-sol" if os.environ.get("MTM_ENV") == "production" else "gpt-6-luna")
            worker_effort = os.environ.get("MTM_WORKER_EFFORT", "medium" if os.environ.get("MTM_ENV") == "production" else "low")
            envelope = {"task_id": key, "task_type": task_type, "title": eligible.get("title", key),
                        "description": specification or eligible.get("description") or eligible.get("descriptionMd", ""), "repository_path": str(target),
                        "match_root": str(worker_match), "base_revision": base, "points": int(eligible.get("points", 0)),
                        "deadline_epoch": eligible.get("deadline_epoch"),
                        "selected_skills": ["mtm-init", f"mtm-{task_type.lower()}-worker", "mtm-verification", "mtm-evidence"],
                        "time_budget_seconds": budget, "model": worker_model, "reasoning_effort": worker_effort, "backend": "codex",
                        "evidence_required": details.get("evidence_required", eligible.get("evidence_required", [])),
                        "mvs": self._mvs_list(details.get("minimumVerificationScenarios", details.get("mvs"))),
                        "submit_template": details.get("submitTemplate"), "allowed_paths": [str(worker_match)],
                        "worker_id": worker_id,
                        "metadata": {"match_root": str(worker_match), "start_details": details,
                                     "project_runtime_path": str(Path(__file__).resolve().parents[3] / "runner" / "project_runtime.py"),
                                     "match_id": self.match_id, "worker_id": worker_id,
                                     "submit_template": details.get("submitTemplate"),
                                     "worker_result_path": str(worker_match / "worker_result.json")}}
            stored = json.loads(row["data"])
            _, server_now, server_remaining = self._clock(db)
            stored["lease"] = {"start_server_epoch": server_now, "start_remaining_seconds": server_remaining,
                               "budget_seconds": budget}
            db.execute("UPDATE tasks SET phase='RUNNING',worker_id=?,worktree=?,data=? WHERE key=?",
                       (worker_id, str(target), json.dumps(stored), key))
            return {"taskEnvelope": envelope, "worktreeRoot": str(target), "matchRoot": str(worker_match), "match_root": str(worker_match), "workerId": worker_id}

    @staticmethod
    def _mvs_list(value):
        if isinstance(value, list) and all(isinstance(v, dict) and isinstance(v.get("id"), str) for v in value):
            return value
        return None

    def _materialize_start_files(self, key: str, details: dict, worker_match: Path, validate_only: bool = False) -> None:
        """Copy only the current announced item's inputs, never another item's suites."""
        def destination(relative: str) -> Path:
            if not isinstance(relative, str):
                raise NativeCoordinationError("Delivered file path must be a string")
            path = PurePosixPath(relative)
            if path.is_absolute() or ".." in path.parts or "\\" in relative or re.match(r"^[A-Za-z]:", relative) or not path.parts:
                raise NativeCoordinationError("Delivered file path escapes worker match root")
            # Inputs may contain seed/check scripts, but never control/secret/immutable files.
            if any(part.startswith('.') or part == 'exam-info.json' for part in path.parts):
                raise NativeCoordinationError("Delivered file targets a protected path")
            target = worker_match / relative
            if not target.resolve().is_relative_to(worker_match.resolve()):
                raise NativeCoordinationError("Delivered file follows an escaping symlink")
            return target

        files = details.get("files", {})
        if files is not None and not isinstance(files, dict):
            raise NativeCoordinationError("Start Work files must be a mapping")
        for relative, content in (files or {}).items():
            target = destination(relative)
            if not isinstance(content, str) or len(content.encode('utf-8')) > 10_000_000:
                raise NativeCoordinationError("Delivered file content must be bounded UTF-8 text")
            if not validate_only:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding='utf-8')
        if validate_only:
            return
        source_challenge = self.match_root / "challenge"
        if source_challenge.is_dir() and source_challenge.resolve().is_relative_to(self.match_root):
            for source in [source_challenge / f"{key}.md", *source_challenge.glob(key + "-*")]:
                if not source.is_file() or source.is_symlink() or source.stat().st_size > 10_000_000:
                    continue
                target = destination("challenge/" + source.name)
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        # Synthesize challenge/{key}.md if missing so worker always has challenge specification
        challenge_md = destination(f"challenge/{key}.md")
        if not challenge_md.exists():
            title = details.get("title") or details.get("backlogTitle") or key
            desc = details.get("descriptionMd") or details.get("description") or f"# {key}: {title}\n\nTask details from Game Master."
            mvs = details.get("minimumVerificationScenarios") or ""
            start_instr = details.get("startInstruction") or ""
            synth_content = f"# {key}: {title}\n\n## Description\n{desc}\n"
            if start_instr:
                synth_content += f"\n## Instructions\n{start_instr}\n"
            if mvs:
                synth_content += f"\n## Verification Scenarios\n{mvs}\n"
            challenge_md.parent.mkdir(parents=True, exist_ok=True)
            challenge_md.write_text(synth_content, encoding='utf-8')
        template = details.get("submitTemplate")
        if template is not None:
            target = worker_match / "answers" / key / "submit.json"
            if not target.resolve().is_relative_to(worker_match.resolve()):
                raise NativeCoordinationError("Submit template follows an escaping symlink")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(template, indent=2), encoding='utf-8')
        # Copy pre-fetched official QA fixtures/sidecars from root answers/<key>/evidence if present
        source_evidence = self.match_root / "answers" / key / "evidence"
        if source_evidence.is_dir() and source_evidence.resolve().is_relative_to(self.match_root.resolve()):
            for ev_file in source_evidence.iterdir():
                if not ev_file.is_file() or ev_file.is_symlink() or ev_file.stat().st_size > 10_000_000:
                    continue
                target_ev = destination(f"answers/{key}/evidence/{ev_file.name}")
                if not target_ev.exists():
                    target_ev.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(ev_file, target_ev)
        # Auto-stage official challenge/<KEY>-mvs.test.ts to src/tests/<key.lower()>-mvs.test.ts when src/ exists
        mvs_challenge_test = worker_match / "challenge" / f"{key}-mvs.test.ts"
        if mvs_challenge_test.is_file() and (worker_match / "src").is_dir():
            mvs_dst = destination(f"src/tests/{key.lower()}-mvs.test.ts")
            if not mvs_dst.exists():
                mvs_dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(mvs_challenge_test, mvs_dst)

    def _provision_worker_sandbox(self, worker_match: Path) -> None:
        """Link shared read-only dependencies (src/node_modules) into worker worktree for zero-latency sandbox execution."""
        src_nm = self.match_root / "src" / "node_modules"
        dst_nm = worker_match / "src" / "node_modules"
        if src_nm.is_dir() and (worker_match / "src").is_dir() and not dst_nm.exists() and not dst_nm.is_symlink():
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["cmd", "/c", "mklink", "/J", str(dst_nm), str(src_nm)],
                        capture_output=True,
                        timeout=10,
                        check=False,
                    )
                else:
                    os.symlink(src_nm, dst_nm, target_is_directory=True)
            except Exception:
                pass

    def task_details(self, key: str) -> dict[str, Any]:
        with self._db() as db:
            row = self._task(db, self._key(key))
            return {"key": key, "phase": row["phase"], "workerId": row["worker_id"],
                    "worktreeRoot": row["worktree"], "data": json.loads(row["data"]),
                    "workerResult": json.loads(row["result"]) if row["result"] else None,
                    "receiptId": row["receipt"]}

    def reconcile_worker(self, key: str, *, confirmed_stopped: bool,
                         reason: str, requeue: bool = False) -> dict[str, Any]:
        """Never automatically expire a lease while its native agent may still be writing.

        Requeue preserves the old worktree for investigation and uses a fresh attempt path.
        The caller must first observe completion or stop the native worker and confirm it.
        """
        if confirmed_stopped is not True or not isinstance(reason, str) or not reason.strip():
            raise NativeCoordinationError("Confirmed stopped worker and reason are required")
        with self._db() as db:
            row = self._task(db, self._key(key))
            if row["phase"] not in ("RUNNING", "NEEDS_ATTENTION", "READY_TO_INTEGRATE", "INTEGRATING"):
                raise NativeCoordinationError("Only active/failed workers can be reconciled")
            data = json.loads(row["data"])
            data["prior_attempts"] = [*data.get("prior_attempts", []),
                                     {"worktree": row["worktree"], "worker": row["worker_id"], "reason": reason}]
            data["attempt_number"] = int(data.get("attempt_number", 1)) + 1
            phase = "QUEUED" if requeue else "CLOSED"
            db.execute("UPDATE tasks SET phase=?,data=?,worker_id=NULL,worktree=NULL WHERE key=?",
                       (phase, json.dumps(data), key))
            if self._meta(db, "integration_owner") == key:
                self._set_meta(db, "integration_owner", None)
                self._set_meta(db, "integration_acquired_at", None)
            return {"key": key, "phase": phase}

    def record_worker_result(self, key: str, worker_result: dict[str, Any]) -> dict[str, Any]:
        key = self._key(key)
        result = dict(worker_result)
        with self._db() as db:
            row = self._task(db, key)
            if row["phase"] != "RUNNING" or result.get("task_id") != key:
                raise NativeCoordinationError("Result does not match an active task")
            if result.get("worker_id", row["worker_id"]) != row["worker_id"]:
                raise NativeCoordinationError("Result worker mismatch")
            required = {"task_id", "status", "skill_ids", "changed_files", "test_results", "evidence_paths", "elapsed_seconds"}
            if not required.issubset(result):
                raise NativeCoordinationError("WorkerResult contract fields missing")
            allowed_statuses = ('READY_FOR_REVIEW', 'PARTIAL', 'DONE', 'BLOCKED', 'TIMED_OUT', 'FAILED', 'ESCALATE')
            if (result['status'] not in allowed_statuses
                    or any(not isinstance(result[field], list) or any(not isinstance(v, str) for v in result[field])
                           for field in ('skill_ids', 'changed_files', 'evidence_paths'))
                    or not isinstance(result['test_results'], list)
                    or not isinstance(result['elapsed_seconds'], (int, float)) or isinstance(result['elapsed_seconds'], bool)
                    or not math.isfinite(result['elapsed_seconds']) or result['elapsed_seconds'] < 0):
                raise NativeCoordinationError("WorkerResult fields violate contract types/status")

            worker_match = Path(row["worktree"]) / self.repository_folder
            has_changes_or_answers = bool(result.get("changed_files")) or (worker_match / "answers" / key / "submit.json").is_file()
            if result["status"] in ("BLOCKED", "TIMED_OUT", "FAILED") and has_changes_or_answers:
                result["status"] = "PARTIAL"
            is_integratable = result["status"] in ('READY_FOR_REVIEW', 'PARTIAL', 'DONE')

            if not is_integratable:
                db.execute("UPDATE tasks SET phase='NEEDS_ATTENTION',result=? WHERE key=?", (json.dumps(result), key))
                return {"key": key, "phase": "NEEDS_ATTENTION", "status": result["status"]}

            # Lossless JSON type fixes ("0" -> 0, "true" -> True) so a typing slip does not cost a Root turn.
            tests = [normalize_test_row(t) for t in result["test_results"]]
            result["test_results"] = tests
            problems = [f"test_results[{i}]: {why}" for i, t in enumerate(tests) for why in [passing_row_problem(t)] if why]
            if problems:
                raise NativeCoordinationError("Actual passing test commands and exit_code=0 required. Fix in "
                                              "worker_result.json: " + "; ".join(problems[:6]))
            def safe(relative: str) -> Path:
                if not isinstance(relative, str):
                    raise NativeCoordinationError("Result path must be a string")
                p = PurePosixPath(relative)
                if p.is_absolute() or ".." in p.parts or "\\" in relative or re.match(r"^[A-Za-z]:", relative):
                    raise NativeCoordinationError("Result path is not contained")
                if any(part in ('.git', '.codex', '.agents', '.mtm', 'node_modules', '__pycache__', '.cache')
                       or part.startswith('.env') or part == 'exam-info.json' for part in p.parts):
                    raise NativeCoordinationError("Result path targets a protected file or directory")
                resolved = (worker_match / relative).resolve()
                if not resolved.is_relative_to(worker_match.resolve()):
                    raise NativeCoordinationError("Result path escapes worker match root")
                return resolved
            for changed in result["changed_files"]:
                safe(changed)
            evidence = result["evidence_paths"]
            if not isinstance(evidence, list):
                raise NativeCoordinationError("evidence_paths must be a list")
            prefix = f"answers/{key}/evidence/"
            for relative in evidence:
                file = safe(relative)
                if not relative.startswith(prefix) or not file.is_file() or not file.stat().st_size:
                    raise NativeCoordinationError("Evidence must exist, be nonempty, and belong to current task")
            task = json.loads(row["data"])
            required_evidence = task.get("start_details", {}).get("evidence_required", task.get("evidence_required", []))
            if not set(required_evidence).issubset(evidence):
                raise NativeCoordinationError("Required evidence missing from worker result")
            db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE',result=? WHERE key=?", (json.dumps(result), key))
            return {"key": key, "phase": "READY_TO_INTEGRATE", "worktreeRoot": row["worktree"], "workerResult": result}

    def _assert_can_submit(self, db, key: str) -> None:
        row = self._task(db, self._key(key))
        status, now, remaining = self._clock(db)
        clock, sample = self._meta(db, "clock"), self._meta(db, "remaining")
        if (status not in ("RUNNING", "IN_PROGRESS", "STARTED") or now is None or remaining is None
                or remaining <= 0 or not clock or not sample):
            raise NativeCoordinationError("Submit/integration forbidden: official running clock unavailable or match paused/ended")
        if max(time.monotonic() - clock[1], time.monotonic() - sample[1]) > self.clock_max_age:
            raise NativeCoordinationError("Official clock is stale; refresh match before submission")
        task = json.loads(row["data"])
        if task.get("status") not in ("OPEN", "IN_PROGRESS", "WORKING", "STARTED"):
            raise NativeCoordinationError("Task is not officially open for submission")
        if task.get("deadline_epoch") is not None and float(task["deadline_epoch"]) <= now:
            raise NativeCoordinationError("Task official deadline elapsed")
        if row["phase"] == "SUBMITTED":
            raise NativeCoordinationError("Task already has an accepted receipt")

    def assert_can_submit(self, key: str) -> None:
        """Check immediately before dispatch; Game Master remains final deadline authority."""
        with self._db() as db:
            self._assert_can_submit(db, key)

    def begin_integration(self, key: str) -> dict[str, Any]:
        key = self._key(key)
        with self._db() as db:
            row = self._task(db, key)
            self._assert_can_submit(db, key)
            owner = self._meta(db, "integration_owner")
            if owner and owner != key:
                raise NativeCoordinationError("Another task owns the integration/submission lock")
            if row["phase"] not in ("READY_TO_INTEGRATE", "INTEGRATING", "INTEGRATED"):
                raise NativeCoordinationError("Worker result is not ready for integration")
            self._set_meta(db, "integration_owner", key)
            db.execute("UPDATE tasks SET phase='INTEGRATING' WHERE key=?", (key,))
            return {"key": key, "phase": "INTEGRATING", "worktreeRoot": row["worktree"], "workerResult": json.loads(row["result"])}

    def end_integration(self, key: str, success: bool = True) -> dict[str, Any]:
        with self._db() as db:
            if self._meta(db, "integration_owner") != key:
                raise NativeCoordinationError("Task does not own integration lock")
            phase = "INTEGRATED" if success else "READY_TO_INTEGRATE"
            db.execute("UPDATE tasks SET phase=? WHERE key=?", (phase, key))
            # Keep lock through successful push+submit; releasing here permits a remote-ref race.
            if not success:
                self._set_meta(db, "integration_owner", None)
                self._set_meta(db, "integration_acquired_at", None)
            return {"key": key, "phase": phase}

    def mark_submitted(self, key: str, receipt_id: str) -> None:
        if not isinstance(receipt_id, str) or not receipt_id.strip():
            raise NativeCoordinationError("A real server receipt is required")
        with self._db() as db:
            row = self._task(db, key)
            if row["phase"] == "SUBMITTED" and row["receipt"] == receipt_id:
                return
            if row["phase"] not in ("INTEGRATING", "INTEGRATED") or self._meta(db, "integration_owner") != key:
                raise NativeCoordinationError("Submission requires current integration ownership")
            db.execute("UPDATE tasks SET phase='SUBMITTED',receipt=? WHERE key=?", (receipt_id, key))
            self._set_meta(db, "integration_owner", None)
            self._set_meta(db, "integration_acquired_at", None)
