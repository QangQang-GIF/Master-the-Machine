"""
Contract Tests: LOG-01..02.
Verifies progress.ndjson telemetry events conform strictly to schemas/progress-event.schema.json,
with additionalProperties: false enforcement and persistent playerSessionId tracking.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict
import jsonschema
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.telemetry_validator import (
        validate_progress_ndjson,
        validate_progress_file,
        validate_event_object,
        get_cached_schema,
    )
except ModuleNotFoundError:
    from fixtures.telemetry_validator import (
        validate_progress_ndjson,
        validate_progress_file,
        validate_event_object,
        get_cached_schema,
    )


def test_log_01_progress_ndjson_schema_compliance(tmp_path: Path):
    """
    LOG-01: progress.ndjson Schema Compliance.
    Validates progress events written to NDJSON conform strictly to progress-event.schema.json
    with additionalProperties: false.
    Rejects extra properties and missing required properties.
    """
    log_file = tmp_path / "match.progress.ndjson"
    session_id = "8c0e1b22-1111-4222-8333-444444444444"

    valid_event_1 = {
        "schemaVersion": 1,
        "playerSessionId": session_id,
        "type": "ORCHESTRATOR_STARTED",
        "summary": "Player orchestrator initialized",
    }
    valid_event_2 = {
        "schemaVersion": 1,
        "playerSessionId": session_id,
        "type": "PREPARATION_STATUS",
        "stage": "ENVIRONMENT",
        "status": "IN_PROGRESS",
        "summary": "Checking PostgreSQL and Node environment",
    }

    # Write valid NDJSON
    log_file.write_text(
        f"{json.dumps(valid_event_1)}\n{json.dumps(valid_event_2)}\n",
        encoding="utf-8",
    )

    events = validate_progress_file(log_file, expected_session_id=session_id)
    assert len(events) == 2

    # Negative Case A: Extra undeclared property rejected (additionalProperties: false)
    invalid_extra_prop = {
        "schemaVersion": 1,
        "playerSessionId": session_id,
        "type": "ORCHESTRATOR_STARTED",
        "summary": "Started with extra field",
        "unauthorized_extra_field": "disallowed",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_event_object(invalid_extra_prop)

    # Negative Case B: Missing mandatory property 'summary'
    invalid_missing_summary = {
        "schemaVersion": 1,
        "playerSessionId": session_id,
        "type": "ORCHESTRATOR_STARTED",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_event_object(invalid_missing_summary)

    # Negative Case C: Invalid schemaVersion
    invalid_schema_ver = {
        "schemaVersion": 2,
        "playerSessionId": session_id,
        "type": "ORCHESTRATOR_STARTED",
        "summary": "Wrong schema version",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_event_object(invalid_schema_ver)


def test_log_02_telemetry_event_fields_and_playersessionid(tmp_path: Path):
    """
    LOG-02: Telemetry event fields and persistent playerSessionId tracking.
    All events within a match lifecycle must share the exact same persistent playerSessionId.
    Divergent or empty playerSessionId values are strictly rejected.
    """
    log_file = tmp_path / "match_lifecycle.progress.ndjson"
    session_id = "session-player-12345"

    events_stream = [
        {
            "schemaVersion": 1,
            "playerSessionId": session_id,
            "type": "ORCHESTRATOR_STARTED",
            "summary": "Orchestrator launched",
        },
        {
            "schemaVersion": 1,
            "playerSessionId": session_id,
            "type": "PREPARATION_STATUS",
            "stage": "ENVIRONMENT",
            "status": "COMPLETED",
            "summary": "Environment verified with doctor READY",
        },
        {
            "schemaVersion": 1,
            "playerSessionId": session_id,
            "type": "ENVIRONMENT_READY",
            "requestId": "req-prep-1",
            "repository": {
                "url": "https://git.example.com/repo.git",
                "branch": "main",
                "folder": "repo",
            },
            "revision": "a" * 40,
            "build": "PASS",
            "matchLogin": "PASS",
        },
    ]

    log_file.write_text("\n".join(json.dumps(e) for e in events_stream) + "\n", encoding="utf-8")
    parsed = validate_progress_file(log_file, expected_session_id=session_id)
    assert len(parsed) == 3

    # Divergent playerSessionId in stream
    bad_stream = [
        {
            "schemaVersion": 1,
            "playerSessionId": session_id,
            "type": "ORCHESTRATOR_STARTED",
            "summary": "First event",
        },
        {
            "schemaVersion": 1,
            "playerSessionId": "different-session-uuid",
            "type": "ORCHESTRATOR_STARTED",
            "summary": "Second event with divergent session",
        },
    ]
    bad_log_file = tmp_path / "bad_stream.progress.ndjson"
    bad_log_file.write_text("\n".join(json.dumps(e) for e in bad_stream) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="diverged"):
        validate_progress_file(bad_log_file)

    # Empty playerSessionId rejected by schema pattern
    empty_session_event = {
        "schemaVersion": 1,
        "playerSessionId": "",
        "type": "ORCHESTRATOR_STARTED",
        "summary": "Event with empty session",
    }
    with pytest.raises(jsonschema.ValidationError):
        validate_event_object(empty_session_event)


def test_log_03_origin_parsing_and_submitted_status_strict_validation(tmp_path: Path):
    """
    LOG-03: Verify URL origin parsing with path prefixes and SUBMITTED receipt/revision enforcement.
    """
    from runner.telemetry import TelemetryLogger

    log_file = tmp_path / "telemetry_test.ndjson"
    logger = TelemetryLogger(log_path=log_file, player_session_id="session-strict-01")

    # 1. URL origin parsing retains https://<netloc> even with path prefixes
    ev_api = logger.emit_game_master_api(
        operation="SUBMIT",
        phase="SUCCEEDED",
        summary="Submitted via API",
        backlog_id="TASK-01",
        method="POST",
        origin="https://ai-fest-five.vercel.app/api/v1",
        path="/api/v1/matches/abc/submissions",
        http_status=201,
        request_id="req-12345",
    )
    assert ev_api["origin"] == "https://ai-fest-five.vercel.app"

    # 2. Non-HTTPS origin sanitized to None per schema ^https://[^/?#]+$ pattern
    ev_http = logger.emit_game_master_api(
        operation="START",
        phase="SUCCEEDED",
        summary="Started local",
        backlog_id="TASK-01",
        method="POST",
        origin="http://127.0.0.1:8080/api/v1",
        path="/api/v1/matches/abc/backlogs/start",
        http_status=200,
    )
    assert ev_http["origin"] is None

    # 3. SUBMITTED event requires non-empty receipt_id and 40-64 char hex revision
    with pytest.raises(ValueError, match="SUBMITTED event requires a valid non-empty receiptId"):
        logger.emit_backlog_status(
            backlog_id="TASK-01",
            title="Task 1",
            status="SUBMITTED",
            summary="Submitted",
            receipt_id="",
            revision="a" * 40,
        )

    with pytest.raises(ValueError, match="SUBMITTED event requires a valid 40-64 char hex revision"):
        logger.emit_backlog_status(
            backlog_id="TASK-01",
            title="Task 1",
            status="SUBMITTED",
            summary="Submitted",
            receipt_id="receipt-1234",
            revision="invalid-non-hex-sha",
        )

    # Valid SUBMITTED event succeeds and validates against schema
    ev_submitted = logger.emit_backlog_status(
        backlog_id="TASK-01",
        title="Task 1",
        status="SUBMITTED",
        summary="Submitted successfully",
        receipt_id="sub-rec-12345",
        revision="a" * 40,
    )
    assert ev_submitted["receiptId"] == "sub-rec-12345"
    assert ev_submitted["revision"] == "a" * 40
