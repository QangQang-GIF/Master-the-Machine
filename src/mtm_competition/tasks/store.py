"""SQLite-backed deterministic TaskStore for competition state and idempotency."""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import TaskEnvelope, TaskState


class TaskStore:
    """Deterministic, crash-resilient persistence layer for tasks, attempts, and submissions."""

    def __init__(self, db_path: str | Path = ":memory:"):
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_tables()

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
                deadline_epoch REAL,
                time_budget_seconds INTEGER DEFAULT 420,
                status TEXT NOT NULL,
                repo_path TEXT,
                base_revision TEXT,
                mvs_json TEXT,
                evidence_required_json TEXT,
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

            try:
                self.conn.execute("ALTER TABLE tasks ADD COLUMN time_budget_seconds INTEGER DEFAULT 420")
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

    def record_attempt(
        self,
        task_id: str,
        attempt_id: str,
        status: str,
        attempt_number: int = 1,
        elapsed_seconds: float = 0.0,
        billable_tokens: int = 0,
        result_json: Optional[Dict[str, Any]] = None,
    ) -> None:
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO attempts (
                attempt_id, task_id, attempt_number, status,
                elapsed_seconds, billable_tokens, result_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                attempt_id,
                task_id,
                attempt_number,
                status,
                elapsed_seconds,
                billable_tokens,
                json.dumps(result_json or {}),
                time.time(),
            ))

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
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (final_task_state, now, task_id),
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
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (task_state, now, task_id),
            )
            self.record_event(task_id, "GRADING_STATUS_UPDATED", sub["status"], status, {"score": final_score})

    def get_total_confirmed_points(self) -> int:
        """Return sum of confirmed points awarded by Grader (PASSED submissions only)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT SUM(confirmed_points) AS total FROM submissions WHERE status = 'PASSED'")
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
