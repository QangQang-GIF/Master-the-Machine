"""
tests/test_telemetry.py: Unit tests for runner/telemetry.py.
Verifies schema compliance against schemas/progress-event.schema.json (LOG-01, LOG-02).
"""

from __future__ import annotations

import concurrent.futures
import json
from pathlib import Path
from typing import Any, Dict

import sys
from pathlib import Path

import jsonschema
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.telemetry_validator import validate_progress_file
except ModuleNotFoundError:
    from fixtures.telemetry_validator import validate_progress_file
from runner.telemetry import TelemetryLogger, clean_summary, validate_progress_event


def test_clean_summary():
    """Verify clean_summary sanitizes newlines, nulls, and bounds length."""
    raw = "Line 1\r\nLine 2\0with null\nLine 3"
    cleaned = clean_summary(raw, max_len=50)
    assert "\r" not in cleaned
    assert "\n" not in cleaned
    assert "\0" not in cleaned
    assert len(cleaned) <= 50

    empty = clean_summary("")
    assert empty == "Progress update"


def test_telemetry_all_14_event_types(tmp_path):
    """LOG-01, LOG-02: Emit all 14 event types and verify schema compliance and persistent playerSessionId."""
    log_file = tmp_path / "test_match.progress.ndjson"
    session_id = "test-session-12345"
    logger = TelemetryLogger(log_path=log_file, player_session_id=session_id)

    assert logger.player_session_id == session_id

    # 1. ORCHESTRATOR_STARTED
    logger.emit_orchestrator_started("Orchestrator online")

    # 2. ORCHESTRATOR_RESTARTED
    logger.emit_orchestrator_restarted(attempt=1, phase="PREPARATION", reason="sigterm", summary="Restarted")

    # 3. PREPARATION_STATUS
    logger.emit_preparation_status(stage="GIT_AUTH", status="COMPLETED", summary="Git authenticated")

    # 4. ENVIRONMENT_READY
    logger.emit_environment_ready(
        request_id="req-init-1",
        repo_url="https://github.com/contest/repo.git",
        branch="main",
        folder="repo",
        revision="a" * 40,
        build="PASS",
        match_login="PASS",
    )

    # 5. GAME_START_STATUS
    logger.emit_game_start_status(stage="BACKLOG_SYNC", status="COMPLETED", summary="Backlog synced")

    # 6. BACKLOG_STATUS
    logger.emit_backlog_status(
        backlog_id="TASK-01",
        title="Fix Null Pointer",
        status="IN_PROGRESS",
        summary="Working on task",
        revision=None,
        receipt_id=None,
    )

    # 7. BACKLOG_TESTS
    logger.emit_backlog_tests(
        backlog_id="TASK-01",
        status="PASSED",
        required=4,
        executed=4,
        passed=4,
        failed=0,
        skipped=0,
        summary="All tests passed",
    )

    # 8. GAME_MASTER_API
    logger.emit_game_master_api(
        operation="start_work",
        phase="SUCCEEDED",
        summary="POST /backlogs/start ok",
        backlog_id="TASK-01",
        method="POST",
        origin="https://gamemaster.local",
        path="/api/v1/matches/123/backlogs/start",
        http_status=201,
        request_id="req-start-1",
    )

    # 9. MODEL_USAGE
    logger.emit_model_usage(
        model="gpt-4o",
        input_tokens=1500,
        cached_input_tokens=800,
        output_tokens=350,
        reasoning_tokens=50,
        summary="Prompt tokens used",
    )

    # 10. TOOL_ACTIVITY
    logger.emit_tool_activity(
        tool_name="git_status",
        operation="check_clean",
        phase="SUCCEEDED",
        summary="Repo is clean",
        backlog_id="TASK-01",
    )

    # 11. NETWORK_ACTIVITY
    logger.emit_network_activity(
        operation="fetch_task",
        method="GET",
        origin="https://gamemaster.local",
        path="/api/v1/matches/123",
        phase="SUCCEEDED",
        summary="Match state fetched",
        http_status=200,
        request_id="req-gm-01",
    )

    # 12. BLOCKER_OPENED
    logger.emit_blocker_opened(blocker_id="blk-99", summary="Token rate limit reached", backlog_id="TASK-01")

    # 13. BLOCKER_RESOLVED
    logger.emit_blocker_resolved(blocker_id="blk-99", summary="Rate limit window cleared")

    # 14. ORCHESTRATOR_ENDED
    logger.emit_orchestrator_ended(status="COMPLETED", summary="Match ended successfully")

    assert logger.events_written_count == 14

    # Validate using the strict validator fixture
    events = validate_progress_file(log_file, expected_session_id=session_id)
    assert len(events) == 14

    all_types = [e["type"] for e in events]
    expected_all_14 = [
        "ORCHESTRATOR_STARTED",
        "ORCHESTRATOR_RESTARTED",
        "PREPARATION_STATUS",
        "ENVIRONMENT_READY",
        "GAME_START_STATUS",
        "BACKLOG_STATUS",
        "BACKLOG_TESTS",
        "GAME_MASTER_API",
        "MODEL_USAGE",
        "TOOL_ACTIVITY",
        "NETWORK_ACTIVITY",
        "BLOCKER_OPENED",
        "BLOCKER_RESOLVED",
        "ORCHESTRATOR_ENDED",
    ]
    assert all_types == expected_all_14

    # Verify all events have schemaVersion == 1 and persistent playerSessionId
    for e in events:
        assert e["schemaVersion"] == 1
        assert e["playerSessionId"] == session_id


def test_additional_properties_rejected(tmp_path):
    """LOG-01: Verify that extra fields fail validation due to additionalProperties: false."""
    log_file = tmp_path / "extra_prop.ndjson"
    logger = TelemetryLogger(log_path=log_file, player_session_id="valid-session-1")

    with pytest.raises(jsonschema.ValidationError):
        logger.log_event({
            "type": "ORCHESTRATOR_STARTED",
            "summary": "Starting",
            "unauthorized_extra_field": "illegal",
        })


def test_missing_mandatory_field_rejected(tmp_path):
    """LOG-01: Verify that omitting mandatory field fails schema validation."""
    log_file = tmp_path / "missing_field.ndjson"
    logger = TelemetryLogger(log_path=log_file, player_session_id="valid-session-1")

    # PREPARATION_STATUS requires stage, status, summary
    with pytest.raises(jsonschema.ValidationError):
        logger.log_event({
            "type": "PREPARATION_STATUS",
            "stage": "GIT_AUTH",
            # missing status and summary
        })


def test_invalid_enum_rejected(tmp_path):
    """LOG-01: Verify that invalid enum value fails schema validation."""
    log_file = tmp_path / "invalid_enum.ndjson"
    logger = TelemetryLogger(log_path=log_file, player_session_id="valid-session-1")

    with pytest.raises(jsonschema.ValidationError):
        logger.log_event({
            "type": "PREPARATION_STATUS",
            "stage": "INVALID_STAGE_NAME",
            "status": "COMPLETED",
            "summary": "testing",
        })


def test_invalid_session_id_format(tmp_path):
    """LOG-02: Verify invalid playerSessionId format is rejected."""
    log_file = tmp_path / "invalid_sess.ndjson"

    # Starts with illegal character or contains space
    with pytest.raises(ValueError, match="Invalid playerSessionId"):
        TelemetryLogger(log_path=log_file, player_session_id=" invalid space")

    with pytest.raises(ValueError, match="Invalid playerSessionId"):
        TelemetryLogger(log_path=log_file, player_session_id="@illegal_start")


def test_match_code_filename_derivation(tmp_path):
    """LOG-01: Verify <matchCode>.progress.ndjson is created when match_code is provided."""
    logger = TelemetryLogger(log_path=tmp_path, match_code="ABCD1234")
    assert logger.log_file.name == "ABCD1234.progress.ndjson"
    logger.emit_orchestrator_started("Init")
    assert logger.log_file.exists()


def test_multithreaded_concurrent_logging(tmp_path):
    """Verify thread-safe concurrent append-only writing."""
    log_file = tmp_path / "concurrent.progress.ndjson"
    logger = TelemetryLogger(log_path=log_file, player_session_id="thread-test-session")

    num_threads = 4
    events_per_thread = 5

    def worker(worker_id: int):
        for i in range(events_per_thread):
            logger.emit_tool_activity(
                tool_name=f"tool_{worker_id}",
                operation=f"op_{i}",
                phase="SUCCEEDED",
                summary=f"Worker {worker_id} event {i}",
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker, w) for w in range(num_threads)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    events = validate_progress_file(log_file, expected_session_id="thread-test-session")
    assert len(events) == num_threads * events_per_thread


def test_knowledge_bootstrap_generate_project_map(tmp_path):
    """PREP-02: Verify KnowledgeBootstrap generates PROJECT_MAP.md outside repo."""
    from mtm_knowledge.bootstrap import KnowledgeBootstrap, lookup_section, incremental_refresh, get_knowledge_or_fallback

    repo_dir = tmp_path / "challenge_repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "app.py").write_text("def hello(): pass\n", encoding="utf-8")
    (repo_dir / "test_app.py").write_text("def test_hello(): pass\n", encoding="utf-8")

    bootstrap = KnowledgeBootstrap(repo_path=repo_dir, match_code="KWO5GPMP")

    # Failure when generating inside repo (PREP-02 strict requirement)
    inside_path = repo_dir / "PROJECT_MAP.md"
    with pytest.raises(ValueError, match="must never be created inside"):
        bootstrap.generate_project_map(inside_path)

    # Success when generating outside repo
    outside_path = tmp_path / "knowledge" / "PROJECT_MAP.md"
    generated_path = bootstrap.generate_project_map(outside_path)
    assert generated_path.exists()
    content = generated_path.read_text(encoding="utf-8")

    # Verify all 8 sections exist
    for i in range(1, 9):
        assert f"## {i}." in content

    # KNO-01: Section lookup
    sec2 = lookup_section(generated_path, 2)
    assert "Technology & Local Environment" in sec2
    assert "Platform" in sec2

    sec_arch = lookup_section(generated_path, "Architecture")
    assert "Architecture & Request Flow" in sec_arch

    # KNO-02: Incremental refresh
    (repo_dir / "new_service.py").write_text("class NewService: pass\n", encoding="utf-8")
    refreshed = incremental_refresh(generated_path, repo_dir, changed_files=["new_service.py"], current_revision="b" * 40)
    assert "b" * 40 in refreshed
    assert "new_service.py" in refreshed

    # KNO-03: Fallback lookup
    # 1. With valid map
    know = get_knowledge_or_fallback(repo_dir, generated_path, query="architecture flow")
    assert "Architecture" in know

    # 2. When map is missing (graceful fallback)
    fallback = get_knowledge_or_fallback(repo_dir, tmp_path / "nonexistent.md", query="hello app")
    assert "app.py" in fallback or "Repository revision" in fallback

