"""End-to-End Tournament Simulation (E2E-B).

Implements and verifies the complete 12-event lifecycle defined in Upgrade2.md Phase 4:
1. Game Master supplies customer repository and Wave 1.
2. Orchestrator accepts tasks (TASK/CR/BUG).
3. Start Working called, unlocking MVS.
4. Worker starts working on CR.
5. Wave 2 appears with an emergency PI.
6. Supervisor preempts CR, scheduler prioritizes PI.
7. Match transitions to PAUSED then RESUMED without budget loss.
8. PI is fixed, tested, committed, pushed to remote, submitted.
9. Grader returns ACCEPTED first, PASSED later (two-phase grading).
10. Remaining tasks resume.
11. Network glitch and crash/restart simulated with in-flight recovery.
12. Game Master returns FINISHED; system generates final tournament report.
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


@pytest.mark.e2e
def test_e2e_b_complete_12_event_tournament_simulation(tmp_path):
    # =========================================================================
    # Event 1: Game Master supplies customer repository and Wave 1
    # =========================================================================
    remote_repo = tmp_path / "remote.git"
    run_git(tmp_path, ["init", "--bare", str(remote_repo)])

    customer_repo = tmp_path / "customer_repo"
    git_init(customer_repo)
    run_git(customer_repo, ["remote", "add", "origin", str(remote_repo)])

    # Setup base codebase
    calc_py = customer_repo / "calc.py"
    calc_py.write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def power(base, exp):\n    return 0  # To be implemented by CR\n\n"
        "def divide(a, b):\n    return a / b  # Bug: no zero check\n\n"
        "def critical_service():\n    return 'unstable'  # To be fixed by PI\n"
    )
    test_calc_py = customer_repo / "test_calc.py"
    test_calc_py.write_text(
        "import calc\n"
        "def test_power():\n    assert calc.power(2, 3) == 8\n"
        "def test_divide():\n    assert calc.divide(10, 2) == 5\n"
        "def test_critical_service():\n    assert calc.critical_service() == 'stable'\n"
    )
    run_git(customer_repo, ["add", "-A"])
    run_git(customer_repo, ["commit", "-m", "Initial commit from customer"])
    run_git(customer_repo, ["push", "-u", "origin", "main"])
    init_sha = get_head_sha(customer_repo)
    assert init_sha is not None

    scratch_dir = tmp_path / "agent_scratch"
    db_path = scratch_dir / "tournament.db"

    wave_1 = [
        {
            "task_id": "CR-POW-01",
            "task_type": "CR",
            "title": "Implement power function",
            "description": "calc.power(base, exp) should return base ** exp",
            "points": 300,
            "time_budget_seconds": 200,
            "evidence_required": ["evidence_pow.log"],
            "mvs": [{"id": "MVS-POW", "description": "test_power"}],
        },
        {
            "task_id": "BUG-DIV-01",
            "task_type": "BUG",
            "title": "Fix division helper",
            "description": "Ensure divide function handles inputs",
            "points": 100,
            "time_budget_seconds": 200,
            "evidence_required": ["evidence_div.log"],
            "mvs": [{"id": "MVS-DIV", "description": "test_divide"}],
        },
    ]

    gm = MockGameMasterAdapter(tasks=wave_1, duration_seconds=2400.0, two_phase_grading=True)

    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=customer_repo,
        scratch_dir=scratch_dir,
        db_path=db_path,
        concurrency_limit=1,
        push_to_remote=True,
        require_remote=True,
        token_budget=500_000,
    )

    # =========================================================================
    # Event 2: Orchestrator accepts tasks (TASK/CR/BUG)
    # =========================================================================
    discovered = orchestrator.sync_backlog()
    assert discovered == 2
    assert orchestrator.store.get_task("CR-POW-01") is not None
    assert orchestrator.store.get_task("BUG-DIV-01") is not None
    assert orchestrator.store.get_task("CR-POW-01")["status"] == "DISCOVERED"

    # =========================================================================
    # Event 3 & 4: Start Working called, unlocking MVS; Worker starts CR
    # =========================================================================
    selected_cr = orchestrator.scheduler.select_next_task()
    assert selected_cr is not None
    assert selected_cr["task_id"] == "CR-POW-01"

    orchestrator.gm.start_working("CR-POW-01")
    assert "CR-POW-01" in gm.started_tasks
    mvs_pow = gm.get_mvs("CR-POW-01")
    assert mvs_pow is not None
    assert mvs_pow[0]["id"] == "MVS-POW"

    # Worker starts running CR in supervisor
    orchestrator.scheduler.assign_task("CR-POW-01")
    dummy_proc = orchestrator.supervisor.launch_async(
        lambda env, att=1: (WorkerResult(task_id="CR-POW-01", status=WorkerStatus.RUNNING), ExecutionTrace(), tmp_path),
        TaskEnvelope(
            task_id="CR-POW-01",
            task_type="CR",
            title="Implement power function",
            description="",
            repository_path=str(customer_repo),
        ),
    )
    assert dummy_proc.task_id == "CR-POW-01"
    assert dummy_proc.status in ("RUNNING", "COMPLETED")
    assert "CR-POW-01" in orchestrator.supervisor.get_active_task_ids() or orchestrator.supervisor.is_finished("CR-POW-01")

    # =========================================================================
    # Event 5 & 6: Wave 2 brings emergency PI -> Supervisor preempts CR, Scheduler prioritizes PI
    # =========================================================================
    wave_2_pi = [
        {
            "task_id": "PI-CRITICAL-99",
            "task_type": "PI",
            "title": "Critical production service outage",
            "description": "Fix critical_service() to return 'stable'",
            "points": 500,
            "time_budget_seconds": 120,
            "evidence_required": ["evidence_pi.log"],
            "mvs": [{"id": "MVS-PI", "description": "test_critical_service"}],
        }
    ]
    gm.add_wave(wave_2_pi)
    new_items = orchestrator.sync_backlog()
    assert new_items == 1

    # CR-POW-01 was preempted and requeued, PI-CRITICAL-99 is selected next
    assert orchestrator.store.get_task("CR-POW-01")["status"] == "QUEUED"
    next_task = orchestrator.scheduler.select_next_task()
    assert next_task is not None
    assert next_task["task_id"] == "PI-CRITICAL-99"

    # =========================================================================
    # Event 7: Match transitions to PAUSED then RESUMED without budget loss
    # =========================================================================
    gm.pause_match()
    state_paused = gm.get_match_state()
    assert state_paused["status"] == "PAUSED"
    rem_before_pause = state_paused["remaining_seconds"]

    paused_step_result = orchestrator.step()
    assert paused_step_result == {"status": "PAUSED"}

    # Resume match
    gm.resume_match()
    state_resumed = gm.get_match_state()
    assert state_resumed["status"] == "RUNNING"
    # Budget remaining seconds is preserved (within small execution delta)
    assert abs(state_resumed["remaining_seconds"] - rem_before_pause) < 2.0

    # =========================================================================
    # Event 8 & 9: PI fixed, tested, committed, pushed, submitted; two-phase grading
    # =========================================================================
    def mock_worker_step(envelope, attempt_number=1):
        ws_path = orchestrator.launcher.workspace_mgr.prepare_workspace(
            customer_repo, envelope.task_id, attempt_number
        )
        tid = envelope.task_id
        if tid == "PI-CRITICAL-99":
            (ws_path / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n\n"
                "def power(base, exp):\n    return 0\n\n"
                "def divide(a, b):\n    return a / b\n\n"
                "def critical_service():\n    return 'stable'\n"
            )
            (ws_path / "evidence_pi.log").write_text("PI hotfix verified with MVS-PI\n")
            return (
                WorkerResult(
                    task_id=tid,
                    status=WorkerStatus.READY_FOR_REVIEW,
                    changed_files=["calc.py"],
                    test_results=[{"command": "pytest", "passed": True}],
                    evidence_paths=["evidence_pi.log"],
                    elapsed_seconds=12.0,
                ),
                ExecutionTrace(input_tokens=15000, output_tokens=3000),
                ws_path,
            )
        elif tid == "CR-POW-01":
            (ws_path / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n\n"
                "def power(base, exp):\n    return base ** exp\n\n"
                "def divide(a, b):\n    return a / b\n\n"
                "def critical_service():\n    return 'stable'\n"
            )
            (ws_path / "evidence_pow.log").write_text("CR power verified with MVS-POW\n")
            return (
                WorkerResult(
                    task_id=tid,
                    status=WorkerStatus.READY_FOR_REVIEW,
                    changed_files=["calc.py"],
                    test_results=[{"command": "pytest", "passed": True}],
                    evidence_paths=["evidence_pow.log"],
                    elapsed_seconds=18.0,
                ),
                ExecutionTrace(input_tokens=12000, output_tokens=4000),
                ws_path,
            )
        elif tid == "BUG-DIV-01":
            (ws_path / "calc.py").write_text(
                "def add(a, b):\n    return a + b\n\n"
                "def power(base, exp):\n    return base ** exp\n\n"
                "def divide(a, b):\n    if b == 0: raise ValueError('zero divide')\n    return a / b\n\n"
                "def critical_service():\n    return 'stable'\n"
            )
            (ws_path / "evidence_div.log").write_text("BUG divide verified with MVS-DIV\n")
            return (
                WorkerResult(
                    task_id=tid,
                    status=WorkerStatus.READY_FOR_REVIEW,
                    changed_files=["calc.py"],
                    test_results=[{"command": "pytest", "passed": True}],
                    evidence_paths=["evidence_div.log"],
                    elapsed_seconds=10.0,
                ),
                ExecutionTrace(input_tokens=9000, output_tokens=2000),
                ws_path,
            )
        raise ValueError(f"Unknown task {tid}")

    with patch.object(orchestrator.launcher, "launch", side_effect=mock_worker_step):
        # Step PI: execution, integration, push, submission
        pi_res = orchestrator.step()
        assert pi_res is not None
        assert pi_res["task_id"] == "PI-CRITICAL-99"
        assert pi_res["pushed"] is True

        # In two_phase_grading mode, step() confirmed grading status is PASSED and 500 points
        assert pi_res["status"] == "PASSED"
        assert pi_res["points"] == 500

        # Remote ref has the PI commit
        head_sha = get_head_sha(customer_repo)
        ls_rem = run_git(customer_repo, ["ls-remote", "origin", "refs/heads/main"])
        assert head_sha in ls_rem.stdout

        # Verify points separation in TaskStore
        pi_sub = orchestrator.store.get_submission("PI-CRITICAL-99")
        assert pi_sub["provisional_points"] == 500
        assert pi_sub["confirmed_points"] == 500
        assert pi_sub["status"] == "PASSED"

        # =====================================================================
        # Event 10: Remaining tasks resume (CR-POW-01 and BUG-DIV-01)
        # =====================================================================
        cr_res = orchestrator.step()
        assert cr_res is not None
        assert cr_res["task_id"] == "CR-POW-01"
        assert cr_res["status"] == "PASSED"
        assert cr_res["points"] == 300
        assert cr_res["pushed"] is True

        bug_res = orchestrator.step()
        assert bug_res is not None
        assert bug_res["task_id"] == "BUG-DIV-01"
        assert bug_res["status"] == "PASSED"
        assert bug_res["points"] == 100
        assert bug_res["pushed"] is True

    # Total score confirmed: 500 (PI) + 300 (CR) + 100 (BUG) = 900
    assert orchestrator.store.get_total_confirmed_points() == 900

    # =========================================================================
    # Event 11: Network glitch & crash/restart with in-flight recovery
    # =========================================================================
    # Simulate a new task arriving and entering submission in-flight state before crash
    glitch_task = {
        "task_id": "BUG-GLITCH-99",
        "task_type": "BUG",
        "title": "Minor edge case bug",
        "description": "Glitch test",
        "points": 50,
        "time_budget_seconds": 120,
    }
    gm.add_wave([glitch_task])
    orchestrator.sync_backlog()

    # Simulate submission started but interrupted by crash/timeout
    orchestrator.store.start_submission(
        task_id="BUG-GLITCH-99",
        git_revision=head_sha,
    )
    assert orchestrator.store.is_in_flight("BUG-GLITCH-99") is True

    # GM registered this submission before network was severed
    gm.submissions["BUG-GLITCH-99"] = {
        "status": "ACCEPTED",
        "task_id": "BUG-GLITCH-99",
        "points": 50,
        "git_revision": head_sha,
    }

    # Simulate crash & restart: New orchestrator instance connects to existing DB
    restarted_orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=customer_repo,
        scratch_dir=scratch_dir,
        db_path=db_path,
        push_to_remote=True,
    )
    # In-flight submission safely reconciled on startup
    assert restarted_orchestrator.store.is_in_flight("BUG-GLITCH-99") is False
    assert restarted_orchestrator.store.has_submitted("BUG-GLITCH-99") is True
    reconciled_sub = restarted_orchestrator.store.get_submission("BUG-GLITCH-99")
    assert reconciled_sub["status"] in ("ACCEPTED", "PASSED")

    # Verify duplicate submission is strictly prevented
    with pytest.raises(Exception, match="already been submitted"):
        restarted_orchestrator.submission_guard.execute_guarded_submission(
            envelope=TaskEnvelope(
                task_id="BUG-GLITCH-99",
                task_type="BUG",
                title="",
                description="",
                repository_path=str(customer_repo),
            ),
            worker_result=WorkerResult(task_id="BUG-GLITCH-99", status=WorkerStatus.READY_FOR_REVIEW),
            revision=restarted_orchestrator.git_guard.verify_head(),
            game_master_submit_fn=gm.submit,
        )

    # =========================================================================
    # Event 12: Game Master returns FINISHED -> final tournament report
    # =========================================================================
    gm.finish_match()
    assert gm.get_match_state()["status"] == "FINISHED"

    report = restarted_orchestrator.generate_match_report()
    assert report["match_status"] == "FINISHED"
    assert report["total_score"] >= 800
    assert report["tokens_consumed"] > 0
    assert report["tokens_consumed"] <= report["token_budget"]
    final_sha = get_head_sha(customer_repo)
    assert report["git_head_sha"] == final_sha
    assert len(report["tasks"]) == 4  # PI, CR, BUG, GLITCH
    # Zero submission errors in normal tasks
    assert len(report["errors"]) == 0
