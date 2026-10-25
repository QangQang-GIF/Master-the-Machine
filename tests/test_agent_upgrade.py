"""Offline regression tests: portable profiles, native dispatcher, incremental knowledge, credits, and DEV learning."""
from pathlib import Path
import time
import tomllib
from unittest.mock import MagicMock

import pytest

from mtm_agents.profiles import resolve_profile, export_native_agents
from mtm_agents.context import compile_task_context
from mtm_agents.dispatcher import NativeSubagentDispatcher
from mtm_backends import probe_backend, select_available_backend
from mtm_core.config.loader import verify_profile_checksums, verify_skill_checksums
from mtm_core.runtime import CodexRuntime, ExecutionTrace, RuntimeResult, TokenUsage
from mtm_knowledge import build_index, retrieve_knowledge, clear_index_cache
from mtm_training.pipeline import (
    evaluate_paired_runs,
    propose_from_failures,
    generate_proposal_diff,
    save_paired_dataset,
    load_paired_dataset,
    run_ab_training_pipeline,
    rollback_skill_promotion,
)


def test_profiles_render_both_native_formats(tmp_path):
    profile = resolve_profile("BUG")
    assert profile.name == "bug-fixer"
    assert tomllib.loads(profile.codex_toml())["name"] == "bug-fixer"
    assert "name: bug-fixer" in profile.antigravity_markdown()
    generated = export_native_agents(tmp_path)
    assert len(generated) == 8
    assert (tmp_path / ".codex/agents/bug-fixer.toml").is_file()
    assert (tmp_path / ".agents/agents/incident-responder.md").is_file()


def test_native_subagent_dispatcher_injects_and_cleans_up_profiles(tmp_path):
    mock_rt = MagicMock(spec=CodexRuntime)

    def _inspect_workspace(**kwargs):
        ws = Path(kwargs["workspace"])
        assert (ws / ".codex/agents/bug-fixer.toml").is_file()
        assert (ws / ".agents/agents/bug-fixer.md").is_file()
        assert kwargs["agent"] == "bug-fixer"
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(input_tokens=200, output_tokens=50),
            usage={},
            commands=1,
            file_changes=1,
            last_message="OK",
            elapsed=0.2,
        )

    mock_rt.run.side_effect = _inspect_workspace
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    res = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Fix bug",
        workspace=tmp_path,
        backend="antigravity",
        model="gemini-3.8-flash-low",
        effort="low",
    )
    assert res.exit_code == 0
    assert not (tmp_path / ".codex").exists()
    assert not (tmp_path / ".agents").exists()


def test_knowledge_index_is_bounded_incremental_and_excludes_secrets(tmp_path):
    clear_index_cache()
    repo = tmp_path / "repo"
    repo.mkdir()
    cache_dir = tmp_path / "cache"
    (repo / "service.py").write_text("def calculate_total(x):\n    return x\n")
    (repo / "test_service.py").write_text("def test_total():\n    pass\n")
    (repo / ".env").write_text("PASSWORD=abc")
    (repo / "private_key.py").write_text("def bad():\n    pass\n")

    idx1 = build_index(repo, cache_dir=cache_dir)
    paths = [f["path"] for f in idx1["files"]]
    assert "service.py" in paths
    assert ".env" not in paths
    assert "private_key.py" not in paths
    assert idx1["cache_hit"] is False
    assert idx1["reindexed_files"] == 2

    # Second run with unchanged files hits incremental cache
    idx2 = build_index(repo, cache_dir=cache_dir)
    assert idx2["cache_hit"] is True
    assert idx2["reused_files"] == 2
    assert idx2["reindexed_files"] == 0

    # Modifying one file invalidates only that file while reusing unchanged files
    time.sleep(0.01)
    (repo / "service.py").write_text("def calculate_total(x):\n    return x\ndef apply_coupon(y):\n    return y\n")
    idx3 = build_index(repo, cache_dir=cache_dir)
    assert idx3["cache_hit"] is False
    assert idx3["reused_files"] == 1
    assert idx3["reindexed_files"] == 1

    summary = retrieve_knowledge(idx3, "apply_coupon calculate_total service", max_chars=500)
    assert "service.py" in summary
    assert "apply_coupon" in summary
    assert "PASSWORD" not in summary
    assert len(summary) <= 500


def test_compiled_context_contains_real_skills(tmp_path):
    (tmp_path / "engine.py").write_text("def broken(): pass\n")
    context = compile_task_context(
        {"task_type": "BUG", "title": "broken engine", "description": "Fix engine.py", "repository_path": str(tmp_path)},
        ["mtm-init@1.0.0", "mtm-bug-worker@1.0.0"],
    )
    assert "Specialist Agent Profile" in context
    assert "Project Knowledge Index" in context
    assert "Selected MTM Skill Instructions" in context
    assert "Minimal Surgical" in context
    with pytest.raises(ValueError):
        compile_task_context({"task_type": "BUG"}, ["../../secrets@1"])


def test_backend_capability_is_never_falsely_verified(monkeypatch):
    import mtm_backends.capabilities as module
    monkeypatch.setattr(module.shutil, "which", lambda cmd: "/bin/mock" if cmd == "agy" else None)
    assert probe_backend("antigravity").cli_available
    assert not probe_backend("antigravity").native_headless_verified
    assert select_available_backend("codex") == "antigravity"
    with pytest.raises(ValueError):
        probe_backend("invalid")


def test_normalized_credit_accounting_and_policy_sandbox():
    u_fast = TokenUsage.calculate(2000, 1000, 500, 100, model="gemini-3.8-flash-low", backend="antigravity")
    u_std = TokenUsage.calculate(2000, 1000, 500, 100, model="gpt-6.1-sol", backend="codex")
    u_deep = TokenUsage.calculate(2000, 1000, 500, 100, model="claude-sonnet-4-6", backend="antigravity")
    assert u_fast.normalized_credits < u_std.normalized_credits < u_deep.normalized_credits
    assert "normalized_credits" in u_std.to_dict()

    rt = CodexRuntime()
    cmd_ro = rt.build_agy_cmd("Audit code", sandbox_policy="read-only", agent="bug-fixer")
    assert "--sandbox" in cmd_ro
    assert "--mode" in cmd_ro and "plan" in cmd_ro
    assert "--dangerously-skip-permissions" not in cmd_ro
    assert "--agent" in cmd_ro and "bug-fixer" in cmd_ro

    cmd_sb = rt.build_agy_cmd("Fix code", sandbox_policy="sandbox")
    assert "--sandbox" in cmd_sb
    assert "accept-edits" in cmd_sb


def test_training_evaluation_requires_sufficient_pairs():
    base = {str(i): {"passed": True, "tokens": 100} for i in range(5)}
    improved = {str(i): {"passed": True, "tokens": 90} for i in range(5)}
    assert evaluate_paired_runs(base, improved).eligible is True
    improved["0"]["passed"] = False
    assert evaluate_paired_runs(base, improved).eligible is False
    assert evaluate_paired_runs(base, {"0": improved["0"]}).eligible is False


def test_training_candidate_ab_pipeline_promotion_and_rollback(tmp_path):
    skills_dir = tmp_path / "skills"
    skill = skills_dir / "mtm-bug-worker"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("# Original\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"skills": {"mtm-bug-worker": {"version": "1.0.0"}}}', encoding="utf-8")

    base = {f"T-{i}": {"passed": i < 4, "tokens": 1000} for i in range(5)}
    cand = {f"T-{i}": {"passed": True, "tokens": 950} for i in range(5)}
    events = [{"status": "TIMED_OUT", "changed_files": []} for _ in range(3)]

    ds_file = save_paired_dataset(tmp_path / "dataset.json", base, cand, events)
    loaded = load_paired_dataset(ds_file)
    assert len(loaded["baseline"]) == 5

    # 1. Dry-run pipeline does not mutate SKILL.md
    dry_report = run_ab_training_pipeline(
        baseline=loaded["baseline"],
        candidate=loaded["candidate"],
        failures=loaded["failures"],
        skill_name="mtm-bug-worker",
        current_version="1.0.0",
        skills_dir=skills_dir,
        registry_manifest=manifest,
        promote=False,
    )
    assert dry_report.evaluation.eligible is True
    assert dry_report.proposal is not None
    assert dry_report.promoted is False
    assert "Learned Procedural Guideline" in dry_report.proposed_diff
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "# Original\n"

    # 2. Promotion requires an explicit human reviewer
    with pytest.raises(ValueError, match="reviewer"):
        run_ab_training_pipeline(
            baseline=base,
            candidate=cand,
            failures=events,
            skill_name="mtm-bug-worker",
            skills_dir=skills_dir,
            registry_manifest=manifest,
            promote=True,
            reviewer=None,
        )

    # 3. Reviewed promotion updates SKILL.md and manifest, and rollback restores exact state
    prom_report = run_ab_training_pipeline(
        baseline=base,
        candidate=cand,
        failures=events,
        skill_name="mtm-bug-worker",
        current_version="1.0.0",
        skills_dir=skills_dir,
        registry_manifest=manifest,
        promote=True,
        reviewer="lead-engineer",
    )
    assert prom_report.promoted is True
    assert "v1.1.0" in (skill / "SKILL.md").read_text(encoding="utf-8")

    rollback_skill_promotion(
        skill_name="mtm-bug-worker",
        previous_version="1.0.0",
        backup_content=prom_report.backup_content,
        skills_dir=skills_dir,
        registry_manifest=manifest,
    )
    assert (skill / "SKILL.md").read_text(encoding="utf-8") == "# Original\n"


def test_release_manifest_pins_both_skills_and_agent_profiles():
    root = Path(__file__).resolve().parent.parent
    assert verify_skill_checksums(root) is True
    assert verify_profile_checksums(root) is True
