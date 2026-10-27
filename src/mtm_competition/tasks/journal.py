"""SubmissionJournal: Durable SQLite ledger for in-flight submission journaling and idempotency replay.

Requirement R2 & Wire Invariants:
1. Store canonical serialized request bytes, SHA256, idempotency key, and client submission UUID before HTTP dispatch.
2. On network timeout or transient 5xx/429, retrieve exact saved attempt to replay identical UUID, header, and body bytes.
3. Reject generating a new UUID or altered payload for an unresolved attempt (API-06).
4. Record receipt on HTTP 201 receipt (status ACCEPTED) without marking GRADED or PASSED (LIFE-03).
"""
from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union


class SubmissionJournal:
    """Durable SQLite ledger persisting and reconciling in-flight submissions."""

    def __init__(
        self,
        db_path: Union[str, Path, sqlite3.Connection] = ":memory:",
        conn: Optional[sqlite3.Connection] = None,
    ) -> None:
        if isinstance(db_path, sqlite3.Connection):
            self.conn = db_path
            self.db_path = ":memory:"
            self._owns_conn = False
        elif conn is not None:
            self.conn = conn
            self.db_path = str(db_path)
            self._owns_conn = False
        else:
            self.db_path = str(db_path)
            if self.db_path != ":memory:":
                Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(self.db_path, check_same_thread=False, timeout=30.0)
            self._owns_conn = True
            if self.db_path != ":memory:":
                try:
                    self.conn.execute("PRAGMA journal_mode=WAL")
                    self.conn.execute("PRAGMA synchronous=FULL")
                    self.conn.execute("PRAGMA busy_timeout=30000")
                except sqlite3.DatabaseError:
                    pass

        self.conn.row_factory = sqlite3.Row
        self._init_tables()

    def close(self) -> None:
        """Close connection if owned by this instance."""
        if self._owns_conn:
            try:
                self.conn.close()
            except Exception:
                pass

    def __enter__(self) -> "SubmissionJournal":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _init_tables(self) -> None:
        with self.conn:
            self.conn.execute("""
            CREATE TABLE IF NOT EXISTS submission_journal (
                match_uuid TEXT NOT NULL,
                backlog_item_key TEXT NOT NULL,
                client_submission_id TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                request_body_sha256 TEXT NOT NULL,
                request_body_bytes BLOB NOT NULL,
                git_revision TEXT NOT NULL,
                remote_ref TEXT NOT NULL,
                state TEXT NOT NULL,
                receipt_id TEXT,
                last_http_code INTEGER,
                last_error_code TEXT,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                PRIMARY KEY (match_uuid, backlog_item_key)
            );
            """)

    def record_in_flight(
        self,
        match_uuid: str,
        backlog_item_key: str,
        client_submission_id: str,
        idempotency_key: str,
        request_body_bytes: bytes,
        git_revision: str,
        remote_ref: str = "main",
    ) -> str:
        """Calculate SHA256 and atomically store attempt as IN_FLIGHT before HTTP dispatch.

        Fails closed if an unresolved in-flight attempt exists with a different UUID or altered body.
        Returns request_body_sha256.
        """
        if not isinstance(request_body_bytes, bytes):
            raise TypeError("request_body_bytes must be bytes")

        computed_sha = hashlib.sha256(request_body_bytes).hexdigest()
        clean_match = match_uuid.strip()
        clean_key = backlog_item_key.strip()
        now = time.time()

        existing = self.get_entry(clean_match, clean_key)
        if existing:
            st = existing["state"]
            if st == "ACCEPTED":
                raise ValueError(
                    f"Submission for {clean_key} in match {clean_match} already ACCEPTED with receipt {existing.get('receipt_id')}"
                )
            if st == "IN_FLIGHT":
                # Verify that client_submission_id and idempotency_key match
                if existing["client_submission_id"] != client_submission_id.strip():
                    raise ValueError(
                        f"Unresolved in-flight submission exists for {clean_key} with clientSubmissionId "
                        f"'{existing['client_submission_id']}'. Rejecting generation of new UUID '{client_submission_id}'."
                    )
                if existing["idempotency_key"] != idempotency_key.strip():
                    raise ValueError(
                        f"Unresolved in-flight submission exists for {clean_key} with idempotencyKey "
                        f"'{existing['idempotency_key']}'. Rejecting generation of new key '{idempotency_key}'."
                    )
                # Verify body has not been altered
                self.verify_or_abort(clean_match, clean_key, request_body_bytes)

                # Replay verification passed; update timestamp
                with self.conn:
                    self.conn.execute(
                        "UPDATE submission_journal SET updated_at = ? WHERE match_uuid = ? AND backlog_item_key = ?",
                        (now, clean_match, clean_key),
                    )
                return computed_sha

        # Insert new in-flight record or replace FAILED attempt
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO submission_journal (
                    match_uuid, backlog_item_key, client_submission_id, idempotency_key,
                    request_body_sha256, request_body_bytes, git_revision, remote_ref,
                    state, receipt_id, last_http_code, last_error_code, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'IN_FLIGHT', NULL, NULL, NULL, ?, ?)
                ON CONFLICT(match_uuid, backlog_item_key) DO UPDATE SET
                    client_submission_id = excluded.client_submission_id,
                    idempotency_key = excluded.idempotency_key,
                    request_body_sha256 = excluded.request_body_sha256,
                    request_body_bytes = excluded.request_body_bytes,
                    git_revision = excluded.git_revision,
                    remote_ref = excluded.remote_ref,
                    state = 'IN_FLIGHT',
                    receipt_id = NULL,
                    last_http_code = NULL,
                    last_error_code = NULL,
                    updated_at = excluded.updated_at
                WHERE submission_journal.state != 'ACCEPTED'
                """,
                (
                    clean_match,
                    clean_key,
                    client_submission_id.strip(),
                    idempotency_key.strip(),
                    computed_sha,
                    request_body_bytes,
                    git_revision.strip(),
                    remote_ref.strip(),
                    now,
                    now,
                ),
            )
        return computed_sha

    def get_in_flight(self, match_uuid: str, backlog_item_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve existing IN_FLIGHT attempt for timeout/restart replay."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM submission_journal WHERE match_uuid = ? AND backlog_item_key = ? AND state = 'IN_FLIGHT'",
            (match_uuid.strip(), backlog_item_key.strip()),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_entry(self, match_uuid: str, backlog_item_key: str) -> Optional[Dict[str, Any]]:
        """Retrieve submission journal record regardless of state."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT * FROM submission_journal WHERE match_uuid = ? AND backlog_item_key = ?",
            (match_uuid.strip(), backlog_item_key.strip()),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_in_flight(self, match_uuid: Optional[str] = None) -> List[Dict[str, Any]]:
        """Retrieve all currently IN_FLIGHT entries, optionally filtered by match."""
        cursor = self.conn.cursor()
        if match_uuid is not None:
            cursor.execute(
                "SELECT * FROM submission_journal WHERE match_uuid = ? AND state = 'IN_FLIGHT' ORDER BY created_at ASC",
                (match_uuid.strip(),),
            )
        else:
            cursor.execute(
                "SELECT * FROM submission_journal WHERE state = 'IN_FLIGHT' ORDER BY created_at ASC"
            )
        return [dict(r) for r in cursor.fetchall()]

    def verify_or_abort(self, match_uuid: str, backlog_item_key: str, request_body_bytes: bytes) -> bool:
        """Check SHA256 against saved attempt; fails closed if body altered during IN_FLIGHT."""
        entry = self.get_entry(match_uuid.strip(), backlog_item_key.strip())
        if not entry:
            return True
        if entry["state"] in ("REJECTED", "FAILED"):
            return True

        computed_sha = hashlib.sha256(request_body_bytes).hexdigest()
        saved_sha = entry["request_body_sha256"]
        if computed_sha != saved_sha:
            raise ValueError(
                f"Submission payload conflict for {backlog_item_key} in match {match_uuid}: "
                f"saved requestBodySha256 is '{saved_sha}', but current payload SHA256 is '{computed_sha}'. "
                "Failing closed to prevent idempotency conflict."
            )
        return True

    def record_receipt(
        self,
        match_uuid: str,
        backlog_item_key: str,
        receipt_id: str,
        http_code: Optional[int] = None,
        http_status: Optional[int] = None,
    ) -> None:
        """Update status to ACCEPTED with receipt_id. Decouples from completion: does NOT mark GRADED/PASSED."""
        clean_match = match_uuid.strip()
        clean_key = backlog_item_key.strip()
        status_code = http_code if http_code is not None else (http_status if http_status is not None else 201)
        now = time.time()

        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_journal
                SET state = 'ACCEPTED',
                    receipt_id = ?,
                    last_http_code = ?,
                    updated_at = ?
                WHERE match_uuid = ? AND backlog_item_key = ?
                """,
                (receipt_id, status_code, now, clean_match, clean_key),
            )

    def record_failure(
        self,
        match_uuid: str,
        backlog_item_key: str,
        http_code: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> None:
        """Record fatal or rejected submission attempt."""
        clean_match = match_uuid.strip()
        clean_key = backlog_item_key.strip()
        now = time.time()

        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_journal
                SET state = 'FAILED',
                    last_http_code = ?,
                    last_error_code = ?,
                    updated_at = ?
                WHERE match_uuid = ? AND backlog_item_key = ?
                """,
                (http_code, error_code, now, clean_match, clean_key),
            )

    def record_rejection(
        self,
        match_uuid: str,
        backlog_item_key: str,
        http_code: Optional[int] = None,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
    ) -> None:
        """Record explicit server rejection (e.g. 400, 422 VALIDATION_ERROR).

        Transitions state from IN_FLIGHT to REJECTED, releasing the in-flight lock
        so a corrected payload and new submission attempt can be safely registered.
        """
        clean_match = match_uuid.strip()
        clean_key = backlog_item_key.strip()
        now = time.time()
        err_val = error_code or error_message

        with self.conn:
            self.conn.execute(
                """
                UPDATE submission_journal
                SET state = 'REJECTED',
                    last_http_code = ?,
                    last_error_code = ?,
                    updated_at = ?
                WHERE match_uuid = ? AND backlog_item_key = ?
                """,
                (http_code, err_val, now, clean_match, clean_key),
            )
