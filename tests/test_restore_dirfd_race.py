"""Fd-relative FileBackup.restore(): ancestor swapped for a symlink after verification must not redirect the write."""
import os
import shutil

import pytest

import mtm_agents.dispatcher as dispatcher
from mtm_agents.dispatcher import FileBackup

posix_only = pytest.mark.skipif(
    not dispatcher._dirfd_restore_supported(),
    reason="fd-relative no-follow directory operations require POSIX",
)


@posix_only
def test_ancestor_swapped_to_symlink_after_verification_is_fail_closed(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    agents = ws / ".codex" / "agents"
    agents.mkdir(parents=True)
    target = agents / "bug-fixer.toml"
    target.write_text("original", encoding="utf-8")
    backup = FileBackup.capture(target, workspace_root=ws)

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "bug-fixer.toml").write_text("EXTERNAL", encoding="utf-8")

    real_verify = dispatcher._prepare_and_verify_ancestors

    def verify_then_swap(dest, root):
        real_verify(dest, root)  # verification passes on the real directory...
        shutil.rmtree(root / ".codex" / "agents")
        os.symlink(outside, root / ".codex" / "agents")  # ...then the attacker swaps it

    monkeypatch.setattr(dispatcher, "_prepare_and_verify_ancestors", verify_then_swap)

    with pytest.raises(ValueError):
        backup.restore(workspace_root=ws)

    assert (outside / "bug-fixer.toml").read_text(encoding="utf-8") == "EXTERNAL"
    assert sorted(p.name for p in outside.iterdir()) == ["bug-fixer.toml"], "no temp file may leak outside"


@posix_only
def test_dirfd_restore_replaces_destination_symlink_without_following(tmp_path):
    ws = tmp_path / "ws"
    agents = ws / ".agents" / "agents"
    agents.mkdir(parents=True)
    target = agents / "a.md"
    target.write_text("original", encoding="utf-8")
    os.utime(target, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    backup = FileBackup.capture(target, workspace_root=ws)

    outside = tmp_path / "outside.txt"
    outside.write_text("EXTERNAL", encoding="utf-8")
    target.unlink()
    os.symlink(outside, target)

    backup.restore(workspace_root=ws)

    assert not target.is_symlink()
    assert target.read_text(encoding="utf-8") == "original"
    assert outside.read_text(encoding="utf-8") == "EXTERNAL"
    assert target.stat().st_mtime_ns == 1_700_000_000_000_000_000
    assert [p.name for p in agents.iterdir()] == ["a.md"], "temp file must be cleaned up"
