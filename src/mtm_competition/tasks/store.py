"""SQLite-backed deterministic TaskStore for competition state and idempotency."""
from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mtm_core.contracts import TaskEnvelope, TaskState
from mtm_core.runtime.process import (
    _kill_unix_descendants,
    get_process_create_time,
    is_expected_worker_pid,
    is_pid_alive,
)
from mtm_competition.tasks.state_machine import TaskStateMachine


def _terminate_pid_tree(pid: int) -> None:
    """Terminate an orphaned worker PID and its descendants safely across platforms."""
    if not pid or pid <= 0 or pid == os.getpid():
        return
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
            )
        else:
            _kill_unix_descendants(pid)
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except Exception:
        pass


class TaskStore:
    """Deterministic, crash-resilient persistence layer for tasks, attempts, and submissions."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
        self.conn.row_factory = sqlite3.Row
        if self.db_path != ":memory:":
            try:
                # WAL: far fewer fsyncs per commit and no reader/writer blocking.
                # synchronous=FULL keeps every commit durable (the once-only submission ledger must not lose rows).
                self.conn.execute("PRAGMA journal_mode=WAL")
                self.conn.execute("PRAGMA synchronous=FULL")
                self.conn.execute("PRAGMA busy_timeout=30000")
            except sqlite3.DatabaseError:
                pass
        self._init_tables()

    def close(self) -> None:
        """Close the underlying SQLite connection (releases the DB file handle; required on Windows)."""
        try:
            self.conn.close()
        except Exception:
            pass

    def __enter__(self) -> "TaskStore":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _init_tables(self) -> None:
        with self.conn:
            self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS matches (
                id TEXT PRIMARY KEY,
                started_at REAL,
                duration_seconds REAL,
                status TEXT
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                match_id TEXT,
                task_type TEXT NOT NULL,
                title TEXT,
                description TEXT,
                points INTEGER DEFAULT 0,
                awarded_points REAL DEFAULT 0,
                deadline_epoch REAL,
                time_budget_seconds INTEGER DEFAULT 420,
                status TEXT NOT NULL,
                repo_path TEXT,
                base_revision TEXT,
                mvs_json TEXT,
                evidence_required_json TEXT,
                fencing_token TEXT,
                active_pid INTEGER,
                active_pid_started_at REAL,
                verified_result_json TEXT,
                verified_workspace TEXT,
                verified_revision TEXT,
                start_working_requeues INTEGER DEFAULT 0,
                created_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                worker_id TEXT,
                attempt_number INTEGER DEFAULT 1,
                status TEXT NOT NULL,
                elapsed_seconds REAL,
                billable_tokens INTEGER,
                result_json TEXT,
                fencing_token TEXT,
                created_at REAL,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            );

            CREATE TABLE IF NOT EXISTS submissions (
                task_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                score INTEGER DEFAULT 0,
                provisional_points INTEGER DEFAULT 0,
                confirmed_points INTEGER DEFAULT 0,
                git_revision TEXT,
                submission_id TEXT,
                response_json TEXT,
                submitted_at REAL,
                updated_at REAL
            );

            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT,
                event_type TEXT NOT NULL,
                from_state TEXT,
                to_state TEXT,
                timestamp REAL,
                payload_json TEXT
            );
            """)

            # Safe column migrations for existing databases
            for col, col_type in [
                ("git_revision", "TEXT"),
                ("submission_id", "TEXT"),
                ("updated_at", "REAL"),
                ("provisional_points", "INTEGER DEFAULT 0"),
                ("confirmed_points", "INTEGER DEFAULT 0"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE submissions ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            for col, col_type in [
                ("time_budget_seconds", "INTEGER DEFAULT 420"),
                ("awarded_points", "REAL DEFAULT 0"),
                ("fencing_token", "TEXT"),
                ("active_pid", "INTEGER"),
                ("active_pid_started_at", "REAL"),
                ("verified_result_json", "TEXT"),
                ("verified_workspace", "TEXT"),
                ("verified_revision", "TEXT"),
                ("start_working_requeues", "INTEGER DEFAULT 0"),
            ]:
                try:
                    self.conn.execute(f"ALTER TABLE tasks ADD COLUMN {col} {col_type}")
                except sqlite3.OperationalError:
                    pass

            try:
                self.conn.execute("ALTER TABLE attempts ADD COLUMN fencing_token TEXT")
            except sqlite3.OperationalError:
                pass

    def add_task(self, envelope: TaskEnvelope, match_id: str = "default_match") -> None:
        """Register a new task discovered from Game Master."""
        now = time.time()
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO tasks (
                task_id, match_id, task_type, title, description, points,
                deadline_epoch, time_budget_seconds, status, repo_path, base_revision, mvs_json,
                evidence_required_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                envelope.task_id,
                match_id,
                envelope.task_type,
                envelope.title,
                envelope.description,
                envelope.points,
                envelope.deadline_epoch,
                envelope.time_budget_seconds or 420,
                TaskState.DISCOVERED.value,
                envelope.repo_path,
                envelope.base_revision,
                json.dumps(envelope.mvs or []),
                json.dumps(envelope.evidence_required or []),
                now,
                now,
            ))
            self.record_event(envelope.task_id, "TASK_DISCOVERED", None, TaskState.DISCOVERED.value, envelope.to_dict())

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def list_tasks(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if status:
            cursor.execute("SELECT * FROM tasks WHERE status = ? ORDER BY points DESC", (status,))
        else:
            cursor.execute("SELECT * FROM tasks ORDER BY points DESC")
        return [dict(r) for r in cursor.fetchall()]

    def update_task_status(
        self,
        task_id: str,
        new_state: TaskState | str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        state_str = new_state.value if isinstance(new_state, TaskState) else new_state
        task = self.get_task(task_id)
        old_state = task["status"] if task else None
        now = time.time()

        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (state_str, now, task_id),
            )
            self.record_event(task_id, "STATUS_CHANGE", old_state, state_str, payload or {})

    def issue_fencing_token(self, task_id: str, active_pid: Optional[int] = None) -> str:
        """Issue a fresh fencing token for a task attempt and optionally record its active PID."""
        token = f"fence-{task_id}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        now = time.time()
        pid_started_at = get_process_create_time(int(active_pid)) if active_pid else None
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET fencing_token = ?, active_pid = ?, active_pid_started_at = ?, updated_at = ? WHERE task_id = ?",
                (token, active_pid, pid_started_at, now, task_id),
            )
            self.record_event(
                task_id,
                "FENCING_TOKEN_ISSUED",
                None,
                None,
                {"fencing_token": token, "active_pid": active_pid, "active_pid_started_at": pid_started_at},
            )
        return token

    def set_active_pid(
        self,
        task_id: str,
        pid: Optional[int],
        fencing_token: Optional[str] = None,
        pid_started_at: Optional[float] = None,
    ) -> bool:
        """Record or clear the active OS subprocess PID (and its OS creation timestamp) for a running task."""
        if fencing_token is not None and not self.validate_fencing_token(task_id, fencing_token):
            return False
        now = time.time()
        resolved_start = (
            pid_started_at
            if pid_started_at is not None
            else (get_process_create_time(int(pid)) if pid else None)
        )
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET active_pid = ?, active_pid_started_at = ?, updated_at = ? WHERE task_id = ?",
                (pid, resolved_start, now, task_id),
            )
        return True

    def validate_fencing_token(self, task_id: str, fencing_token: Optional[str]) -> bool:
        """Return True if fencing_token matches the task's current active fencing token."""
        if not fencing_token:
            return False
        task = self.get_task(task_id)
        if not task:
            return False
        return bool(task.get("fencing_token") == fencing_token)

    def invalidate_fencing_token(self, task_id: str, reason: str = "invalidated") -> str:
        """Rotate fencing token and clear active_pid so stale attempts cannot mutate state."""
        new_token = f"fence-invalidated-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"
        now = time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET fencing_token = ?, active_pid = NULL, active_pid_started_at = NULL, updated_at = ? WHERE task_id = ?",
                (new_token, now, task_id),
            )
            self.record_event(
                task_id,
                "FENCING_TOKEN_INVALIDATED",
                None,
                None,
                {"reason": reason, "new_token": new_token},
            )
        return new_token

    def transition_state(
        self,
        task_id: str,
        target_state: TaskState | str,
        expected_current: Optional[Tuple[TaskState, ...]] = None,
        fencing_token: Optional[str] = None,
        payload: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Validated state transition enforcing both state machine rules and fencing token."""
        task = self.get_task(task_id)
        if not task:
            raise ValueError(f"Unknown task_id: {task_id}")
        if fencing_token is not None and task.get("fencing_token") != fencing_token:
            raise ValueError(
                f"Fencing token mismatch for task {task_id}: expected {task.get('fencing_token')}, got {fencing_token}"
            )
        cur_state = TaskState(task["status"])
        tgt_state = target_state if isinstance(target_state, TaskState) else TaskState(target_state)
        if expected_current and cur_state not in expected_current:
            raise ValueError(
                f"State mismatch for task {task_id}: expected one of {[s.value for s in expected_current]}, got {cur_state.value}"
            )
        if cur_state != tgt_state:
            TaskStateMachine.validate_transition(cur_state, tgt_state)
        self.update_task_status(task_id, tgt_state, payload=payload)

    def save_verified_checkpoint(
        self,
        task_id: str,
        worker_result_json: str,
        workspace_path: str,
        revision_sha: Optional[str] = None,
        fencing_token: Optional[str] = None,
    ) -> None:
        """Persist QA-verified worker result, workspace path, and revision SHA before READY_TO_SUBMIT."""
        if fencing_token is not None and not self.validate_fencing_token(task_id, fencing_token):
            raise ValueError(f"Fencing token mismatch for task {task_id}")
        now = time.time()
        with self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET verified_result_json = ?, verified_workspace = ?, verified_revision = ?, updated_at = ?
                WHERE task_id = ?
                """,
                (worker_result_json, workspace_path, revision_sha, now, task_id),
            )

    def clear_verified_checkpoint(self, task_id: str) -> None:
        """Clear any saved READY_TO_SUBMIT checkpoint fields for `task_id`."""
        now = time.time()
        with self.conn:
            self.conn.execute(
                """
                UPDATE tasks
                SET verified_result_json = NULL, verified_workspace = NULL, verified_revision = NULL, updated_at = ?
                WHERE task_id = ?
                """,
                (now, task_id),
            )

    def increment_start_working_requeues(self, task_id: str) -> int:
        """Increment and return the count of transient start_working/get_mvs requeues for a task."""
        task = self.get_task(task_id)
        cur = int((task or {}).get("start_working_requeues") or 0) + 1
        now = time.time()
        with self.conn:
            self.conn.execute(
                "UPDATE tasks SET start_working_requeues = ?, updated_at = ? WHERE task_id = ?",
                (cur, now, task_id),
            )
        return cur

    def record_attempt(
        self,
        task_id: str,
        attempt_id: str,
        status: str,
        attempt_number: int = 1,
        elapsed_seconds: float = 0.0,
        billable_tokens: int = 0,
        result_json: Optional[Dict[str, Any]] = None,
        fencing_token: Optional[str] = None,
    ) -> None:
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO attempts (
                attempt_id, task_id, attempt_number, status,
                elapsed_seconds, billable_tokens, result_json, fencing_token, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attempt_id,
                task_id,
                attempt_number,
                status,
                elapsed_seconds,
                billable_tokens,
                json.dumps(result_json or {}),
                fencing_token,
                time.time(),
            ))

    def get_attempt_count(self, task_id: str) -> int:
        """Return the number of recorded attempts for a task."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT COUNT(*) AS cnt FROM attempts WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return int(row["cnt"] or 0) if row else 0

    def reconcile_orphaned_tasks(self, status_check_fn: Any = None) -> List[Dict[str, Any]]:
        """Reconcile tasks left in ASSIGNED, RUNNING, or VERIFYING state after an abrupt process crash.

        1. Terminates any lingering zombie OS worker process recorded in `active_pid`.
        2. Rotates `fencing_token` to fence off stale attempts.
        3. Queries Game Master (`status_check_fn`) first in case a submission already succeeded on the server
           before local SQLite was updated.
        4. Otherwise requeues unsubmitted tasks back to `QUEUED`.
        """
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM tasks
            WHERE status IN ('ASSIGNED', 'RUNNING', 'VERIFYING')
        """)
        rows = [dict(r) for r in cursor.fetchall()]
        requeued = []
        now = time.time()
        for t in rows:
            tid = t["task_id"]
            old_state = t["status"]

            # 1. Kill any orphaned OS subprocess from previous process instance ONLY if its creation timestamp matches
            stale_pid = t.get("active_pid")
            recorded_start = t.get("active_pid_started_at")
            if stale_pid and is_pid_alive(int(stale_pid)):
                if is_expected_worker_pid(
                    int(stale_pid),
                    recorded_start_time=recorded_start,
                    task_updated_at=t.get("updated_at"),
                ):
                    _terminate_pid_tree(int(stale_pid))
                else:
                    self.record_event(
                        tid,
                        "ORPHAN_PID_SKIPPED_REUSED",
                        old_state,
                        old_state,
                        {
                            "stale_pid": int(stale_pid),
                            "recorded_start": recorded_start,
                            "actual_start": get_process_create_time(int(stale_pid)),
                        },
                    )

            # 2. Rotate fencing token & clear active_pid so stale worker threads cannot commit
            self.invalidate_fencing_token(tid, reason="crash_recovery")

            # 3. Reconcile against Game Master if status_check_fn is provided
            gm_reconciled = False
            if status_check_fn is not None and not self.has_submitted(tid):
                try:
                    res = status_check_fn(tid)
                    if isinstance(res, dict) and isinstance(res.get("status"), str):
                        st = res["status"].strip().upper()
                        raw_score = res.get("score") if res.get("score") is not None else res.get("points")
                        score_val = int(float(raw_score)) if raw_score is not None else 0
                        if st in ("PASSED", "GRADED", "COMPLETED"):
                            self.record_submission(
                                task_id=tid,
                                status="PASSED",
                                score=score_val,
                                response_json=res,
                                git_revision=res.get("git_revision") or res.get("commit_sha"),
                                submission_id=res.get("submission_id"),
                            )
                            requeued.append({"task_id": tid, "from_state": old_state, "to_state": TaskState.PASSED.value})
                            gm_reconciled = True
                        elif st in ("FAILED", "REJECTED"):
                            self.record_submission(
                                task_id=tid,
                                status="FAILED",
                                score=0,
                                response_json=res,
                                git_revision=res.get("git_revision") or res.get("commit_sha"),
                                submission_id=res.get("submission_id"),
                            )
                            requeued.append({"task_id": tid, "from_state": old_state, "to_state": TaskState.FAILED.value})
                            gm_reconciled = True
                        elif st in ("ACCEPTED", "SUBMITTED", "GRADING"):
                            self.record_submission(
                                task_id=tid,
                                status="SUBMITTED",
                                score=score_val,
                                response_json=res,
                                git_revision=res.get("git_revision") or res.get("commit_sha"),
                                submission_id=res.get("submission_id"),
                            )
                            requeued.append({"task_id": tid, "from_state": old_state, "to_state": TaskState.SUBMITTED.value})
                            gm_reconciled = True
                except Exception:
                    pass

            if gm_reconciled:
                continue

            if not self.has_submitted(tid):
                with self.conn:
                    self.conn.execute(
                        "UPDATE tasks SET status = ?, active_pid = NULL, updated_at = ? WHERE task_id = ?",
                        (TaskState.QUEUED.value, now, tid),
                    )
                    self.record_event(
                        tid,
                        "CRASH_RECOVERY_REQUEUED",
                        old_state,
                        TaskState.QUEUED.value,
                        {"reason": "Task was active during process restart without submission"},
                    )
                requeued.append({"task_id": tid, "from_state": old_state, "to_state": TaskState.QUEUED.value})
        return requeued

    def has_submitted(self, task_id: str) -> bool:
        """Check if task has already been submitted or is currently in flight (idempotency guard)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM submissions WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        if not row:
            return False
        st = row["status"]
        return st in ("ACCEPTED", "PASSED", "FAILED", "SUBMIT_IN_FLIGHT", "SUBMITTED")

    def is_in_flight(self, task_id: str) -> bool:
        """Check if task has an in-flight network submission pending."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT status FROM submissions WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return bool(row and row["status"] == "SUBMIT_IN_FLIGHT")

    def get_submission(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Fetch submission record."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM submissions WHERE task_id = ?", (task_id,))
        row = cursor.fetchone()
        return dict(row) if row else None

    def reconcile_in_flight_submissions(self, status_check_fn: Any) -> List[Dict[str, Any]]:
        """Scan and reconcile any unresolved SUBMIT_IN_FLIGHT submissions against Game Master."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT * FROM submissions WHERE status = 'SUBMIT_IN_FLIGHT'")
        in_flight_rows = [dict(r) for r in cursor.fetchall()]
        reconciled = []
        for sub in in_flight_rows:
            task_id = sub["task_id"]
            try:
                res = status_check_fn(task_id)
                st = res.get("status")
                if st in ("ACCEPTED", "PASSED", "FAILED"):
                    score = int(res["points"]) if (st == "PASSED" and res.get("points") is not None) else 0
                    self.record_submission(
                        task_id=task_id,
                        status=st,
                        score=score,
                        response_json=res,
                        git_revision=sub.get("git_revision"),
                        submission_id=res.get("submission_id") or sub.get("submission_id"),
                    )
                    reconciled.append({"task_id": task_id, "status": st, "score": score})
                elif st in ("NOT_SUBMITTED", None):
                    now = time.time()
                    with self.conn:
                        self.conn.execute("DELETE FROM submissions WHERE task_id = ?", (task_id,))
                        self.conn.execute(
                            "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                            (TaskState.READY_TO_SUBMIT.value, now, task_id),
                        )
                        self.record_event(
                            task_id,
                            "RECONCILED_RESET",
                            TaskState.SUBMIT_IN_FLIGHT.value,
                            TaskState.READY_TO_SUBMIT.value,
                            {"reason": "Not submitted on server"},
                        )
                    reconciled.append({"task_id": task_id, "status": "RESET_TO_READY_TO_SUBMIT"})
            except Exception:
                pass
        return reconciled

    def start_submission(self, task_id: str, git_revision: str) -> bool:
        """Atomically record SUBMIT_IN_FLIGHT marker before network transmission."""
        sub = self.get_submission(task_id)
        if sub and sub["status"] in ("ACCEPTED", "PASSED", "FAILED"):
            return False  # Already permanently submitted

        now = time.time()
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO submissions (task_id, status, score, git_revision, submitted_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (task_id, "SUBMIT_IN_FLIGHT", 0, git_revision, now, now))
            self.conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskState.SUBMIT_IN_FLIGHT.value, now, task_id),
            )
            self.record_event(task_id, "SUBMISSION_IN_FLIGHT", TaskState.READY_TO_SUBMIT.value, TaskState.SUBMIT_IN_FLIGHT.value, {"git_revision": git_revision})
        return True

    def record_submission(
        self,
        task_id: str,
        status: str,
        score: int = 0,
        response_json: Optional[Dict[str, Any]] = None,
        git_revision: Optional[str] = None,
        submission_id: Optional[str] = None,
    ) -> bool:
        """Record a submission. Enforces once-only submission invariant and scoring separation."""
        sub = self.get_submission(task_id)
        if sub and sub["status"] in ("ACCEPTED", "PASSED", "FAILED") and status not in ("PASSED", "FAILED"):
            return False  # Block duplicate submission

        now = time.time()
        # Separate provisional from confirmed points (P0.4)
        if status == "PASSED":
            confirmed = int(score)
            provisional = int(score)
        elif status in ("ACCEPTED", "SUBMITTED", "GRADING"):
            confirmed = 0
            provisional = int(score)
        else:  # FAILED, REJECTED
            confirmed = 0
            provisional = 0

        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO submissions (
                task_id, status, score, provisional_points, confirmed_points,
                git_revision, submission_id, response_json, submitted_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task_id,
                status,
                score,
                provisional,
                confirmed,
                git_revision,
                submission_id,
                json.dumps(response_json or {}),
                now,
                now,
            ))

            if status == "PASSED":
                final_task_state = TaskState.PASSED.value
            elif status == "FAILED":
                final_task_state = TaskState.FAILED.value
            elif status in ("ACCEPTED", "SUBMITTED"):
                final_task_state = TaskState.SUBMITTED.value
            else:
                final_task_state = TaskState.REJECTED.value

            self.conn.execute(
                "UPDATE tasks SET status = ?, awarded_points = ?, active_pid = NULL, updated_at = ? WHERE task_id = ?",
                (final_task_state, float(confirmed), now, task_id),
            )
            self.record_event(task_id, "SUBMISSION_RECORDED", None, status, {"score": score})
        return True

    def update_submission_status(
        self,
        task_id: str,
        status: str,
        score: Optional[int] = None,
        response_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Update grading status of an existing submission (e.g. GRADING -> PASSED/FAILED)."""
        sub = self.get_submission(task_id)
        if not sub:
            return
        now = time.time()
        final_score = int(score if score is not None else sub.get("score", 0))
        if status == "PASSED":
            confirmed = final_score
            provisional = final_score
        elif status == "FAILED":
            confirmed = 0
            provisional = 0
        else:  # GRADING, ACCEPTED
            confirmed = 0
            provisional = final_score

        with self.conn:
            self.conn.execute("""
            UPDATE submissions SET status = ?, score = ?, provisional_points = ?, confirmed_points = ?, response_json = ?, updated_at = ?
            WHERE task_id = ?
            """, (status, final_score, provisional, confirmed, json.dumps(response_json or {}), now, task_id))

            if status == "PASSED":
                task_state = TaskState.PASSED.value
            elif status == "FAILED":
                task_state = TaskState.FAILED.value
            else:
                task_state = TaskState.SUBMITTED.value

            self.conn.execute(
                "UPDATE tasks SET status = ?, awarded_points = ?, active_pid = NULL, updated_at = ? WHERE task_id = ?",
                (task_state, float(confirmed), now, task_id),
            )
            self.record_event(task_id, "GRADING_STATUS_UPDATED", sub["status"], status, {"score": final_score})

    def get_total_confirmed_points(self) -> int:
        """Return sum of confirmed points awarded by Grader (PASSED submissions only)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(confirmed_points) AS total FROM submissions WHERE status = 'PASSED'")
        row = cursor.fetchone()
        return int(row["total"] or 0) if row else 0

    def get_total_billable_tokens(self) -> int:
        """Return total billable tokens consumed across all recorded attempts."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(billable_tokens) AS total FROM attempts")
        row = cursor.fetchone()
        return int(row["total"] or 0) if row else 0


    def get_total_provisional_points(self) -> int:
        """Return sum of provisional points (including in-progress grading)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(provisional_points) AS total FROM submissions")
        row = cursor.fetchone()
        return int(row["total"] or 0) if row else 0

    def reconcile_grading_lifecycle(
        self,
        status_check_fn: Any,
        timeout_seconds: float = 300.0,
    ) -> List[Dict[str, Any]]:
        """Poll GM for submissions in ACCEPTED/SUBMITTED/GRADING state until PASSED, FAILED, or timeout."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT * FROM submissions
            WHERE status IN ('ACCEPTED', 'SUBMITTED', 'GRADING')
        """)
        rows = [dict(r) for r in cursor.fetchall()]
        reconciled = []
        now = time.time()

        for sub in rows:
            task_id = sub["task_id"]
            submitted_at = sub.get("submitted_at") or now
            try:
                res = status_check_fn(task_id)
                st = res.get("status")
                if st in ("PASSED", "FAILED"):
                    pts = int(res["points"]) if (st == "PASSED" and res.get("points") is not None) else 0
                    self.update_submission_status(task_id, status=st, score=pts, response_json=res)
                    reconciled.append({"task_id": task_id, "status": st, "points": pts})
                elif (now - submitted_at) > timeout_seconds:
                    self.record_event(
                        task_id,
                        "GRADING_TIMEOUT_UNRESOLVED",
                        sub["status"],
                        "UNRESOLVED",
                        {"elapsed_waiting": round(now - submitted_at, 1)},
                    )
                    reconciled.append({"task_id": task_id, "status": "UNRESOLVED"})
            except Exception:
                pass

        return reconciled

    def record_event(
        self,
        task_id: Optional[str],
        event_type: str,
        from_state: Optional[str],
        to_state: Optional[str],
        payload: Dict[str, Any],
    ) -> None:
        with self.conn:
            self.conn.execute("""
            INSERT INTO events (task_id, event_type, from_state, to_state, timestamp, payload_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """, (
                task_id,
                event_type,
                from_state,
                to_state,
                time.time(),
                json.dumps(payload),
            ))

    def get_events(self, task_id: Optional[str] = None) -> List[Dict[str, Any]]:
        cursor = self.conn.cursor()
        if task_id:
            cursor.execute("SELECT * FROM events WHERE task_id = ? ORDER BY timestamp ASC", (task_id,))
        else:
            cursor.execute("SELECT * FROM events ORDER BY timestamp ASC")
        return [dict(r) for r in cursor.fetchall()]
