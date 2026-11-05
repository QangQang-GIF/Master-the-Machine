import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("mtm_install", ROOT / "scripts" / "install.py")
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)

BEGIN, END = "<!-- MTM-PLAYER:BEGIN -->", "<!-- MTM-PLAYER:END -->"


def test_sync_replaces_stale_limits_and_keeps_player_block(tmp_path):
    src, ws = tmp_path / "src", tmp_path / "ws"
    src.mkdir(); ws.mkdir()
    (src / "AGENTS.md").write_text("# base\n4 normal + 1 PI\n", encoding="utf-8")
    (ws / "AGENTS.md").write_text(f"# old\n1 normal worker slot\n\n{BEGIN}\nmatch RLL3DYW5\n{END}\n", encoding="utf-8")
    assert install.sync_agents_md(src, ws) is True
    text = (ws / "AGENTS.md").read_text(encoding="utf-8")
    assert "1 normal worker slot" not in text and "4 normal + 1 PI" in text
    assert f"{BEGIN}\nmatch RLL3DYW5\n{END}" in text
    assert install.sync_agents_md(src, ws) is False  # idempotent


def test_repo_agents_md_states_four_plus_one():
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "4 normal slots" in text and "1 reserved slot" in text
    assert "1 normal worker slot" not in text


def test_both_skill_copies_match_and_state_four_plus_one():
    a = (ROOT / ".agents/skills/team-orchestrator/SKILL.md").read_text(encoding="utf-8")
    b = (ROOT / "skills/team-orchestrator/SKILL.md").read_text(encoding="utf-8")
    assert a == b  # install overlays skills/ onto .agents/skills/, so a stale copy would win
    assert "4 normal slots" in a and "3 normal slots" not in a
