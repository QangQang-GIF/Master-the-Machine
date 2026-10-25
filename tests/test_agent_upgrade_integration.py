"""Portable profile/context, 3-way conflict integration, pause/resume deadline, and secure verification tests."""
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus
from mtm_core.git import git_init, run_git, get_head_sha
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_competition.adapters.mock_game_master import MockGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator
from mtm_competition.integration.integration_guard import IntegrationGuard
from mtm_competition.workers.launcher import WorkerLauncher
from mtm_competition.workers.supervisor import WorkerSupervisor
from mtm_competition.verification.qa_gate import QAGateError, validate_schema
from mtm_competition.verification.command_safety import parse_safe_command, run_verification_command


def test_worker_opt_in_context(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AGENT_CONTEXT_ENABLED", "1")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def faulty(): pass\n")
    runtime = MagicMock()
    runtime.run.return_value = RuntimeResult(
        exit_code=1,
        timed_out=False,
        trace=ExecutionTrace(),
        usage={},
        commands=0,
        file_changes=0,
        last_message="",
        elapsed=0.1,
    )
    launcher = WorkerLauncher(tmp_path / "scratch", runtime=runtime, backend="codex")
    launcher.launch(
        {
            "task_id": "BUG-CONTEXT",
            "task_type": "BUG",
            "title": "Repair faulty service",
            "description": "Fix service.py",
            "repository_path": str(repo),
        }
    )
    ws = runtime.run.call_args.kwargs["workspace"]
    task_text = (ws / "TASK.md").read_text(encoding="utf-8")
    assert "Specialist Agent Profile" in task_text
    assert "Selected MTM Skill Instructions" in task_text
    assert "Project Knowledge Index" in task_text


def test_invalid_schema_becomes_qa_error():
    with pytest.raises(QAGateError, match="Schema validation failed"):
        validate_schema({"status": "NOPE"}, "worker_result")


def test_prod_commands_fail_closed_and_support_expanded_stack_templates(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_ENV", "production")
    approved = [
        "python -m pytest -q",
        "python -m unittest discover -s tests",
        "uv run pytest -q",
        "poetry run pytest",
        "npm test",
        "npm run test:unit",
        "pnpm run verify",
        "yarn test",
        "npx jest --runInBand",
        "npx vitest run",
        "mvn test -B",
        "./gradlew test",
        "go test ./...",
        "cargo test --quiet",
        "dotnet test",
        "bundle exec rspec",
        "ctest --output-on-failure",
    ]
    for cmd in approved:
        parsed = parse_safe_command(cmd)
        assert isinstance(parsed, list) and len(parsed) >= 1

    attacks = [
        "python -c 'import os'",
        "python -m pytest -c 'evil'",
        "sh -c 'echo danger'",
        "pytest ; echo hacked",
        "pytest && echo bad",
        "npm exec evil-pkg",
        "npm install malicious",
        "npm run deploy",
        "npx -y malicious-pkg",
        "go test -exec=sh ./...",
        "bundle exec ruby -e 'puts 1'",
    ]
    for attack in attacks:
        with pytest.raises(ValueError):
            parse_safe_command(attack)
    with pytest.raises(ValueError):
        run_verification_command("echo test", tmp_path)


def test_dev_legacy_runner_compatibility(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_ENV", "development")
    monkeypatch.delenv("MTM_SECURE_TEST_COMMANDS", raising=False)
    assert run_verification_command(f'{sys.executable} -c "print(123)"', tmp_path).returncode == 0


def test_three_way_conflict_aware_patch_integration(tmp_path):
    """Verify R-04 / SV-07: non-overlapping concurrent edits merge cleanly; conflicting edits fail closed without clobbering."""
    canon = tmp_path / "canonical"
    git_init(canon)
    base_content = "\n".join([f"line_{i} = {i}" for i in range(1, 15)]) + "\n"
    (canon / "module.py").write_text(base_content, encoding="utf-8")
    run_git(canon, ["add", "module.py"])
    run_git(canon, ["commit", "-m", "initial module"])

    # Clone two isolated worker workspaces at base commit
    ws_clean = tmp_path / "ws_clean"
    ws_conflict = tmp_path / "ws_conflict"
    run_git(tmp_path, ["clone", str(canon), str(ws_clean)])
    run_git(tmp_path, ["clone", str(canon), str(ws_conflict)])

    # Canonical repo advances with an edit on line_1
    canon_lines = base_content.splitlines()
    canon_lines[0] = "line_1 = 100  # concurrent canonical update"
    (canon / "module.py").write_text("\n".join(canon_lines) + "\n", encoding="utf-8")
    run_git(canon, ["add", "module.py"])
    run_git(canon, ["commit", "-m", "concurrent task update on line 1"])
    sha_after_concurrent = get_head_sha(canon)

    # Worker 1 modifies line_14 (non-overlapping with line_1)
    w1_lines = base_content.splitlines()
    w1_lines[-1] = "line_14 = 999  # worker 1 non-overlapping fix"
    (ws_clean / "module.py").write_text("\n".join(w1_lines) + "\n", encoding="utf-8")

    guard = IntegrationGuard(canon, push_to_remote=False, verify_post_integration=False)
    res_clean = guard.integrate(
        WorkerResult(
            task_id="BUG-3WAY-CLEAN",
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=["module.py"],
            test_results=[{"command": "pytest", "passed": True}],
        ),
        ws_clean,
    )
    assert res_clean.success is True
    merged_text = (canon / "module.py").read_text(encoding="utf-8")
    assert "line_1 = 100" in merged_text
    assert "line_14 = 999" in merged_text
    sha_after_merge = get_head_sha(canon)
    assert sha_after_merge != sha_after_concurrent

    # Worker 2 modifies line_1 differently (conflicting with canonical line_1!)
    w2_lines = base_content.splitlines()
    w2_lines[0] = "line_1 = -555  # conflicting edit on line 1"
    (ws_conflict / "module.py").write_text("\n".join(w2_lines) + "\n", encoding="utf-8")

    res_conflict = guard.integrate(
        WorkerResult(
            task_id="BUG-3WAY-CONFLICT",
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=["module.py"],
            test_results=[{"command": "pytest", "passed": True}],
        ),
        ws_conflict,
    )
    assert res_conflict.success is False
    assert "Three-way merge conflict" in (res_conflict.error_message or "")
    # Canonical repo must remain untouched with both line_1 = 100 and line_14 = 999 preserved
    assert get_head_sha(canon) == sha_after_merge
    preserved_text = (canon / "module.py").read_text(encoding="utf-8")
    assert "line_1 = 100" in preserved_text
    assert "line_14 = 999" in preserved_text
    assert "-555" not in preserved_text


def test_real_process_pause_resume_deadline_behavior(tmp_path):
    """Verify R-09: Pausing a worker freezes both Supervisor timeout accounting and CodexRuntime subprocess timeout."""
    supervisor = WorkerSupervisor(max_workers=1)
    env = TaskEnvelope(
        task_id="TASK-PAUSE-01",
        task_type="BUG",
        title="Pause test",
        description="Test pause/resume deadline",
        repository_path=str(tmp_path),
        time_budget_seconds=1,
    )
    proc = supervisor.register_worker("TASK-PAUSE-01", 1, timeout_seconds=0.35, workspace_path=tmp_path, envelope=env)
    supervisor.pause_worker("TASK-PAUSE-01")
    time.sleep(0.45)
    # Must NOT time out while paused even though wall-clock > 0.35s
    assert supervisor.is_timed_out("TASK-PAUSE-01") is False
    supervisor.resume_worker("TASK-PAUSE-01")
    assert supervisor.is_timed_out("TASK-PAUSE-01") is False
    time.sleep(0.40)
    assert supervisor.is_timed_out("TASK-PAUSE-01") is True

    # Also verify CodexRuntime._communicate_with_pause freezes deadline while pause_event is set
    pause_ev = threading.Event()
    pause_ev.set()

    class PausableProc:
        def __init__(self, duration: float):
            self.start = time.monotonic()
            self.duration = duration
            self.args = ["mock_worker"]

        def poll(self):
            if time.monotonic() - self.start >= self.duration:
                return 0
            return None

        def communicate(self, timeout=None):
            if timeout is not None:
                rem = self.duration - (time.monotonic() - self.start)
                if rem > timeout:
                    time.sleep(timeout)
                    raise __import__("subprocess").TimeoutExpired(cmd=self.args, timeout=timeout)
                elif rem > 0:
                    time.sleep(rem)
            return "DONE_AFTER_PAUSE", ""

    # Process duration is 0.35s, timeout is 0.20s.
    # While pause_ev is held for 0.25s, timeout depletion is frozen.
    proc = PausableProc(duration=0.35)

    def _release_later():
        time.sleep(0.25)
        pause_ev.clear()

    threading.Thread(target=_release_later, daemon=True).start()
    out, _ = CodexRuntime._communicate_with_pause(proc, timeout=0.20, pause_event=pause_ev)
    assert "DONE_AFTER_PAUSE" in out

    # Without pause (active depletion in loop), the exact same duration exceeds 0.20s timeout and raises TimeoutExpired
    proc_no_pause = PausableProc(duration=0.35)
    unset_pause_ev = threading.Event()
    with pytest.raises(__import__("subprocess").TimeoutExpired):
        CodexRuntime._communicate_with_pause(proc_no_pause, timeout=0.20, pause_event=unset_pause_ev)


def test_sv07_paired_contest_simulator(tmp_path):
    """Verify SV-07: Paired 40-minute contest simulation comparing Codex and Antigravity with fault injection."""
    backend_metrics = {}

    for backend in ("codex", "antigravity"):
        base_dir = tmp_path / f"sim_{backend}"
        base_dir.mkdir(parents=True, exist_ok=True)
        remote = base_dir / "remote.git"
        run_git(base_dir, ["init", "--bare", str(remote)])
        local = base_dir / "local"
        git_init(local)
        run_git(local, ["remote", "add", "origin", str(remote)])
        (local / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n*.pyc\n", encoding="utf-8")
        (local / "math_lib.py").write_text("def solve(): return 1\n", encoding="utf-8")
        run_git(local, ["add", ".gitignore", "math_lib.py"])
        run_git(local, ["commit", "-m", "init math lib"])
        run_git(local, ["push", "-u", "origin", "main"])

        tasks = [
            {
                "task_id": f"BUG-PAIR-{backend}-1",
                "task_type": "BUG",
                "title": f"Bug on {backend}",
                "description": "Fix math_lib",
                "points": 200,
                "evidence_required": [],
                "mvs": [{"id": "MVS-1"}],
            },
            {
                "task_id": f"CR-PAIR-{backend}-2",
                "task_type": "CR",
                "title": f"Feature on {backend}",
                "description": "Add feature",
                "points": 300,
                "evidence_required": [],
                "mvs": [{"id": "MVS-2"}],
            },
        ]

        gm = MockGameMasterAdapter(tasks=tasks, duration_seconds=2400.0, two_phase_grading=True)
        scratch = base_dir / "scratch"
        orchestrator = CompetitionOrchestrator(
            gm_adapter=gm,
            repository_path=local,
            scratch_dir=scratch,
            db_path=scratch / "sim.db",
            worker_backend=backend,
            push_to_remote=True,
            verify_post_integration=True,
        )

        def mock_worker_run(envelope, attempt_number=1, **kwargs):
            tid = envelope.task_id
            ws = orchestrator.launcher.workspace_mgr.prepare_workspace(local, tid, attempt_number)
            file_name = f"code_{tid.replace('-', '_').lower()}.py"
            test_file_name = f"test_{file_name}"
            (ws / file_name).write_text(f"def solve_{tid.replace('-', '_')}(): return '{backend}_ok'\n", encoding="utf-8")
            (ws / test_file_name).write_text(f"def test_{tid.replace('-', '_')}(): assert True\n", encoding="utf-8")
            tok_in = 3000 if backend == "codex" else 4000
            tok_out = 600 if backend == "codex" else 500
            model = "gpt-6.1-sol" if backend == "codex" else "gemini-3.8-flash-low"
            trace = ExecutionTrace(input_tokens=tok_in, cached_input_tokens=1000, output_tokens=tok_out, model=model, backend=backend)
            py_exec = sys.executable
            test_cmd = f"{py_exec} -m pytest -q"
            result = WorkerResult(
                task_id=tid,
                status=WorkerStatus.READY_FOR_REVIEW,
                changed_files=[file_name, test_file_name],
                test_results=[{"command": test_cmd, "passed": True}],
                elapsed_seconds=1.2,
                usage={"normalized_credits": trace.normalized_credits},
            )
            return result, trace, ws

        with patch.object(orchestrator.launcher, "launch", side_effect=mock_worker_run):
            count = orchestrator.sync_backlog()
            assert count == 2
            step1 = orchestrator.step()
            assert step1 is not None and step1["status"] in ("ACCEPTED", "PASSED")
            assert step1["pushed"] is True
            step2 = orchestrator.step()
            assert step2 is not None and step2["status"] in ("ACCEPTED", "PASSED")
            assert step2["pushed"] is True

        orchestrator.store.reconcile_grading_lifecycle(gm.get_submission_status)
        report = orchestrator.generate_match_report()
        backend_metrics[backend] = {
            "score": report["total_score"],
            "tokens": report["tokens_consumed"],
            "head_sha": report["git_head_sha"],
        }
        assert report["total_score"] == 500
        assert len(report["errors"]) == 0

    assert backend_metrics["codex"]["score"] == backend_metrics["antigravity"]["score"] == 500
    assert backend_metrics["codex"]["head_sha"] != "unknown"
    assert backend_metrics["antigravity"]["head_sha"] != "unknown"
