"""Persisted scheduling/leases for Codex's native subagents; never launches an AI CLI.

The root calls Start Work, registers its response, claims a task, then delegates the
returned envelope with the provider's spawn tool. Only the root integrates/submits.
"""
from __future__ import annotations
import os

from contextlib import contextmanager
import json
import uuid
import math
import re
import sqlite3
import subprocess
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Any, Iterator

from mtm_competition.scheduling import policy


class NativeCoordinationError(ValueError):
    pass


class NativeTaskCoordinator:
    def __init__(self, state_dir: Path | str, checkout_root: Path | str,
                 match_root: Path | str, repository_folder: str, match_id: str,
                 max_workers: int = 2, normal_workers: int = 1,
                 integration_reserve_seconds: int = 90, clock_max_age_seconds: int = 120,
                 integration_lease_seconds: float = 8.0, submit_unknown_ttl_seconds: float = 120.0,
                 pi_burst_slots: int = 0, pi_integration_gate_seconds: float = 45.0,
                 flush_lead_seconds: float | None = None, max_reconcile_attempts: int = 3):
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
        # Extra worker slots that ONLY Product Incidents may use, on top of max_workers. PIs arrive at
        # unknown times and are a speed race, so a PI must never wait behind busy ordinary workers.
        self.pi_burst_slots = max(0, min(8, int(pi_burst_slots)))
        # While a finished PI waits to integrate, ordinary items may not START integrating for this long
        # (a lock holder is never interrupted). Time-limited so a stuck PI cannot starve everything else.
        self.pi_integration_gate_seconds = max(0.0, float(pi_integration_gate_seconds))
        self.reserve = max(10, int(integration_reserve_seconds))
        self.clock_max_age = max(10, int(clock_max_age_seconds))
        # Lease: the integration owner must heartbeat (begin/touch_integration) within this window.
        self.integration_lease_seconds = max(1.0, float(integration_lease_seconds))
        # A submit whose outcome is unknown keeps the lock longer, but never forever.
        self.submit_unknown_ttl_seconds = max(self.integration_lease_seconds, float(submit_unknown_ttl_seconds))
        # S0: a running deadline item gets DEADLINE_FLUSH_REQUESTED this long before must_finalize.
        if flush_lead_seconds is None:
            flush_lead_seconds = float(os.environ.get("MTM_FLUSH_LEAD_SECONDS", "60"))
        self.flush_lead_seconds = max(0.0, float(flush_lead_seconds))
        # S4: bounded reconciliation of a Submit whose outcome is unknown.
        self.max_reconcile_attempts = max(1, int(max_reconcile_attempts))
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
                CREATE TABLE IF NOT EXISTS events(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,identity TEXT NOT NULL UNIQUE,type TEXT NOT NULL,
                  key TEXT,attempt_id TEXT,payload TEXT NOT NULL,at REAL NOT NULL);
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
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]{0,15}-[A-Za-z0-9_-]{1,64}", key):
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
        # Two worker flows only: ordinary items share the normal workers; a PI has its own flow
        # (PI burst slots plus the slots above normal_workers). No other item is classified.
        return task.get("type") == "PI"

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

    # ---- policy helpers (Solution v3: S2/S9') ----------------------------------------------------
    def _emit(self, db, event_type: str, key: str | None, attempt_id: str | None, identity: str,
              payload: dict | None = None) -> None:
        """Idempotent event ingest: the same identity is stored once (S7')."""
        db.execute("INSERT OR IGNORE INTO events(identity,type,key,attempt_id,payload,at) VALUES(?,?,?,?,?,?)",
                   (identity, event_type, key, attempt_id, json.dumps(payload or {}), time.monotonic()))

    def _submit_budget(self, db) -> float:
        return policy.submit_budget(self._meta(db, "submit_durations", []))

    @staticmethod
    def _time_left(task: dict, now, remaining, clock_ready: bool):
        if not clock_ready:
            return None
        deadline = task.get("deadline_epoch")
        return min(remaining, float(deadline) - now) if deadline is not None else remaining

    def _read_answer(self, worktree: str | None, key: str) -> dict | None:
        if not worktree:
            return None
        path = Path(worktree) / self.repository_folder / "answers" / key / "submit.json"
        try:
            value = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def _decision(self, db, row, now, remaining, clock_ready: bool, budget: float | None = None,
                  answer_status: str | None = None) -> "policy.Decision":
        task = json.loads(row["data"])
        result = json.loads(row["result"]) if row["result"] else {}
        answer_status = answer_status or result.get("answer_status")
        if answer_status is None and row["phase"] in policy.FINAL_PHASES:
            answer = self._read_answer(row["worktree"], row["key"])
            answer_status = answer.get("status") if answer else None
        ready_at = result.get("ready_at")
        ready_age = time.monotonic() - ready_at if isinstance(ready_at, (int, float)) else None
        status, *_ = self._clock(db)
        item_open = (task.get("status") in ("OPEN", "IN_PROGRESS", "WORKING", "STARTED")
                     and status not in ("FINISHED",))
        return policy.decide(item_type=task.get("type", ""), phase=row["phase"], item_open=item_open,
                             time_left=self._time_left(task, now, remaining, clock_ready), result=result,
                             answer_status=answer_status, rework_count=int(task.get("rework_count", 0)),
                             budget=self._submit_budget(db) if budget is None else budget, ready_age=ready_age)

    def decide(self, key: str, answer_status: str | None = None) -> dict[str, Any]:
        """The policy decision for one item; the Root follows it instead of re-deriving rules.

        ``answer_status`` evaluates the answer that is about to be submitted instead of the worker's own.
        """
        with self._db() as db:
            row = self._task(db, self._key(key))
            status, now, remaining = self._clock(db)
            clock_ready = now is not None and remaining is not None
            return {"key": row["key"], "phase": row["phase"],
                    **self._decision(db, row, now, remaining, clock_ready, answer_status=answer_status).to_dict()}

    def seconds_to_next_deadline_action(self) -> float | None:
        """Seconds until the next deadline item reaches its flush request (running) or must_finalize (ready)."""
        with self._db() as db:
            status, now, remaining = self._clock(db)
            if now is None or remaining is None:
                return None
            budget = self._submit_budget(db)
            waits = []
            for row in db.execute("SELECT * FROM tasks WHERE phase IN ('RUNNING','READY_TO_INTEGRATE')").fetchall():
                task = json.loads(row["data"])
                if task.get("deadline_epoch") is None:
                    continue
                left = self._time_left(task, now, remaining, True)
                lead = budget + (self.flush_lead_seconds if row["phase"] == "RUNNING" else 0.0)
                if left - lead > 0:
                    waits.append(left - lead)
            return min(waits) if waits else None

    def events_since(self, cursor: int = 0, limit: int = 100) -> dict[str, Any]:
        with self._db() as db:
            rows = db.execute("SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (int(cursor), int(limit))).fetchall()
            events = [{"id": r["id"], "type": r["type"], "key": r["key"], "attemptId": r["attempt_id"],
                       "payload": json.loads(r["payload"])} for r in rows]
            return {"events": events, "cursor": events[-1]["id"] if events else int(cursor)}

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
        budget = self._submit_budget(db)
        owner = self._meta(db, "integration_owner")
        acquired_at = self._meta(db, "integration_acquired_at", 0)
        inflight = self._meta(db, "integration_submit_inflight")
        reconciliation = None
        if owner and acquired_at:
            held_for = planning_time - float(acquired_at)
            if inflight and inflight.get("key") == owner:
                # S4: a Submit whose outcome is unknown never releases the lock on a timer. After the TTL the
                # item needs reconciliation (replay the journaled body with the same idempotency key).
                if (planning_time - float(inflight.get("since", acquired_at))) > self.submit_unknown_ttl_seconds:
                    reconciliation = self._meta(db, "integration_reconciliation")
                    if not reconciliation or reconciliation.get("key") != owner:
                        reconciliation = {"key": owner, "since": planning_time, "attempts": 0}
                        self._set_meta(db, "integration_reconciliation", reconciliation)
                        self._emit(db, "RECONCILIATION_REQUIRED", owner, None, f"reconcile:{owner}:{inflight.get('since')}")
            elif held_for > self.integration_lease_seconds:
                # Lease expired with no Submit in flight: the owner stopped heartbeating and is presumed dead.
                db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE' WHERE key=? AND phase='INTEGRATING'", (owner,))
                self._release_integration(db)
                owner = None
        pi_active = sum(json.loads(r["data"]).get("type") == "PI" for r in active)
        base_used = len(active) - min(pi_active, self.pi_burst_slots)   # PIs fill burst slots first
        base_free = max(0, self.max_workers - base_used)
        burst_free = max(0, self.pi_burst_slots - pi_active)
        response = {"matchStatus": status, "availableSlots": base_free + burst_free,
                    "baseSlotsFree": base_free, "piBurstSlotsFree": burst_free,
                    "selected": [], "active": [public_row(r) for r in active], "readyToIntegrate": [],
                    "readyDiagnostics": [], "skippedReady": [], "integrationOwner": owner, "integrationBlocked": [],
                    "overdueWorkers": [], "deadlineFlushRequests": [], "workerResultsAvailable": [],
                    "reconciliationRequired": [reconciliation] if reconciliation else [],
                    "submitBudgetSeconds": budget}

        # S0: near a deadline the coordinator only REQUESTS a checkpoint/final result. It never writes
        # submit.json, never fabricates a result and never changes the phase of a running worker.
        for row in active:
            task = json.loads(row["data"])
            key, attempt_id = row["key"], task.get("attempt_id")
            slack = self._time_left(task, now, remaining, clock_ready) if task.get("deadline_epoch") is not None else None
            result_file = Path(row["worktree"]) / self.repository_folder / "worker_result.json" if row["worktree"] else None
            if result_file is not None and result_file.is_file():
                try:
                    stamp = result_file.stat().st_mtime_ns
                except OSError:
                    stamp = 0
                response["workerResultsAvailable"].append({**public_row(row), "attemptId": attempt_id,
                    "path": str(result_file), "requiredAction": "Read it and call record_worker_result now"})
                self._emit(db, "WORKER_RESULT_AVAILABLE", key, attempt_id, f"result:{key}:{attempt_id}:{stamp}",
                           {"path": str(result_file)})
            if slack is None:
                continue
            if 0 < slack <= budget + self.flush_lead_seconds:
                if not task.get("flush_requested_at"):
                    task["flush_requested_at"] = planning_time
                    db.execute("UPDATE tasks SET data=? WHERE key=?", (json.dumps(task), key))
                    row["data"] = json.dumps(task)
                    self._emit(db, "DEADLINE_FLUSH_REQUESTED", key, attempt_id, f"flush:{key}:{attempt_id}",
                               {"slackSeconds": slack})
                response["deadlineFlushRequests"].append({**public_row(row), "attemptId": attempt_id,
                    "slackSeconds": slack, "mustFinalize": slack <= budget,
                    "requiredAction": "DEADLINE_FLUSH_REQUESTED: tell the worker to commit and write its final "
                                      "worker_result.json now; record it as soon as it exists"})
            elif slack <= 0 and task.get("flush_requested_at") and not task.get("flush_timeout"):
                # FLUSH_TIMEOUT is recorded honestly; the phase is kept and nothing becomes submittable.
                task["flush_timeout"] = True
                db.execute("UPDATE tasks SET data=? WHERE key=?", (json.dumps(task), key))
                row["data"] = json.dumps(task)
                self._emit(db, "FLUSH_TIMEOUT", key, attempt_id, f"flush-timeout:{key}:{attempt_id}")

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
            if task.get("flush_timeout"):
                reasons.append("FLUSH_TIMEOUT")
            if reasons:
                response["overdueWorkers"].append({**public_row(row), "reasons": reasons,
                    "elapsedSeconds": elapsed, "budgetSeconds": lease.get("budget_seconds"),
                    "requiredAction": "Ask the worker for its final result; record it if it exists. Otherwise native "
                                      "stop/checkpoint and confirm completion before reconcile_worker"})
        for row in ready:
            task = json.loads(row["data"])
            deadline = task.get("deadline_epoch")
            slack = min(remaining, float(deadline) - now if deadline is not None else remaining) if clock_ready else None
            expired = status == "FINISHED" or (running and clock_ready and slack <= 0)
            closed = task.get("status") == "CLOSED" or expired
            record = {**public_row(row), "type": task["type"], "taskStatus": task.get("status"),
                      "deadlineEpoch": deadline, "slackSeconds": slack, "points": task.get("points", 0),
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
                decision = self._decision(db, row, now, remaining, clock_ready, budget)
                record["decision"] = decision.to_dict()
                if owner and owner != row["key"]:
                    record["reason"] = "Waiting for integration owner"
                elif owner == row["key"] or decision.action == policy.SUBMIT:
                    response["readyToIntegrate"].append(record)
                else:
                    record["reason"] = f"Policy decision {decision.action}: {decision.reason}"
            else:
                record["reason"] = "Match/item not dispatchable or official clock unavailable/stale"
            response["readyDiagnostics"].append(record)
        response["readyToIntegrate"].sort(key=lambda r: (
            0 if r["key"] == owner else 1,
            policy.priority_key(item_type=r["type"], slack=r["slackSeconds"], has_deadline=r["deadlineEpoch"] is not None,
                                submit_ready=True, budget=budget, key=r["key"])))
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
            # S9': the same priority function as the ready queue and the PI gate. Every ordinary item is
            # treated alike (no type, points or size ranking): PI first, then least time left, then key.
            has_deadline = deadline is not None or task.get("deadlineMinutes") is not None
            prio = policy.priority_key(item_type=task.get("type", ""), slack=slack, has_deadline=has_deadline,
                                       submit_ready=False, budget=budget, key=row["key"])
            candidates.append((prio, task, urgent, slack))
        candidates.sort(key=lambda r: r[0])
        for _, task, urgent, slack in candidates:
            if base_free + burst_free <= 0:
                break
            is_pi_task = task.get("type") == "PI"
            if not is_pi_task and base_free <= 0:
                continue                      # burst slots are PI-only
            if not urgent and normals >= self.normal_workers:
                continue
            if self._overlap(busy_paths, task.get("claimed_paths", [])):
                continue
            if is_pi_task and burst_free > 0:
                burst_free -= 1               # a PI uses a PI-only slot before touching the shared ones
            else:
                base_free -= 1
            response["selected"].append({**task, "urgent": urgent, "slackSeconds": slack})
            busy_paths.update(task.get("claimed_paths", []))
            normals += not urgent
        return response

    def _release_integration(self, db) -> None:
        for name in ("integration_owner", "integration_acquired_at", "integration_lease_id",
                     "integration_submit_inflight", "integration_reconciliation", "integration_started_at"):
            self._set_meta(db, name, None)

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
                if (
                    plan.get("matchStatus") in ("RUNNING", "IN_PROGRESS", "STARTED")
                    and plan["availableSlots"] > 0
                    and (task_data.get("type") == "PI" or plan.get("baseSlotsFree", 1) > 0)
                    and row["phase"] == "QUEUED"
                    and row["started"]
                    and (urgent or normals < self.normal_workers)
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
            # Worker budget = the real hand-off point, not the Root's guess (594OYFBS: estimates of 300 s cut
            # BUG-01 and CR-01 mid-way and forced restarts). Deadline items stop MTM_DEADLINE_HANDOFF_SECONDS
            # before their deadline; every item stops MTM_MATCH_HANDOFF_SECONDS before the match ends, leaving
            # time to commit, integrate and submit (PARTIAL if unfinished). MTM_WORKER_BUDGET_MODE=estimate
            # restores min(estimate, slack - reserve).
            if os.environ.get("MTM_WORKER_BUDGET_MODE", "handoff") == "estimate":
                budget = min(max(10, int(estimated_seconds)), max(10, int(slack - self.reserve)))
            else:
                match_handoff = max(self.reserve, int(os.environ.get("MTM_MATCH_HANDOFF_SECONDS", "240")))
                deadline_handoff = max(self.reserve, int(os.environ.get("MTM_DEADLINE_HANDOFF_SECONDS", "150")))
                limits = []
                if remaining is not None:
                    limits.append(float(remaining) - match_handoff)
                if deadline is not None and now is not None:
                    limits.append(float(deadline) - float(now) - deadline_handoff)
                if not limits:
                    limits.append(float(slack) - match_handoff)
                budget = max(10, int(min(limits)))
            worker_model = os.environ.get("MTM_WORKER_MODEL", "gpt-6.1-sol" if os.environ.get("MTM_ENV") == "production" else "gpt-6-luna")
            worker_effort = os.environ.get("MTM_WORKER_EFFORT", "medium" if os.environ.get("MTM_ENV") == "production" else "low")
            # One model for every worker (PI included); the only split is the PI flow.
            lane = "pi" if str(task_type).upper() == "PI" else "normal"
            skill_type = task_type.lower() if task_type in ("BUG", "CR", "PI", "TASK") else "cr"
            envelope = {"task_id": key, "task_type": task_type, "title": eligible.get("title", key),
                        "description": specification or eligible.get("description") or eligible.get("descriptionMd", ""), "repository_path": str(target),
                        "match_root": str(worker_match), "base_revision": base, "points": int(eligible.get("points", 0)),
                        "deadline_epoch": eligible.get("deadline_epoch"),
                        "selected_skills": ["mtm-init", f"mtm-{skill_type}-worker", "mtm-verification", "mtm-evidence"],
                        "time_budget_seconds": budget, "model": worker_model, "reasoning_effort": worker_effort, "backend": "codex",
                        "lane": lane, "agent_profile": "team_feature_worker",
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
            stored["lane"] = lane
            stored["lease"] = {"start_server_epoch": server_now, "start_remaining_seconds": server_remaining,
                               "budget_seconds": budget}
            # S8': every attempt has its own identity; artifacts and results of another attempt are rejected.
            attempt_id = uuid.uuid4().hex
            stored["attempt_id"] = attempt_id
            stored.pop("flush_requested_at", None)
            stored.pop("flush_timeout", None)
            envelope["attempt_id"] = attempt_id
            envelope["metadata"]["attempt_id"] = attempt_id
            envelope["metadata"]["worker_checkpoint_path"] = str(worker_match / "worker_checkpoint.json")
            envelope["result_contract"] = ("Write worker_checkpoint.json (version, timestamp, worker_id, attempt_id, progress) "
                                           "atomically while working; write worker_result.json atomically, with this "
                                           "worker_id and attempt_id, only when finished. Record failing tests honestly "
                                           "(passed=false); never move them out of test_results.")
            if stored.get("prior_attempts"):
                # REWORK hand-off: earlier worktrees are kept for investigation and reuse of verified artifacts.
                envelope["prior_attempts"] = stored["prior_attempts"]
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
            previous = json.loads(row["result"]) if row["result"] else None
            is_rework = requeue and row["phase"] in ("READY_TO_INTEGRATE", "NEEDS_ATTENTION") and bool(previous)
            if is_rework and int(data.get("rework_count", 0)) >= policy.max_rework():
                # S2 REWORK has a bounded number of retries; the policy then chooses SUBMIT or CLOSE.
                raise NativeCoordinationError(f"REWORK limit reached ({policy.max_rework()}); follow decide() instead")
            hand_off = {"worktree": row["worktree"], "worker": row["worker_id"], "reason": reason,
                        "attempt_id": data.get("attempt_id")}
            if previous:
                hand_off["result"] = {k: previous.get(k) for k in ("status", "answer_status", "verification_status",
                                                                   "mvs_passed", "mvs", "changed_files", "evidence_paths")}
            data["prior_attempts"] = [*data.get("prior_attempts", []), hand_off]
            data["attempt_number"] = int(data.get("attempt_number", 1)) + 1
            if is_rework:
                data["rework_count"] = int(data.get("rework_count", 0)) + 1
            data["attempt_id"] = None
            phase = "QUEUED" if requeue else "CLOSED"
            db.execute("UPDATE tasks SET phase=?,data=?,worker_id=NULL,worktree=NULL WHERE key=?",
                       (phase, json.dumps(data), key))
            if self._meta(db, "integration_owner") == key:
                self._release_integration(db)
            return {"key": key, "phase": phase}

    def record_worker_result(self, key: str, worker_result: dict[str, Any]) -> dict[str, Any]:
        key = self._key(key)
        result = dict(worker_result)
        with self._db() as db:
            row = self._task(db, key)
            if result.get("task_id") != key:
                raise NativeCoordinationError("Result does not match an active task")
            data = json.loads(row["data"])
            attempt_id = data.get("attempt_id")
            stale_attempt = (result.get("attempt_id") is not None and attempt_id is not None
                             and result.get("attempt_id") != attempt_id)
            if row["phase"] != "RUNNING" or stale_attempt:
                # S8' late/stale result: keep an audit copy, never reopen a CLOSED item, never change the phase.
                reason = "stale attempt" if stale_attempt else f"late result (phase {row['phase']})"
                late = data.setdefault("late_results", [])
                late.append({"received_at": time.monotonic(), "reason": reason, "phase": row["phase"],
                             "worker_id": result.get("worker_id"), "attempt_id": result.get("attempt_id"),
                             "status": result.get("status"), "result": result})
                data["late_results"] = late[-5:]
                db.execute("UPDATE tasks SET data=? WHERE key=?", (json.dumps(data), key))
                self._emit(db, "LATE_RESULT", key, result.get("attempt_id"),
                           f"late:{key}:{result.get('attempt_id')}:{len(late)}:{row['phase']}", {"reason": reason})
                return {"key": key, "phase": row["phase"], "auditOnly": True, "reason": reason}
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
            if attempt_id is not None:
                result["attempt_id"] = attempt_id

            worker_match = Path(row["worktree"]) / self.repository_folder
            has_changes_or_answers = bool(result.get("changed_files")) or (worker_match / "answers" / key / "submit.json").is_file()
            # A failed/blocked/timed-out worker is never silently promoted to an integratable result.
            # Its artifacts are kept for review; salvage requires the worker/root to report PARTIAL explicitly.
            if result["status"] in ("BLOCKED", "TIMED_OUT", "FAILED"):
                result["has_unverified_artifacts"] = bool(has_changes_or_answers)
                result["verification_status"] = "UNVERIFIED"
                result["mvs_passed"] = False
            is_integratable = result["status"] in ('READY_FOR_REVIEW', 'PARTIAL', 'DONE')

            if not is_integratable:
                db.execute("UPDATE tasks SET phase='NEEDS_ATTENTION',result=? WHERE key=?", (json.dumps(result), key))
                self._emit(db, "WORKER_RESULT_RECORDED", key, attempt_id, f"recorded:{key}:{attempt_id}:{result['status']}")
                return {"key": key, "phase": "NEEDS_ATTENTION", "status": result["status"]}

            # S6': failing tests are recorded honestly (passed=false) instead of being rejected, so nobody has to
            # move them out of test_results. Each row must still describe a real command.
            tests = result["test_results"]
            if any(not isinstance(t, dict) or not isinstance(t.get("command"), str) or not t["command"].strip()
                   or not isinstance(t.get("passed"), bool)
                   or (t.get("exit_code") is not None and type(t.get("exit_code")) is not int)
                   or (t.get("passed") is True and t.get("exit_code") != 0) for t in tests):
                raise NativeCoordinationError("Each test result needs a command, a boolean passed and an integer exit_code "
                                              "(exit_code=0 when passed)")
            if not tests and not result["evidence_paths"]:
                raise NativeCoordinationError("An integratable result needs test commands or evidence paths")
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
            details = data.get("start_details", {})
            required_evidence = details.get("evidence_required", data.get("evidence_required", []))
            if not set(required_evidence).issubset(evidence):
                raise NativeCoordinationError("Required evidence missing from worker result")
            # RECORDED -> VERIFIED -> INTEGRATION_ELIGIBLE: VERIFIED only when the MVS really passed.
            answer = self._read_answer(row["worktree"], key)
            mvs = policy.mvs_status(policy.announced_test_ids(details), answer, tests,
                                    result.get("diagnostic_test_results"))
            result["mvs"] = mvs
            result["mvs_passed"] = mvs["mvs_passed"]
            result["answer_status"] = answer.get("status") if answer else None
            if mvs["mvs_passed"]:
                result["verification_status"] = "VERIFIED" if mvs["executed_commands"] else "EVIDENCE_ONLY"
            else:
                result["verification_status"] = "RECORDED"
            result["ready_at"] = time.monotonic()   # used by the PI-first integration gate
            db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE',result=? WHERE key=?", (json.dumps(result), key))
            self._emit(db, "WORKER_RESULT_RECORDED", key, attempt_id, f"recorded:{key}:{attempt_id}:{result['status']}")
            row = self._task(db, key)
            status, now, remaining = self._clock(db)
            decision = self._decision(db, row, now, remaining, now is not None and remaining is not None)
            return {"key": key, "phase": "READY_TO_INTEGRATE", "worktreeRoot": row["worktree"], "workerResult": result,
                    "decision": decision.to_dict()}

    def flush_snapshot(self, key: str, *, confirmed_stopped: bool, reason: str) -> dict[str, Any]:
        """S0 option 3: an explicit, policy-gated cutoff. Never called by _plan().

        Requires the caller to confirm the worker stopped writing. Fences the attempt, snapshots the worktree
        HEAD and records an UNVERIFIED PARTIAL built only from the worker's own submit.json (never written
        here). Whether it may be submitted is decided by the policy for the item type.
        """
        key = self._key(key)
        if confirmed_stopped is not True or not isinstance(reason, str) or not reason.strip():
            raise NativeCoordinationError("Confirmed stopped worker and reason are required")
        with self._db() as db:
            row = self._task(db, key)
            if row["phase"] != "RUNNING":
                raise NativeCoordinationError("Only a running attempt can be flushed")
            answer = self._read_answer(row["worktree"], key)
            if not answer or str(answer.get("status", "")).upper() != "PARTIAL":
                raise NativeCoordinationError("Flush needs the worker's own answers/<key>/submit.json with status PARTIAL")
            worktree = Path(row["worktree"])
            try:
                revision = self._git("rev-parse", "HEAD", cwd=worktree)
                dirty = bool(self._git("status", "--porcelain", cwd=worktree))
            except NativeCoordinationError as exc:
                raise NativeCoordinationError(f"Cannot snapshot worktree: {exc}")
            data = json.loads(row["data"])
            attempt_id = data.get("attempt_id")
            data.setdefault("fenced_attempts", []).append(attempt_id)
            data["attempt_id"] = None          # any later result of the fenced attempt goes to the audit path
            result = {"task_id": key, "worker_id": row["worker_id"], "status": "PARTIAL",
                      "skill_ids": ["mtm-explicit-flush"], "changed_files": [], "test_results": [],
                      "evidence_paths": [], "elapsed_seconds": 0, "cutoff_triggered": True,
                      "verification_status": "UNVERIFIED", "mvs_passed": False, "answer_status": "PARTIAL",
                      "snapshot_revision": revision, "snapshot_dirty": dirty, "fenced_attempt_id": attempt_id,
                      "note": f"Explicit flush: {reason.strip()}", "ready_at": time.monotonic()}
            db.execute("UPDATE tasks SET phase='READY_TO_INTEGRATE',data=?,result=? WHERE key=?",
                       (json.dumps(data), json.dumps(result), key))
            self._emit(db, "FLUSH_SNAPSHOT", key, attempt_id, f"flush-snapshot:{key}:{attempt_id}")
            row = self._task(db, key)
            status, now, remaining = self._clock(db)
            decision = self._decision(db, row, now, remaining, now is not None and remaining is not None)
            return {"key": key, "phase": "READY_TO_INTEGRATE", "workerResult": result, "decision": decision.to_dict()}

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
            status_clk, now_clk, remaining_clk = self._clock(db)
            clock_ready = now_clk is not None and remaining_clk is not None
            budget = self._submit_budget(db)
            if owner != key:
                # S2: only the policy decides whether an item is submitted now.
                decision = self._decision(db, row, now_clk, remaining_clk, clock_ready, budget)
                if decision.action != policy.SUBMIT:
                    raise NativeCoordinationError(f"Policy decision {decision.action}: {decision.reason}")
            if owner != key and self.pi_integration_gate_seconds > 0:
                # S9': the PI-first gate uses the same priority function as the ready queue, so a critical
                # deadline item that still has its submit budget is not held behind a PI.
                def prio(r):
                    task = json.loads(r["data"])
                    return policy.priority_key(
                        item_type=task.get("type", ""), slack=self._time_left(task, now_clk, remaining_clk, clock_ready),
                        has_deadline=task.get("deadline_epoch") is not None, submit_ready=True, budget=budget,
                        key=r["key"])
                mine = prio(row)
                now_mono = time.monotonic()
                for other in db.execute("SELECT * FROM tasks WHERE phase='READY_TO_INTEGRATE'").fetchall():
                    if other["key"] == key or json.loads(other["data"]).get("type") != "PI":
                        continue
                    ready_at = (json.loads(other["result"] or "{}") or {}).get("ready_at")
                    if not (isinstance(ready_at, (int, float)) and 0 <= now_mono - ready_at <= self.pi_integration_gate_seconds):
                        continue
                    if (prio(other) < mine and self._decision(db, other, now_clk, remaining_clk, clock_ready, budget).action
                            == policy.SUBMIT):
                        raise NativeCoordinationError(
                            f"PI {other['key']} is ready to integrate; integrate it first (PI-first gate)")
            if owner != key:
                self._set_meta(db, "integration_started_at", time.monotonic())
            if owner != key or not self._meta(db, "integration_lease_id"):
                self._set_meta(db, "integration_lease_id", uuid.uuid4().hex)
            self._set_meta(db, "integration_owner", key)
            # Acquire/renew the lease. Without this timestamp the lease can never be evaluated.
            self._set_meta(db, "integration_acquired_at", time.monotonic())
            db.execute("UPDATE tasks SET phase='INTEGRATING' WHERE key=?", (key,))
            return {"key": key, "phase": "INTEGRATING", "worktreeRoot": row["worktree"],
                    "workerResult": json.loads(row["result"]), "leaseId": self._meta(db, "integration_lease_id")}

    def touch_integration(self, key: str, lease_id: str | None = None) -> bool:
        """Heartbeat for the integration lock. Returns False if this task no longer owns it (fenced out)."""
        key = self._key(key)
        with self._db() as db:
            if self._meta(db, "integration_owner") != key:
                return False
            if lease_id is not None and self._meta(db, "integration_lease_id") != lease_id:
                return False
            self._set_meta(db, "integration_acquired_at", time.monotonic())
            return True

    def owns_integration(self, key: str, lease_id: str | None = None) -> bool:
        key = self._key(key)
        with self._db() as db:
            if self._meta(db, "integration_owner") != key:
                return False
            return lease_id is None or self._meta(db, "integration_lease_id") == lease_id

    def mark_submit_in_flight(self, key: str) -> None:
        """Record that a Submit request is being sent. Until the outcome is known the lock is not reclaimed early."""
        key = self._key(key)
        with self._db() as db:
            if self._meta(db, "integration_owner") != key:
                raise NativeCoordinationError("Submission requires current integration ownership")
            self._set_meta(db, "integration_submit_inflight", {"key": key, "since": time.monotonic()})

    def note_reconciliation_attempt(self, key: str) -> dict[str, Any]:
        """S4: count a replay of an unknown-outcome Submit. After max attempts the lock is released, the item is
        parked in NEEDS_ATTENTION with submit_outcome_unknown, and the journal still blocks a different body."""
        key = self._key(key)
        with self._db() as db:
            rec = self._meta(db, "integration_reconciliation")
            if not rec or rec.get("key") != key:
                return {"key": key, "reconciliationRequired": False}
            rec["attempts"] = int(rec.get("attempts", 0)) + 1
            if rec["attempts"] > self.max_reconcile_attempts:
                data = json.loads(self._task(db, key)["data"])
                data["submit_outcome_unknown"] = True
                db.execute("UPDATE tasks SET phase='NEEDS_ATTENTION',data=? WHERE key=?", (json.dumps(data), key))
                self._release_integration(db)
                self._emit(db, "RECONCILIATION_GAVE_UP", key, None, f"reconcile-gave-up:{key}:{rec.get('since')}")
                return {"key": key, "reconciliationRequired": False, "gaveUp": True, "attempts": rec["attempts"]}
            self._set_meta(db, "integration_reconciliation", rec)
            return {"key": key, "reconciliationRequired": True, "attempts": rec["attempts"]}

    def reconciliation_state(self) -> dict[str, Any] | None:
        with self._db() as db:
            return self._meta(db, "integration_reconciliation")

    def clear_submit_in_flight(self, key: str) -> None:
        key = self._key(key)
        with self._db() as db:
            inflight = self._meta(db, "integration_submit_inflight")
            if inflight and inflight.get("key") == key:
                self._set_meta(db, "integration_submit_inflight", None)

    def worker_quiesced(self, key: str) -> bool:
        """True only when the task's worker has reported completion (phase is no longer RUNNING).

        The coordinator cannot observe a native worker process; a RUNNING phase means it may still write.
        """
        with self._db() as db:
            row = self._task(db, self._key(key))
            return row["phase"] != "RUNNING"

    def end_integration(self, key: str, success: bool = True) -> dict[str, Any]:
        with self._db() as db:
            if self._meta(db, "integration_owner") != key:
                raise NativeCoordinationError("Task does not own integration lock")
            phase = "INTEGRATED" if success else "READY_TO_INTEGRATE"
            db.execute("UPDATE tasks SET phase=? WHERE key=?", (phase, key))
            # Keep lock through successful push+submit; releasing here permits a remote-ref race.
            if not success:
                self._release_integration(db)
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
            started = self._meta(db, "integration_started_at")
            if isinstance(started, (int, float)):
                # measured_submit_p95 for submit_budget (S2): begin_integration -> accepted receipt.
                samples = [*self._meta(db, "submit_durations", []), max(0.0, time.monotonic() - started)][-20:]
                self._set_meta(db, "submit_durations", samples)
            self._emit(db, "SUBMITTED", key, None, f"submitted:{key}:{receipt_id}")
            self._release_integration(db)
