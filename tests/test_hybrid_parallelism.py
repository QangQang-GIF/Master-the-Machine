"""Unit and scenario tests for Hybrid Parallelism, PI Fast-track, and Option B Consolidated Skills."""
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.git import git_init, run_git, get_head_sha
from mtm_core.runtime import ExecutionTrace
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.routing.skill_router import SkillRouter
from mtm_competition.scheduling.preemption import PreemptionPolicy
from mtm_core.config.loader import verify_skill_checksums, verify_profile_checksums


def test_option_b_skills_route_and_checksum_integrity():
    """Verify that Option B consolidated skills route properly and checksums pass."""
    base = Path(__file__).resolve().parent.parent
    assert verify_skill_checksums(base) is True
    assert verify_profile_checksums(base) is True

    # Skill routing verifies mtm-worker is the primary skill
    for task_type in ("BUG", "CR", "TASK", "PI"):
        skills = SkillRouter.route_skills({"task_type": task_type})
        assert "mtm-init@1.0.0" in skills
        assert "mtm-worker@1.0.0" in skills
        assert "mtm-evidence@1.0.0" not in skills

    # Evidence skill is conditionally added when required
    skills_with_ev = SkillRouter.route_skills({"task_type": "BUG", "evidence_required": ["evidence.png"]})
    assert "mtm-evidence@1.0.0" in skills_with_ev


def test_preemption_policy_spares_running_task_when_capacity_available():
    """PreemptionPolicy should NOT preempt if spare capacity is available."""
    incoming_pi = {"task_id": "PI-01", "task_type": "PI", "points": 500}
    running_task = {"task_id": "CR-01", "task_type": "CR", "points": 300, "time_budget_seconds": 420}

    # With capacity available: NO preemption, both can run concurrently
    assert PreemptionPolicy.should_preempt(incoming_pi, running_task, running_elapsed_seconds=30.0, has_capacity=True) is False

    # When capacity is exhausted (has_capacity=False): PI preempts lower-urgency task
    assert PreemptionPolicy.should_preempt(incoming_pi, running_task, running_elapsed_seconds=30.0, has_capacity=False) is True


def test_hybrid_parallel_pi_fast_track(tmp_path):
    """Scenario test: Normal task is running; incoming PI task executes in parallel without delay.

    PI finishes first, submits immediately, and normal task completes afterwards.
    """
    base_dir = tmp_path / "parallel_sim"
    base_dir.mkdir()
    remote = base_dir / "remote.git"
    run_git(base_dir, ["init", "--bare", str(remote)])
    local = base_dir / "local"
    git_init(local)
    run_git(local, ["remote", "add", "origin", str(remote)])
    (local / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    (local / "app.py").write_text("def run(): return 'v1'\n", encoding="utf-8")
    run_git(local, ["add", ".gitignore", "app.py"])
    run_git(local, ["commit", "-m", "initial commit"])
    run_git(local, ["push", "-u", "origin", "main"])

    tasks = [
        {
            "task_id": "CR-01",
            "task_type": "CR",
            "title": "Add analytics feature",
            "description": "Implement analytics",
            "points": 300,
            "evidence_required": [],
            "mvs": [{"id": "MVS-CR"}],
            "time_budget_seconds": 420,
        },
        {
            "task_id": "PI-01",
            "task_type": "PI",
            "title": "Urgent outage fix",
            "description": "Fix memory leak",
            "points": 500,
            "evidence_required": [],
            "mvs": [{"id": "MVS-PI"}],
            "time_budget_seconds": 120,
        },
    ]

    gm = MockGameMasterAdapter(tasks=tasks, duration_seconds=2400.0, two_phase_grading=True)
    scratch = base_dir / "scratch"
    orchestrator = CompetitionOrchestrator(
        gm_adapter=gm,
        repository_path=local,
        scratch_dir=scratch,
        db_path=scratch / "sim.db",
        concurrency_limit=2,
        worker_backend="antigravity",
        push_to_remote=True,
        verify_post_integration=True,
    )

    # Worker execution mock: PI completes fast (0.1s), CR takes longer (0.5s)
    def mock_worker_run(envelope, attempt_number=1, **kwargs):
        tid = envelope.task_id
        ws = orchestrator.launcher.workspace_mgr.prepare_workspace(local, tid, attempt_number)
        file_name = f"mod_{tid.replace('-', '_').lower()}.py"
        test_file_name = f"test_{file_name}"
        (ws / file_name).write_text(f"# fix for {tid}\n", encoding="utf-8")
        (ws / test_file_name).write_text(f"def test_{tid.replace('-', '_')}(): assert True\n", encoding="utf-8")

        # PI is fast, CR takes a bit longer
        sleep_dur = 0.05 if envelope.task_type == "PI" else 0.25
        time.sleep(sleep_dur)

        trace = ExecutionTrace(input_tokens=1000, output_tokens=200, backend="antigravity")
        py_exec = sys.executable
        return (
            WorkerResult(
                task_id=tid,
                status=WorkerStatus.READY_FOR_REVIEW,
                changed_files=[file_name, test_file_name],
                test_results=[{"command": f"{py_exec} -m pytest -q", "passed": True}],
                elapsed_seconds=sleep_dur,
            ),
            trace,
            ws,
        )

    with patch.object(orchestrator.launcher, "launch", side_effect=mock_worker_run):
        orchestrator.sync_backlog()

        # Step 1: Normal task is scheduled and PI fast-tracks in parallel slot!
        # PI finishes first due to lower latency, so step 1 returns PI-01 result!
        res1 = orchestrator.step()
        assert res1 is not None
        assert res1["status"] in ("ACCEPTED", "PASSED")
        assert res1["pushed"] is True
        # PI-01 was fast-tracked and submitted first!
        assert res1["task_id"] == "PI-01"

        # Step 2: CR-01 was still running in parallel and now completes and submits!
        res2 = orchestrator.step()
        assert res2 is not None
        assert res2["status"] in ("ACCEPTED", "PASSED")
        assert res2["pushed"] is True
        assert res2["task_id"] == "CR-01"

    # Verify both tasks successfully submitted and confirmed
    report = orchestrator.generate_match_report()
    assert report["total_score"] == 800
    assert len(report["errors"]) == 0
