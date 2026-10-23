"""E2E-A: Component Integration Tests (Scenarios A01 - A10).

Validates individual failure paths, guards, and boundaries without requiring live external CLI or server.
"""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.git import git_init, run_git, get_head_sha
from mtm_core.runtime import CodexRuntime, ExecutionTrace
from mtm_competition.tasks.store import TaskStore
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.verification.qa_gate import validate_qa_gate, QAGateError
from mtm_competition.integration.integration_guard import IntegrationGuard, IntegrationResult
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionGateError
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.adapters.official_game_master import GameMasterAPIError


@pytest.mark.e2e
def test_a01_worker_fake_test_pass_rejected_by_qa(tmp_path):
    """A01: Worker claims test passed, but independent QA execution detects failure."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b): return a - b\n", encoding="utf-8")

    envelope = {
        "task_id": "BUG-A01",
        "task_type": "BUG",
        "title": "Fix calc",
        "description": "Fix calc",
        "repository_path": str(ws),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
    }
    worker_res = {
        "task_id": "BUG-A01",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": f'{sys.executable} -c "import sys; sys.exit(1)"', "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }

    with pytest.raises(QAGateError, match="Independent test re-verification failed"):
        validate_qa_gate(envelope, worker_res, workspace_dir=ws, reverify_tests=True)


@pytest.mark.e2e
def test_a02_mandatory_mvs_failure_blocks_submission(tmp_path):
    """A02: Mandatory MVS fails -> submission blocked."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "calc.py").write_text("def add(a, b): return a - b\n", encoding="utf-8")

    envelope = {
        "task_id": "BUG-A02",
        "task_type": "BUG",
        "title": "Fix calc",
        "description": "Fix calc",
        "repository_path": str(ws),
        "base_revision": "HEAD",
        "points": 100,
        "selected_skills": ["mtm-bug-worker@0.1.0"],
        "time_budget_seconds": 60,
        "mvs": [{"id": "MVS-ADD", "description": "test_add_strictly"}],
    }
    worker_res = {
        "task_id": "BUG-A02",
        "status": "READY_FOR_REVIEW",
        "skill_ids": ["mtm-bug-worker@0.1.0"],
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest -k test_unrelated", "passed": True}],
        "evidence_paths": [],
        "elapsed_seconds": 10.0,
    }

    with pytest.raises(QAGateError, match="Mandatory MVS requirement 'MVS-ADD'"):
        validate_qa_gate(envelope, worker_res, workspace_dir=ws, reverify_tests=False)


@pytest.mark.e2e
def test_a03_git_push_failure_blocks_submission(tmp_path):
    """A03: Git push to remote fails -> no submission allowed."""
    remote_dir = tmp_path / "bare_remote.git"
    run_git(tmp_path, ["init", "--bare", str(remote_dir)])

    local_dir = tmp_path / "local_repo"
    git_init(local_dir)
    run_git(local_dir, ["remote", "add", "origin", str(remote_dir)])
    (local_dir / "f.txt").write_text("init\n")
    run_git(local_dir, ["add", "f.txt"])
    run_git(local_dir, ["commit", "-m", "init"])
    run_git(local_dir, ["push", "-u", "origin", "main"])

    ws = tmp_path / "worker_ws"
    ws.mkdir()
    (ws / "f.txt").write_text("v2\n")
    worker_res = WorkerResult(task_id="CR-A03", status=WorkerStatus.READY_FOR_REVIEW, changed_files=["f.txt"])

    guard = IntegrationGuard(target_repo_path=local_dir, push_to_remote=True, remote_name="origin")
    # Simulate remote rejection or network drop on push
    with patch("mtm_competition.integration.integration_guard.run_git") as mock_git:
        mock_git.side_effect = lambda path, args: MagicMock(returncode=1, stderr="fatal: remote rejected push") if "push" in args else run_git(path, args)
        res = guard.integrate(worker_res, ws)
        assert res.success is False
        assert res.pushed is False
        assert "push" in res.error_message


@pytest.mark.e2e
def test_a04_remote_sha_mismatch_blocks_submission(tmp_path):
    """A04: Remote returns differing SHA on verification -> submission blocked."""
    remote_dir = tmp_path / "bare_remote.git"
    run_git(tmp_path, ["init", "--bare", str(remote_dir)])

    local_dir = tmp_path / "local_repo"
    git_init(local_dir)
    run_git(local_dir, ["remote", "add", "origin", str(remote_dir)])
    (local_dir / "f.txt").write_text("v1\n")
    run_git(local_dir, ["add", "f.txt"])
    run_git(local_dir, ["commit", "-m", "init"])
    run_git(local_dir, ["push", "-u", "origin", "main"])

    ws = tmp_path / "worker_ws"
    ws.mkdir()
    (ws / "f.txt").write_text("v2\n")
    worker_res = WorkerResult(task_id="CR-A04", status=WorkerStatus.READY_FOR_REVIEW, changed_files=["f.txt"])

    guard = IntegrationGuard(target_repo_path=local_dir, push_to_remote=True, remote_name="origin")
    # Simulate git ls-remote returning a different SHA
    with patch.object(guard, "verify_remote_sha", return_value=False):
        res = guard.integrate(worker_res, ws)
        assert res.success is False
        assert res.pushed is False
        assert "Direct remote verification failed" in res.error_message


@pytest.mark.e2e
def test_a05_submission_timeout_reconciled_without_duplicate(tmp_path):
    """A05: POST /submit times out but server received it -> reconciled, no duplicate submit."""
    store = TaskStore(":memory:")
    guard = SubmissionGuard(store)
    repo = tmp_path / "repo"
    git_init(repo)
    rev = GitGuard(repo).verify_head()

    env = TaskEnvelope(task_id="BUG-A05", task_type="BUG", title="T", description="D", repository_path=str(repo), points=200)
    store.add_task(env)
    worker_res = WorkerResult(task_id="BUG-A05", status=WorkerStatus.READY_FOR_REVIEW, test_results=[{"command": "pytest", "passed": True}])

    submit_fn = MagicMock(side_effect=TimeoutError("Gateway Timeout"))
    status_fn = MagicMock(return_value={"status": "ACCEPTED", "points": 200, "task_id": "BUG-A05"})

    sub_res = guard.execute_guarded_submission(
        envelope=env,
        worker_result=worker_res,
        revision=rev,
        game_master_submit_fn=submit_fn,
        game_master_status_fn=status_fn,
    )
    assert sub_res.success is True
    assert store.has_submitted("BUG-A05") is True
    assert submit_fn.call_count == 1

    # Second submission attempt blocked by once-only policy
    with pytest.raises(SubmissionGateError, match="already been submitted"):
        guard.validate_pre_submission(env, worker_res, rev)


@pytest.mark.e2e
def test_a06_grader_failed_awards_zero_points():
    """A06: Grader returns FAILED -> confirmed points are 0."""
    store = TaskStore(":memory:")
    env = TaskEnvelope(task_id="BUG-A06", task_type="BUG", title="T", description="D", repository_path=".", points=150)
    store.add_task(env)

    store.record_submission("BUG-A06", status="FAILED", score=0)
    sub = store.get_submission("BUG-A06")
    assert sub["status"] == "FAILED"
    assert sub["confirmed_points"] == 0
    assert store.get_total_confirmed_points() == 0


@pytest.mark.e2e
def test_a07_restart_when_submit_in_flight_reconciles_safely(tmp_path):
    """A07: Restart with persisted DB while submission is in-flight -> safe reconciliation on startup."""
    db_file = tmp_path / "tournament.db"
    store = TaskStore(str(db_file))
    env = TaskEnvelope(task_id="BUG-A07", task_type="BUG", title="T", description="D", repository_path=str(tmp_path), points=300)
    store.add_task(env)
    store.start_submission("BUG-A07", "rev-sha-123")
    assert store.is_in_flight("BUG-A07") is True

    # GM confirms task actually passed
    gm = MagicMock()
    gm.get_submission_status.return_value = {"status": "PASSED", "points": 300, "task_id": "BUG-A07"}
    gm.get_match_state.return_value = {"status": "RUNNING", "remaining_seconds": 1500}
    gm.list_backlog.return_value = []

    orch = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=tmp_path,
        scratch_dir=tmp_path / "scratch",
        db_path=str(db_file),
    )
    sub = orch.store.get_submission("BUG-A07")
    assert sub["status"] == "PASSED"
    assert sub["confirmed_points"] == 300
    assert orch.store.is_in_flight("BUG-A07") is False


@pytest.mark.e2e
def test_a08_worker_cli_nonzero_exit_not_ready_for_review(tmp_path):
    """A08: Worker CLI exits with non-zero exit code -> cannot be READY_FOR_REVIEW."""
    from mtm_core.runtime import RuntimeResult, ExecutionTrace
    res = RuntimeResult(
        exit_code=1,
        timed_out=False,
        trace=ExecutionTrace(),
        usage={},
        commands=0,
        file_changes=0,
        last_message="SyntaxError in generation",
        elapsed=5.0,
    )
    assert res.success is False

    worker_res = WorkerResult(
        task_id="BUG-A08",
        status=WorkerStatus.FAILED,
        skill_ids=["mtm-bug-worker"],
        changed_files=[],
    )
    assert worker_res.status != "READY_FOR_REVIEW"


@pytest.mark.e2e
def test_a09_invalid_backend_fails_loudly(tmp_path):
    """A09: Requesting an invalid backend raises ValueError, never silent fallback."""
    gm = MagicMock()
    with pytest.raises(ValueError, match="Invalid backend 'unsupported_backend'"):
        CompetitionOrchestrator(
            gm_adapter=gm,
            repository_path=tmp_path,
            scratch_dir=tmp_path / "scratch",
            worker_backend="unsupported_backend",
        )


@pytest.mark.e2e
def test_a10_patch_modifying_files_outside_scope_rejected(tmp_path):
    """A10: Worker patch modifying files escaping repository bounds is rejected."""
    local_repo = tmp_path / "repo"
    git_init(local_repo)

    ws = tmp_path / "worker_ws"
    ws.mkdir()
    (ws / "outside.py").write_text("hacked\n")

    worker_res = WorkerResult(
        task_id="BUG-A10",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["../../outside.py"],
    )
    guard = IntegrationGuard(local_repo, push_to_remote=False)
    res = guard.integrate(worker_res, ws)
    assert res.success is False
    assert "Invalid path traversal" in res.error_message
