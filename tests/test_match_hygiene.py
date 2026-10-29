from pathlib import Path
import pytest
from runner.match_hygiene import purge_history


def stopped():
    return {"cliSupervisor": {"status": "STOPPED", "pid": None}}


def test_removes_local_old_solutions_but_preserves_current_and_audit(tmp_path):
    old = tmp_path / "matches/OLD12345/OLD12345/answers/TASK-01"
    old.mkdir(parents=True)
    (old / "submit.json").write_text("old synthetic artifact")
    current = tmp_path / "matches/NEW12345/README.md"
    current.parent.mkdir(parents=True)
    current.write_text("new source")
    audit = tmp_path / "team-sdlc-pack/logs/OLD12345.progress.ndjson"
    audit.parent.mkdir(parents=True)
    audit.write_text("official audit")
    removed = purge_history(tmp_path, "NEW12345", stopped())
    assert "matches/OLD12345" in removed
    assert not old.exists() and current.exists() and audit.exists()


def test_no_deletion_with_running_or_pending_runtime(tmp_path):
    old = tmp_path / "matches/OLD12345"
    old.mkdir(parents=True)
    with pytest.raises(ValueError, match="STOPPED"):
        purge_history(tmp_path, "NEW12345", {"cliSupervisor": {"status": "IDLE", "pid": 123}})
    assert old.exists()


def test_rejects_symlink_escape_and_keeps_external_material(tmp_path):
    external = tmp_path / "outside"
    external.mkdir()
    (tmp_path / "matches").symlink_to(external, target_is_directory=True)
    with pytest.raises(ValueError, match="Unsafe"):
        purge_history(tmp_path, "NEW12345", stopped())
    assert external.exists()
