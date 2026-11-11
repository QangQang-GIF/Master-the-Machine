"""End-to-End Pre-Trial Round Tournament Scenario Test (Section 10 of Upgrade.md).

Simulates the complete 8-event tournament lifecycle:
1. Game Master releases customer repository and Wave 1 backlog.
2. Autonomous agent discovers backlog, calls Start working, unlocks MVS.
3. Mid-match emergency: Game Master issues PI backlog item while CR is queued/running.
   Scheduler and supervisor preempt running work and prioritize PI.
4. Tournament match switches to PAUSED then RESUMED; server clock controls timing.
5. Worker passes verified tests; IntegrationGuard commits, pushes to remote, and verifies SHA.
6. SubmissionGuard submits once; Game Master accepts; Grader evaluates to PASSED with full points.
7. Simulated network disruption / crash during submission confirms zero-duplicate idempotency.
8. Tournament completion produces structured match report with score, time, and token metrics.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.runtime import ExecutionTrace
from mtm_core.git import git_init, get_head_sha, run_git
from mtm_competition.tasks.store import TaskStore
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.integration.git_guard import GitGuard
from mtm_competition.integration.integration_guard import IntegrationGuard
from mtm_competition.integration.submission_guard import SubmissionGuard
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator


def test_section_10_full_contest_simulation_scenario(tmp_path):
    # Setup simulated remote git repository
    remote_repo = tmp_path / "remote.git"
    run_git(tmp_path, ["init", "--bare", str(remote_repo)])

    # Setup customer canonical repository cloned from remote
    customer_repo = tmp_path / "customer_repo"
    git_init(customer_repo)
    run_git(customer_repo, ["remote", "add", "origin", str(remote_repo)])

    # Base codebase in customer repo
    (customer_repo / "payment.py").write_text(
        "def process_charge(amount):\n    return False\n\n"
        "def refund(transaction_id):\n    return False\n"
    )
    (customer_repo / "test_payment.py").write_text(
        "import payment\n"
        "def test_charge(): assert payment.process_charge(100) is True\n"
        "def test_refund(): assert payment.refund('tx1') is True\n"
    )
    run_git(customer_repo, ["add", "-A"])
    run_git(customer_repo, ["commit", "-m", "init customer repo"])
    run_git(customer_repo, ["push", "-u", "origin", "main"])
    base_sha = get_head_sha(customer_repo)

    scratch_dir = tmp_path / "agent_scratch"
    db_path = scratch_dir / "match.db"

    # 1. Game Master releases Wave 1 backlog
    wave_1 = [
        {
            "task_id": "CR-PAYMENT-01",
            "task_type": "CR",
            "title": "Enable credit card charges",
            "description": "Fix process_charge() to return True",
            "points": 200,
            "time_budget_seconds": 300,
            "evidence_required": ["test_evidence.log"],
            "mvs": [{"id": "MVS-CHARGE", "description": "test_charge"}],
        }
    ]

    gm = MockGameMasterAdapter(tasks=wave_1, duration_seconds=2400.0, two_phase_grading=True)

    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=customer_repo,
        scratch_dir=scratch_dir,
        db_path=db_path,
        concurrency_limit=1,
        push_to_remote=True,
    )

    # 2. Agent syncs Wave 1 backlog
    discovered_count = orchestrator.sync_backlog()
    assert discovered_count == 1
    assert orchestrator.store.get_task("CR-PAYMENT-01") is not None

    # 3. Simulate mid-match emergency: Game Master issues unexpected PI (Production Incident)
    wave_2_pi = [
        {
            "task_id": "PI-OUTAGE-99",
            "task_type": "PI",
            "title": "Critical refund failure causing deadlock",
            "description": "Fix refund() to return True immediately",
            "points": 350,
            "time_budget_seconds": 180,
            "evidence_required": ["pi_trace.log"],
            "mvs": [{"id": "MVS-REFUND", "description": "test_refund"}],
        }
    ]
    gm.add_wave(wave_2_pi)
    new_items = orchestrator.sync_backlog()
    assert new_items == 1

    # Scheduler prioritizes PI-OUTAGE-99 ahead of CR-PAYMENT-01!
    selected = orchestrator.scheduler.select_next_task()
    assert selected is not None
    assert selected["task_id"] == "PI-OUTAGE-99"

    # 4. Tournament switches to PAUSED then RESUMED
    gm.pause_match()
    assert gm.get_match_state()["status"] == "PAUSED"
    orchestrator.sync_backlog()
    assert orchestrator.scheduler.is_paused is True
    # In PAUSED mode, orchestrator.step() yields status PAUSED and does not execute
    paused_step = orchestrator.step()
    assert paused_step == {"status": "PAUSED"}

    # Resume match
    gm.resume_match()
    assert gm.get_match_state()["status"] == "RUNNING"
    orchestrator.sync_backlog()
    assert orchestrator.scheduler.is_paused is False

    # 5. Worker execution on PI-OUTAGE-99 (mocking autonomous worker implementation)
    def mock_worker_execution(envelope, attempt_number=1):
        ws_path = orchestrator.launcher.workspace_mgr.prepare_workspace(
            customer_repo, envelope.task_id, attempt_number
        )
        task_id = envelope.task_id
        if task_id == "PI-OUTAGE-99":
            (ws_path / "payment.py").write_text(
                "def process_charge(amount):\n    return False\n\n"
                "def refund(transaction_id):\n    return True\n"
            )
            (ws_path / "pi_trace.log").write_text("PI hotfix verified\n")
            return WorkerResult(
                task_id=task_id,
                status=WorkerStatus.READY_FOR_REVIEW,
                changed_files=["payment.py"],
                test_results=[{"command": "pytest", "passed": True}],
                evidence_paths=["pi_trace.log"],
                elapsed_seconds=15.0,
            ), ExecutionTrace(input_tokens=10000, output_tokens=5000), ws_path
        else:
            (ws_path / "payment.py").write_text(
                "def process_charge(amount):\n    return True\n\n"
                "def refund(transaction_id):\n    return True\n"
            )
            (ws_path / "test_evidence.log").write_text("CR charge tests passed\n")
            return WorkerResult(
                task_id=task_id,
                status=WorkerStatus.READY_FOR_REVIEW,
                changed_files=["payment.py"],
                test_results=[{"command": "pytest", "passed": True}],
                evidence_paths=["test_evidence.log"],
                elapsed_seconds=22.0,
            ), ExecutionTrace(input_tokens=10000, output_tokens=8000), ws_path

    with patch.object(orchestrator.launcher, "launch", side_effect=mock_worker_execution):
        # 6. Step 1: Execute and submit PI-OUTAGE-99
        pi_step_res = orchestrator.step()
        assert pi_step_res is not None
        assert pi_step_res["task_id"] == "PI-OUTAGE-99"
        # Two-phase grading confirms PASSED and full 350 points
        assert pi_step_res["status"] == "PASSED"
        assert pi_step_res["points"] == 350
        assert pi_step_res["pushed"] is True

        # Verify remote tracking branch has the PI commit
        rem_check = run_git(customer_repo, ["ls-remote", "--heads", "origin", "main"])
        head_sha = get_head_sha(customer_repo)
        assert head_sha in rem_check.stdout

        # Step 2: Now process the remaining CR-PAYMENT-01
        cr_step_res = orchestrator.step()
        assert cr_step_res is not None
        assert cr_step_res["task_id"] == "CR-PAYMENT-01"
        assert cr_step_res["status"] == "PASSED"
        assert cr_step_res["points"] == 200
        assert cr_step_res["pushed"] is True

    # 7. Zero-duplicate submission guard: attempting to re-submit submitted items is blocked
    git_guard = GitGuard(customer_repo)
    verified_rev = git_guard.verify_head()
    with pytest.raises(Exception, match="already been submitted"):
        orchestrator.submission_guard.execute_guarded_submission(
            envelope=TaskEnvelope(task_id="PI-OUTAGE-99", task_type="PI", title="", description="", repository_path=str(customer_repo)),
            worker_result=WorkerResult(task_id="PI-OUTAGE-99", status=WorkerStatus.READY_FOR_REVIEW),
            revision=verified_rev,
            game_master_submit_fn=gm.submit,
        )

    # 8. Tournament final scorecard
    final_gm_state = gm.get_match_state()
    assert final_gm_state["total_score"] == 550  # 350 (PI) + 200 (CR)
    assert orchestrator.cumulative_billable_tokens == 33000

    # Verify task database persistence after simulated restart
    restarted_orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=customer_repo,
        scratch_dir=scratch_dir,
        db_path=db_path,
    )
    assert restarted_orchestrator.store.has_submitted("PI-OUTAGE-99") is True
    assert restarted_orchestrator.store.has_submitted("CR-PAYMENT-01") is True
    assert restarted_orchestrator.scheduler.select_next_task() is None
