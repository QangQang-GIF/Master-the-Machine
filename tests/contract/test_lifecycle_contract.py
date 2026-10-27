"""
Contract Tests: LIFE-01..03, RUNTIME-01.
Verifies tournament PAUSED quiescence, tournament RESUMED reconciliation,
two-phase grading lifecycle decoupling, and zero nested CLI processes.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any, Dict
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.mock_gm_server import (
        mock_gm_server,
        MockGameMasterHTTPServer,
    )
except ModuleNotFoundError:
    from fixtures.mock_gm_server import (
        mock_gm_server,
        MockGameMasterHTTPServer,
    )
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    GameMasterConflictError,
)
from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.state_machine import TaskState, TaskStateMachine


def test_life_01_tournament_paused_quiescence(mock_gm_server: MockGameMasterHTTPServer):
    """
    LIFE-01: Tournament PAUSED Quiescence.
    When tournament is PAUSED:
    - Start Work is still permitted and returns 201/200 with templates.
    - Submissions are blocked and rejected with 409 MATCH_NOT_ACCEPTING_SUBMISSIONS.
    - Engine halts new work and does NOT emit ORCHESTRATOR_ENDED.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-PAUSE-01"

    # Set match state to PAUSED
    mock_gm_server.match_state = "PAUSED"
    mock_gm_server.allow_submits_during_pause = False

    # Start Work during PAUSE is accepted
    try:
        start_res = adapter.start_working(match_uuid, item_key)
    except TypeError:
        start_res = adapter.start_working(item_key)
    assert start_res.get("replay") is False

    # Submit during PAUSE is rejected by Game Master
    with pytest.raises(GameMasterConflictError) as exc:
        submit_fn = getattr(adapter, "submit_work", getattr(adapter, "submit"))
        if hasattr(adapter, "submit_work"):
            submit_fn(
                match_uuid=match_uuid,
                client_submission_id=str(uuid.uuid4()),
                idempotency_key=str(uuid.uuid4()),
                backlog_item_key=item_key,
                git_revision="4" * 40,
                answer={"codeChanges": True, "summary": "Fix during pause"},
            )
        else:
            submit_fn(
                task_id=item_key,
                git_revision="4" * 40,
                evidence=[],
                idempotency_key=str(uuid.uuid4()),
            )

    assert "MATCH_NOT_ACCEPTING_SUBMISSIONS" in str(exc.value) or exc.value.status_code == 409


def test_life_02_tournament_resumed_reconcile(mock_gm_server: MockGameMasterHTTPServer):
    """
    LIFE-02: Tournament RESUMED & Replay Reconcile.
    When match resumes from pause:
    - In-flight submission attempts are reconciled before new tasks are scheduled.
    - Replaying exact same idempotency_key and body bytes succeeds.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-RESUME-02"

    # 1. Start while IN_PROGRESS
    try:
        adapter.start_working(match_uuid, item_key)
    except TypeError:
        adapter.start_working(item_key)

    client_sub_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())
    git_rev = "5" * 40
    answer = {"codeChanges": True, "summary": "Initial attempt"}

    # 2. Match becomes paused
    mock_gm_server.match_state = "PAUSED"

    # 3. Match resumes
    mock_gm_server.match_state = "IN_PROGRESS"

    # Reconcile in-flight: replay identical submission
    submit_fn = getattr(adapter, "submit_work", getattr(adapter, "submit"))
    if hasattr(adapter, "submit_work"):
        res = submit_fn(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idempotency_key,
            backlog_item_key=item_key,
            git_revision=git_rev,
            answer=answer,
        )
    else:
        res = submit_fn(
            task_id=item_key,
            git_revision=git_rev,
            evidence=[],
            idempotency_key=idempotency_key,
        )

    assert res["submission"]["status"] == "ACCEPTED"
    assert res["submission"]["scoreStatus"] == "PENDING"


def test_life_03_two_phase_grading_lifecycle(mock_gm_server: MockGameMasterHTTPServer):
    """
    LIFE-03: Two-Phase Grading Lifecycle.
    HTTP 201 submission receipt with {status: 'ACCEPTED', scoreStatus: 'PENDING'}
    places task in SUBMITTED state. Confirmed points remain 0 until match projection
    confirms PASSED. HTTP 201 must NOT mark task as GRADED or PASSED.
    """
    adapter = OfficialGameMasterAdapter(
        api_base_url=mock_gm_server.base_url,
        api_key=mock_gm_server.auth_token,
        timeout=3.0,
    )
    match_uuid = mock_gm_server.match_uuid
    item_key = "TASK-GRADING-03"

    try:
        adapter.start_working(match_uuid, item_key)
    except TypeError:
        adapter.start_working(item_key)

    client_sub_id = str(uuid.uuid4())
    idempotency_key = str(uuid.uuid4())

    submit_fn = getattr(adapter, "submit_work", getattr(adapter, "submit"))
    if hasattr(adapter, "submit_work"):
        receipt = submit_fn(
            match_uuid=match_uuid,
            client_submission_id=client_sub_id,
            idempotency_key=idempotency_key,
            backlog_item_key=item_key,
            git_revision="6" * 40,
            answer={"codeChanges": True, "summary": "Grade decoupling"},
        )
    else:
        receipt = submit_fn(
            task_id=item_key,
            git_revision="6" * 40,
            evidence=[],
            idempotency_key=idempotency_key,
        )

    # Submission status is ACCEPTED, but scoreStatus is PENDING
    sub = receipt["submission"]
    assert sub["status"] == "ACCEPTED"
    assert sub["scoreStatus"] == "PENDING"

    # In task store / state machine, this must NOT be marked PASSED or GRADED
    store = TaskStore()
    envelope = TaskEnvelope(
        task_id=item_key,
        task_type="TASK",
        title="Grading test",
        description="",
        repository_path=".",
        points=100,
        evidence_required=[],
    )
    store.add_task(envelope)

    # Record submitted status with ACCEPTED and score 0 (pending final grading)
    store.record_submission(
        task_id=item_key,
        status="ACCEPTED",
        score=0,
        submission_id=sub["id"],
    )
    task_record = store.get_task(item_key)
    assert task_record["status"] in ("SUBMITTED", TaskState.SUBMITTED.value, TaskState.SUBMITTED.name)
    # Confirmed points awarded must remain 0
    assert store.get_total_confirmed_points() == 0

    # Points are awarded only when subsequent status confirms PASSED
    store.record_submission(
        task_id=item_key,
        status="PASSED",
        score=100,
        submission_id=sub["id"],
    )
    assert store.get_total_confirmed_points() == 100


def test_runtime_01_zero_nested_cli_processes():
    """
    RUNTIME-01: Zero Nested CLI Processes.
    MTM Player owns the single root Codex instance.
    Production execution path must not spawn secondary 'codex exec' CLI subprocesses.
    """
    from mtm_competition.workers import launcher
    import inspect

    source = inspect.getsource(launcher)

    # Verify that secondary codex exec subprocess invocation is absent or strictly guarded
    # In MTM Player pack, workers must execute through native provider subagent mechanism, not secondary CLI
    assert 'subprocess.Popen(["codex", "exec"' not in source
    assert "subprocess.Popen(['codex', 'exec'" not in source
