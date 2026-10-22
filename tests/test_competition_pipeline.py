"""Unit and Integration tests for Competition Orchestrator, Scheduler, and Guards."""
import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import ExecutionTrace
from mtm_core.git import git_init, get_head_sha
from mtm_competition.tasks.store import TaskStore
from mtm_competition.tasks.state_machine import TaskStateMachine, InvalidStateTransitionError
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.routing.skill_router import SkillRouter
from mtm_competition.routing.model_router import ModelRouter
from mtm_competition.routing.prompt_builder import PromptBuilder
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionGateError
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator


def test_task_store_idempotency_and_crud():
    store = TaskStore(":memory:")
    env = TaskEnvelope(
        task_id="BUG-101",
        task_type="BUG",
        title="Crash on null input",
        description="Fix NullPointerException",
        repository_path="/tmp/repo",
        points=200,
    )
    store.add_task(env)

    task = store.get_task("BUG-101")
    assert task is not None
    assert task["task_type"] == "BUG"
    assert task["status"] == TaskState.DISCOVERED.value

    # First submission succeeds
    ok1 = store.record_submission("BUG-101", status="ACCEPTED", score=200)
    assert ok1 is True
    assert store.has_submitted("BUG-101") is True

    # Duplicate submission is blocked by idempotency guard
    ok2 = store.record_submission("BUG-101", status="ACCEPTED", score=200)
    assert ok2 is False


def test_state_machine_transitions():
    assert TaskStateMachine.can_transition(TaskState.DISCOVERED, TaskState.QUEUED)
    assert TaskStateMachine.can_transition(TaskState.QUEUED, TaskState.ASSIGNED)
    assert TaskStateMachine.can_transition(TaskState.ASSIGNED, TaskState.RUNNING)
    assert TaskStateMachine.can_transition(TaskState.RUNNING, TaskState.VERIFYING)

    # Invalid jump
    assert not TaskStateMachine.can_transition(TaskState.DISCOVERED, TaskState.SUBMITTED)
    with pytest.raises(InvalidStateTransitionError):
        TaskStateMachine.validate_transition(TaskState.DISCOVERED, TaskState.SUBMITTED)


def test_scheduler_prioritizes_pi_over_minor_task():
    store = TaskStore(":memory:")
    scheduler = TournamentScheduler(store, concurrency_limit=1)

    t1 = TaskEnvelope(
        task_id="TASK-01",
        task_type="TASK",
        title="Update README",
        description="Fix typo",
        repository_path="/tmp/repo",
        points=100,
    )
    t2 = TaskEnvelope(
        task_id="PI-01",
        task_type="PI",
        title="Database deadlock",
        description="High urgency production incident",
        repository_path="/tmp/repo",
        points=250,
    )
    store.add_task(t1)
    store.add_task(t2)

    # Scheduler should pick the PI task first due to higher urgency & points
    selected = scheduler.select_next_task()
    assert selected is not None
    assert selected["task_id"] == "PI-01"


def test_skill_router_mapping():
    assert "mtm-bug-worker@1.0.0" in SkillRouter.route_skills({"task_type": "BUG"})
    assert "mtm-cr-worker@1.0.0" in SkillRouter.route_skills({"task_type": "CR"})
    assert "mtm-pi-worker@1.0.0" in SkillRouter.route_skills({"task_type": "PI"})

    # Dynamic support skill injection
    with_evidence = SkillRouter.route_skills({"task_type": "BUG", "evidence_required": ["proof.png"]})
    assert "mtm-evidence@1.0.0" in with_evidence


def test_prompt_builder():
    env = {
        "task_id": "CR-42",
        "task_type": "CR",
        "title": "Add refund API",
        "description": "Implement POST /refund",
        "evidence_required": ["api_trace.log"],
        "mvs": [{"id": "MVS-1", "description": "test_refund_success"}],
    }
    prompt = PromptBuilder.build_prompt(env)
    assert "CR-42" in prompt
    assert "MVS-1" in prompt
    assert "api_trace.log" in prompt
    assert "worker_result.json" in prompt


def test_submission_guard_blocks_duplicate(tmp_path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)

    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    git_guard = GitGuard(repo_dir)
    rev = git_guard.verify_head()

    env = TaskEnvelope(
        task_id="BUG-50",
        task_type="BUG",
        title="Fix division by zero",
        description="Handle denom=0",
        repository_path=str(repo_dir),
        points=200,
    )
    store.add_task(env)

    worker_res = WorkerResult(
        task_id="BUG-50",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": True}],
    )

    mock_gm_submit = MagicMock(return_value={"status": "ACCEPTED", "points": 200})

    # First submission succeeds
    sub_res = guard.execute_guarded_submission(env, worker_res, rev, mock_gm_submit)
    assert sub_res.success is True
    assert sub_res.points_awarded == 200

    # Second submission is strictly blocked by SubmissionGuard
    with pytest.raises(SubmissionGateError, match="already been submitted"):
        guard.execute_guarded_submission(env, worker_res, rev, mock_gm_submit)


def test_end_to_end_orchestration_cycle(tmp_path):
    """E2E flow: GM -> Orchestrator -> Scheduler -> Worker -> Guards -> Submission -> Score."""
    repo_dir = tmp_path / "submission_repo"
    git_init(repo_dir)
    scratch_dir = tmp_path / "scratch"

    # Initial code in repo
    (repo_dir / "calculator.py").write_text("def add(a, b): return a - b\n", encoding="utf-8")
    sha0 = get_head_sha(repo_dir)

    # Prepare Mock GM with one backlog item
    gm = MockGameMasterAdapter(tasks=[
        {
            "task_id": "BUG-E2E-01",
            "task_type": "BUG",
            "title": "Fix addition operator",
            "description": "Fix add() to return a + b",
            "points": 200,
            "evidence_required": [],
            "mvs": [{"id": "MVS-1", "description": "test_add"}],
        }
    ])

    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=repo_dir,
        scratch_dir=scratch_dir,
        db_path=":memory:",
    )

    # Mock the WorkerLauncher to simulate an autonomous worker making the fix
    def mock_launch(envelope, attempt_number=1):
        ws_path = orchestrator.launcher.workspace_mgr.prepare_workspace(
            repo_dir, envelope.task_id, attempt_number
        )
        # Worker fixes the bug
        (ws_path / "calculator.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
        (ws_path / "trace.jsonl").write_text('{"type": "item.completed"}\n', encoding="utf-8")
        result = WorkerResult(
            task_id=envelope.task_id,
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=["calculator.py"],
            test_results=[{"command": "pytest", "passed": True}],
            evidence_paths=["trace.jsonl"],
            elapsed_seconds=12.4,
        )
        return result, ExecutionTrace(), ws_path

    with patch.object(orchestrator.launcher, "launch", side_effect=mock_launch):
        # 1. Sync backlog from GM
        new_items = orchestrator.sync_backlog()
        assert new_items == 1

        # 2. Step orchestration
        step_result = orchestrator.step()
        assert step_result is not None
        assert step_result["status"] == "ACCEPTED"
        assert step_result["points"] == 200

        # 3. Verify fix was committed to submission repo
        fixed_content = (repo_dir / "calculator.py").read_text(encoding="utf-8")
        assert "return a + b" in fixed_content

        # 4. Verify GM match state total score
        gm_state = gm.get_match_state()
        assert gm_state["total_score"] == 200


def test_submission_guard_blocks_expired_deadline(tmp_path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    rev = GitGuard(repo_dir).verify_head()

    env = TaskEnvelope(
        task_id="BUG-EXPIRED",
        task_type="BUG",
        title="Late task",
        description="Deadline passed",
        repository_path=str(repo_dir),
        deadline_epoch=time.time() - 100,  # Expired in past
    )
    store.add_task(env)
    worker_res = WorkerResult(task_id="BUG-EXPIRED", status=WorkerStatus.READY_FOR_REVIEW)

    with pytest.raises(SubmissionGateError, match="deadline expired"):
        guard.execute_guarded_submission(env, worker_res, rev, MagicMock())


def test_submission_guard_blocks_missing_evidence(tmp_path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    rev = GitGuard(repo_dir).verify_head()

    env = TaskEnvelope(
        task_id="CR-EVIDENCE",
        task_type="CR",
        title="Requires screenshot",
        description="Needs proof",
        repository_path=str(repo_dir),
        evidence_required=["screen.png"],
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="CR-EVIDENCE",
        status=WorkerStatus.READY_FOR_REVIEW,
        evidence_paths=[],  # Missing screen.png
    )

    with pytest.raises(SubmissionGateError, match="missing required evidence"):
        guard.execute_guarded_submission(env, worker_res, rev, MagicMock())


def test_submission_guard_blocks_failed_test(tmp_path):
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    rev = GitGuard(repo_dir).verify_head()

    env = TaskEnvelope(
        task_id="BUG-FAILED-TEST",
        task_type="BUG",
        title="Unverified fix",
        description="Tests failed",
        repository_path=str(repo_dir),
    )
    store.add_task(env)
    worker_res = WorkerResult(
        task_id="BUG-FAILED-TEST",
        status=WorkerStatus.READY_FOR_REVIEW,
        test_results=[{"command": "pytest", "passed": False}],
    )

    with pytest.raises(SubmissionGateError, match="test failed"):
        guard.execute_guarded_submission(env, worker_res, rev, MagicMock())


def test_orchestrator_persistence_and_recovery(tmp_path):
    """Verify that restarting Orchestrator from persistent DB recovers match state and prevents duplicate work."""
    db_file = tmp_path / "tournament.db"
    repo_dir = tmp_path / "repo"
    git_init(repo_dir)
    scratch_dir = tmp_path / "scratch"

    # Run 1: Add task and submit it
    store1 = TaskStore(db_file)
    env = TaskEnvelope(
        task_id="BUG-RECOVERY-01",
        task_type="BUG",
        title="Persist test",
        description="Test state recovery",
        repository_path=str(repo_dir),
        points=150,
    )
    store1.add_task(env)
    store1.record_submission("BUG-RECOVERY-01", status="ACCEPTED", score=150)

    # Simulate process crash and restart: Run 2 opens the same DB file
    store2 = TaskStore(db_file)
    recovered_task = store2.get_task("BUG-RECOVERY-01")
    assert recovered_task is not None
    assert store2.has_submitted("BUG-RECOVERY-01") is True

    # Scheduler in new instance will not re-select already submitted task
    scheduler2 = TournamentScheduler(store2)
    next_task = scheduler2.select_next_task()
    assert next_task is None  # Nothing queued/available to submit


def test_test_policy_and_evidence_validator(tmp_path):
    from mtm_competition.verification import (
        VerificationLevel,
        TestPolicyManager,
        EvidenceValidator,
    )
    # PI task selects TARGETED
    p_pi = TestPolicyManager.select_policy("PI", time_budget_seconds=300, remaining_match_seconds=1200)
    assert p_pi == VerificationLevel.TARGETED

    # Ample time CR task selects IMPACT_REGRESSION
    p_cr = TestPolicyManager.select_policy("CR", time_budget_seconds=400, remaining_match_seconds=1000)
    assert p_cr == VerificationLevel.IMPACT_REGRESSION

    # Validate evidence files
    (tmp_path / "valid.log").write_text("All tests green\n")
    (tmp_path / "empty.log").write_text("")

    ok1, missing1 = EvidenceValidator.validate(tmp_path, ["valid.log"], ["valid.log"])
    assert ok1 is True
    assert len(missing1) == 0

    ok2, missing2 = EvidenceValidator.validate(tmp_path, ["empty.log"], ["empty.log"])
    assert ok2 is False
    assert any("empty file" in m for m in missing2)


def test_worker_supervisor(tmp_path):
    from mtm_competition.workers import WorkerSupervisor
    sup = WorkerSupervisor()
    assert sup.active_worker_count() == 0

    proc = sup.register_worker("TASK-1", attempt_number=1, timeout_seconds=0.01, workspace_path=tmp_path)
    assert sup.active_worker_count() == 1
    assert proc.task_id == "TASK-1"

    time.sleep(0.02)
    assert sup.is_timed_out("TASK-1") is True

    unreg = sup.unregister_worker("TASK-1")
    assert unreg is not None
    assert sup.active_worker_count() == 0
