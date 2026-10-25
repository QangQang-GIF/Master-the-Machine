"""Offline regression tests: portable profiles, safe knowledge and DEV evaluation."""
from pathlib import Path
import tomllib

import pytest

from mtm_agents.profiles import resolve_profile, export_native_agents
from mtm_agents.context import compile_task_context
from mtm_backends import probe_backend, select_available_backend
from mtm_knowledge import build_index, retrieve_knowledge
from mtm_training.pipeline import evaluate_paired_runs, propose_from_failures


def test_profiles_render_both_native_formats(tmp_path):
    profile = resolve_profile("BUG")
    assert profile.name == "bug-fixer"
    assert tomllib.loads(profile.codex_toml())["name"] == "bug-fixer"
    assert "name: bug-fixer" in profile.antigravity_markdown()
    generated = export_native_agents(tmp_path)
    assert len(generated) == 8
    assert (tmp_path / ".codex/agents/bug-fixer.toml").is_file()
    assert (tmp_path / ".agents/agents/incident-responder.md").is_file()


def test_knowledge_index_is_bounded_and_excludes_secrets(tmp_path):
    (tmp_path / "service.py").write_text("def calculate_total(x):\n    return x\n")
    (tmp_path / "test_service.py").write_text("def test_total():\n    pass\n")
    (tmp_path / ".env").write_text("PASSWORD=abc")
    (tmp_path / "private_key.py").write_text("def bad():\n    pass\n")
    idx = build_index(tmp_path)
    paths = [f["path"] for f in idx["files"]]
    assert "service.py" in paths
    assert ".env" not in paths
    assert "private_key.py" not in paths
    summary = retrieve_knowledge(idx, "calculate_total service", max_chars=500)
    assert "service.py" in summary
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


def test_training_evaluation_requires_sufficient_pairs():
    base = {str(i): {"passed": True, "tokens": 100} for i in range(5)}
    improved = {str(i): {"passed": True, "tokens": 90} for i in range(5)}
    assert evaluate_paired_runs(base, improved).eligible is True
    improved["0"]["passed"] = False
    assert evaluate_paired_runs(base, improved).eligible is False
    assert evaluate_paired_runs(base, {"0": improved["0"]}).eligible is False


def test_training_candidate_is_dry_run(tmp_path):
    skill = tmp_path / "mtm-bug-worker"
    skill.mkdir()
    (skill / "SKILL.md").write_text("# Original\n")
    events = [{"status": "TIMED_OUT", "changed_files": []} for _ in range(3)]
    proposal = propose_from_failures("mtm-bug-worker", "1.0.0", events, skills_dir=str(tmp_path))
    assert proposal is not None
    assert proposal.candidate_version == "1.1.0"
    assert (skill / "SKILL.md").read_text() == "# Original\n"
