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
                response_json TEXT,
                submitted_at REAL
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

    def add_task(self, envelope: TaskEnvelope, match_id: str = "default_match") -> None:
        """Register a new task discovered from Game Master."""
        now = time.time()
        with self.conn:
            self.conn.execute("""
            INSERT OR REPLACE INTO tasks (
                task_id, match_id, task_type, title, description, points,
                deadline_epoch, status, repo_path, base_revision, mvs_json,
                evidence_required_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                envelope.task_id,
                match_id,
                envelope.task_type,
                envelope.title,
                envelope.description,
                envelope.points,
                envelope.deadline_epoch,
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
        """Check if task has already been submitted (idempotency guard)."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM submissions WHERE task_id = ?", (task_id,))
        return cursor.fetchone() is not None

    def record_submission(
        self,
        task_id: str,
        status: str,
        score: int = 0,
        response_json: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Record a submission. Enforces once-only submission invariant."""
        if self.has_submitted(task_id):
            return False  # Block duplicate submission

        with self.conn:
            self.conn.execute("""
            INSERT INTO submissions (task_id, status, score, response_json, submitted_at)
            VALUES (?, ?, ?, ?, ?)
            """, (
                task_id,
                status,
                score,
                json.dumps(response_json or {}),
                time.time(),
            ))
            self.conn.execute(
                "UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?",
                (TaskState.SUBMITTED.value, time.time(), task_id),
            )
            self.record_event(task_id, "SUBMISSION_RECORDED", None, status, {"score": score})
        return True

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
