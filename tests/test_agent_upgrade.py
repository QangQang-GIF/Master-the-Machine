"""Offline regression tests: portable profiles, native dispatcher, incremental knowledge, credits, and DEV learning."""
import hashlib
import os
from pathlib import Path
import stat
import time
import tomllib
from unittest.mock import MagicMock, patch

import pytest

from mtm_agents.profiles import resolve_profile, export_native_agents
from mtm_agents.context import compile_task_context
from mtm_agents.dispatcher import FileBackup, NativeSubagentDispatcher
from mtm_core.config.loader import verify_profile_checksums, verify_skill_checksums
from mtm_core.runtime import CodexRuntime, ExecutionTrace, RuntimeResult, TokenUsage
from mtm_knowledge import build_index, retrieve_knowledge, clear_index_cache


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


def test_normalized_credit_accounting_and_policy_sandbox():
    u_fast = TokenUsage.calculate(2000, 1000, 500, 100, model="gemini-3.8-flash-low", backend="antigravity")
    u_std = TokenUsage.calculate(2000, 1000, 500, 100, model="gpt-6.1-sol", backend="codex")
    u_deep = TokenUsage.calculate(2000, 1000, 500, 100, model="claude-sonnet-4-6", backend="antigravity")
    assert u_fast.normalized_credits < u_std.normalized_credits < u_deep.normalized_credits
    assert "normalized_credits" in u_std.to_dict()

    rt = CodexRuntime()
    cmd_codex = rt.build_codex_cmd(
        workspace=Path("/tmp"),
        prompt="Audit code",
        sandbox_policy="workspace-write",
        agent="bug-fixer",
    )
    assert "codex" in cmd_codex
    assert "exec" in cmd_codex
    assert "-s" in cmd_codex and "workspace-write" in cmd_codex
    assert any("bug-fixer" in arg for arg in cmd_codex)


def test_release_manifest_pins_both_skills_and_agent_profiles():
    root = Path(__file__).resolve().parent.parent
    assert verify_skill_checksums(root) is True
    assert verify_profile_checksums(root) is True


def test_dispatcher_collision_backup_and_restore_exact_content_and_hash(tmp_path):
    """R2: Pre-existing colliding files retain exact pre-run content, SHA-256 hash, and ephemeral files are unlinked."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    pre_codex = codex_dir / "bug-fixer.toml"
    pre_codex_content = b'# CUSTOM PRE-EXISTING CODEX CONFIG\nname = "my-custom-bug-fixer"\nversion = "9.9.9"\n'
    pre_codex.write_bytes(pre_codex_content)
    pre_codex_hash = hashlib.sha256(pre_codex_content).hexdigest()

    agy_dir = tmp_path / ".agents" / "agents"
    agy_dir.mkdir(parents=True)
    pre_agy = agy_dir / "bug-fixer.md"
    pre_agy_content = b'# CUSTOM PRE-EXISTING AGY AGENT\nrole: custom-bug-fixer\nspecialization: high-priority-audit\n'
    pre_agy.write_bytes(pre_agy_content)
    pre_agy_hash = hashlib.sha256(pre_agy_content).hexdigest()

    mock_rt = MagicMock(spec=CodexRuntime)

    def _inspect_in_flight(**kwargs):
        ws = Path(kwargs["workspace"])
        # During dispatch, injected profile content replaced custom content
        assert (ws / ".codex/agents/bug-fixer.toml").read_bytes() != pre_codex_content
        assert (ws / ".agents/agents/bug-fixer.md").read_bytes() != pre_agy_content
        # Ephemeral files exist during execution
        assert (ws / ".codex/agents/feature-implementer.toml").is_file()
        assert (ws / ".agents/agents/feature-implementer.md").is_file()
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(input_tokens=150, output_tokens=30),
            usage={},
            commands=1,
            file_changes=1,
            last_message="Completed",
            elapsed=0.1,
        )

    mock_rt.run.side_effect = _inspect_in_flight
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    result = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Fix edge case",
        workspace=tmp_path,
        backend="antigravity",
    )

    assert result.exit_code == 0

    # 1. Colliding files exist and retain exact content and SHA-256 hash
    assert pre_codex.exists()
    assert pre_codex.read_bytes() == pre_codex_content
    assert hashlib.sha256(pre_codex.read_bytes()).hexdigest() == pre_codex_hash

    assert pre_agy.exists()
    assert pre_agy.read_bytes() == pre_agy_content
    assert hashlib.sha256(pre_agy.read_bytes()).hexdigest() == pre_agy_hash

    # 2. Ephemeral files generated during injection are unlinked
    assert not (codex_dir / "feature-implementer.toml").exists()
    assert not (agy_dir / "feature-implementer.md").exists()
    assert not (codex_dir / "incident-responder.toml").exists()
    assert not (agy_dir / "incident-responder.md").exists()


def test_dispatcher_preserves_nanosecond_timestamps(tmp_path):
    """R2: Pre-existing colliding files preserve exact nanosecond timestamps across dispatch."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target_file = codex_dir / "bug-fixer.toml"
    target_file.write_bytes(b'name = "timestamp-fidelity-test"\n')

    # Set historical timestamp (1 hour ago, rounded to 100ns for Windows NTFS precision)
    hist_mtime_ns = int((time.time() - 3600) * 1_000_000_000)
    hist_mtime_ns = (hist_mtime_ns // 100) * 100
    os.utime(target_file, ns=(hist_mtime_ns, hist_mtime_ns))
    pre_mtime_ns = target_file.stat().st_mtime_ns

    mock_rt = MagicMock(spec=CodexRuntime)
    mock_rt.run.return_value = RuntimeResult(
        exit_code=0,
        timed_out=False,
        trace=ExecutionTrace(),
        usage={},
        commands=0,
        file_changes=0,
        last_message="OK",
        elapsed=0.05,
    )

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Check mtime",
        workspace=tmp_path,
    )

    post_mtime_ns = target_file.stat().st_mtime_ns
    assert post_mtime_ns == pre_mtime_ns, f"mtime changed from {pre_mtime_ns} to {post_mtime_ns}"


def test_dispatcher_non_colliding_files_and_directories_untouched(tmp_path):
    """R2: Pre-existing custom files and directories in .codex and .agents are not removed."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    user_tool = codex_dir / "custom-user-tool.toml"
    user_content = b'tool_name = "secret-scanner"\nversion = "2.1.0"\n'
    user_tool.write_bytes(user_content)

    custom_subdir = tmp_path / ".agents" / "custom_dir"
    custom_subdir.mkdir(parents=True)
    custom_file = custom_subdir / "custom_doc.txt"
    custom_file.write_text("pre-existing custom document", encoding="utf-8")

    mock_rt = MagicMock(spec=CodexRuntime)
    mock_rt.run.return_value = RuntimeResult(
        exit_code=0,
        timed_out=False,
        trace=ExecutionTrace(),
        usage={},
        commands=0,
        file_changes=0,
        last_message="OK",
        elapsed=0.05,
    )

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Verify untouched files",
        workspace=tmp_path,
    )

    assert user_tool.exists()
    assert user_tool.read_bytes() == user_content
    assert codex_dir.exists()

    assert custom_file.exists()
    assert custom_file.read_text(encoding="utf-8") == "pre-existing custom document"
    assert custom_subdir.exists()


def test_dispatcher_restores_on_runtime_exception(tmp_path):
    """R2: In-memory backup restores colliding files even if runtime throws an unexpected exception."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target_file = codex_dir / "bug-fixer.toml"
    initial_bytes = b'name = "crash-resilience-check"\n'
    target_file.write_bytes(initial_bytes)
    initial_hash = hashlib.sha256(initial_bytes).hexdigest()

    mock_rt = MagicMock(spec=CodexRuntime)
    mock_rt.run.side_effect = RuntimeError("Worker process crashed unexpectedly with SIGSEGV")

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    with pytest.raises(RuntimeError, match="Worker process crashed"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Crash simulation",
            workspace=tmp_path,
        )

    assert target_file.exists()
    assert target_file.read_bytes() == initial_bytes
    assert hashlib.sha256(target_file.read_bytes()).hexdigest() == initial_hash
    # Ephemeral files are still unlinked
    assert not (codex_dir / "feature-implementer.toml").exists()


def test_dispatcher_read_only_preexisting_file(tmp_path):
    """R2: Windows and Linux read-only pre-existing files are safely backed up and restored without PermissionError."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    ro_file = codex_dir / "bug-fixer.toml"
    ro_content = b'# READONLY PROTECTED TOML\nlocked = true\nimmutable = true\n'
    ro_file.write_bytes(ro_content)

    os.chmod(ro_file, stat.S_IREAD)
    assert not os.access(ro_file, os.W_OK)

    mock_rt = MagicMock(spec=CodexRuntime)
    mock_rt.run.return_value = RuntimeResult(
        exit_code=0,
        timed_out=False,
        trace=ExecutionTrace(),
        usage={},
        commands=0,
        file_changes=0,
        last_message="OK",
        elapsed=0.05,
    )

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    try:
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Read-only test",
            workspace=tmp_path,
        )

        assert ro_file.exists()
        assert ro_file.read_bytes() == ro_content
        assert not os.access(ro_file, os.W_OK)
    finally:
        # Reset writable permission so pytest tmp_path cleanup succeeds
        try:
            os.chmod(ro_file, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass


def test_dispatcher_fail_closed_on_unreadable_file(tmp_path):
    """R1: If a pre-existing agent file cannot be read, dispatch() fails closed without overwriting or deleting it."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target = codex_dir / "bug-fixer.toml"
    initial_bytes = b"ORIGINAL PRE-EXISTING CUSTOM AGENT"
    target.write_bytes(initial_bytes)

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())

    with patch("pathlib.Path.read_bytes", side_effect=PermissionError("Permission denied: unreadable")):
        with pytest.raises((PermissionError, OSError)):
            dispatcher.dispatch(
                envelope={"task_type": "BUG"},
                prompt="Fail closed test",
                workspace=tmp_path,
            )

    # Verification: Target exists, was never overwritten, and was never deleted
    assert target.exists()
    assert target.read_bytes() == initial_bytes
    assert not (codex_dir / "feature-implementer.toml").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX write-only file permission test")
def test_dispatcher_fail_closed_on_posix_unreadable_file(tmp_path):
    """R1: Native POSIX write-only (0o200) unreadable file causes dispatch() to fail closed."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target = codex_dir / "bug-fixer.toml"
    initial_bytes = b"POSIX CUSTOM UNREADABLE AGENT"
    target.write_bytes(initial_bytes)
    os.chmod(target, stat.S_IWUSR)

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    try:
        with pytest.raises((PermissionError, OSError)):
            dispatcher.dispatch(
                envelope={"task_type": "BUG"},
                prompt="Fail closed POSIX test",
                workspace=tmp_path,
            )
        assert target.exists()
    finally:
        os.chmod(target, stat.S_IWRITE | stat.S_IREAD)
        assert target.read_bytes() == initial_bytes


def test_dispatcher_blocks_codex_agents_symlink_escape(tmp_path):
    """R2: If .codex/agents is a symlink pointing outside the workspace, dispatch() raises ValueError."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()

    (ws / ".codex").mkdir()
    symlink_path = ws / ".codex" / "agents"
    try:
        os.symlink(outside, symlink_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Escape test",
            workspace=ws,
        )

    # Verification: zero files written outside workspace
    assert list(outside.iterdir()) == []


def test_dispatcher_blocks_agents_agents_symlink_escape(tmp_path):
    """R2: If .agents/agents is a symlink pointing outside the workspace, dispatch() raises ValueError."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_dir"
    outside.mkdir()

    (ws / ".agents").mkdir()
    symlink_path = ws / ".agents" / "agents"
    try:
        os.symlink(outside, symlink_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Escape test",
            workspace=ws,
        )

    assert list(outside.iterdir()) == []


def test_dispatcher_blocks_individual_file_symlink_escape(tmp_path):
    """R2: If an individual agent config is a symlink pointing outside workspace, dispatch() raises ValueError."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    codex_agents = ws / ".codex" / "agents"
    codex_agents.mkdir(parents=True)

    secret_file = tmp_path / "sensitive.txt"
    secret_bytes = b"SECRET HOST CREDENTIALS"
    secret_file.write_bytes(secret_bytes)

    target_symlink = codex_agents / "bug-fixer.toml"
    try:
        os.symlink(secret_file, target_symlink)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="File escape test",
            workspace=ws,
        )

    # Verification: sensitive file was never overwritten
    assert secret_file.read_bytes() == secret_bytes


def test_dispatcher_blocks_windows_junction_escape(tmp_path):
    """R2: If .codex/agents is a Windows directory junction pointing outside workspace, dispatch() raises ValueError."""
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_junction"
    outside.mkdir()

    (ws / ".codex").mkdir()
    junction_path = ws / ".codex" / "agents"
    _winapi.CreateJunction(str(outside), str(junction_path))

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Junction test",
            workspace=ws,
        )

    assert list(outside.iterdir()) == []


def test_cleanup_workspace_agents_does_not_delete_symlink_target(tmp_path):
    """R2: Ephemeral file cleanup unlinks the symlink itself without deleting the external target file."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    external_target = tmp_path / "external_target.txt"
    external_target.write_bytes(b"DO NOT DELETE ME")

    symlink_file = ws / "ephemeral_link.txt"
    try:
        os.symlink(external_target, symlink_file)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    # Call cleanup_workspace_agents with the symlink as an ephemeral file
    NativeSubagentDispatcher.cleanup_workspace_agents(
        written_files=[symlink_file],
        created_dirs=[],
    )

    # Verification: symlink is unlinked, but external target is preserved!
    assert not symlink_file.exists()
    assert external_target.exists()
    assert external_target.read_bytes() == b"DO NOT DELETE ME"


def test_dispatcher_restore_replaces_adversarial_symlink_without_writing_to_target(tmp_path):
    """R2: If an agent replaces a backed-up file with an external symlink during execution,

    restore() unlinks the symlink, does not write to the external file, and recreates
    the original file with exact pre-run content, SHA-256 hash, and nanosecond timestamps.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    codex_dir = ws / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    pre_file = codex_dir / "bug-fixer.toml"
    orig_content = b'# ORIGINAL CUSTOM TOML CONFIG\nname = "original-bug-fixer"\nversion = "1.0.0"\n'
    pre_file.write_bytes(orig_content)
    orig_hash = hashlib.sha256(orig_content).hexdigest()

    # Set historical timestamp (aligned to 100ns for Windows NTFS precision)
    hist_mtime_ns = int((time.time() - 3600) * 1_000_000_000) // 100 * 100
    os.utime(pre_file, ns=(hist_mtime_ns, hist_mtime_ns))
    pre_mtime_ns = pre_file.stat().st_mtime_ns

    # Create external sensitive target
    outside_dir = tmp_path / "outside_sensitive_dir"
    outside_dir.mkdir()
    sensitive_file = outside_dir / "host_sensitive.txt"
    sensitive_bytes = b"SECRET HOST CREDENTIALS - NEVER OVERWRITE"
    sensitive_file.write_bytes(sensitive_bytes)

    mock_rt = MagicMock(spec=CodexRuntime)

    def _adversarial_run(**kwargs):
        w = Path(kwargs["workspace"])
        target = w / ".codex" / "agents" / "bug-fixer.toml"
        assert target.exists()
        # Adversary replaces target with an external symlink pointing to sensitive_file
        target.unlink()
        try:
            os.symlink(sensitive_file, target)
        except OSError:
            pytest.skip("Symlink creation not supported in this environment")
        assert target.is_symlink()
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=1,
            file_changes=1,
            last_message="Adversarial run complete",
            elapsed=0.05,
        )

    mock_rt.run.side_effect = _adversarial_run
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    res = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Adversarial symlink test",
        workspace=ws,
    )
    assert res.exit_code == 0

    # 1. External sensitive file was NEVER written to or modified
    assert sensitive_file.exists()
    assert sensitive_file.read_bytes() == sensitive_bytes

    # 2. Target file was restored as a real file (not a symlink)
    assert pre_file.exists()
    assert not pre_file.is_symlink()

    # 3. Content and SHA-256 match original pre-run file exactly
    restored_bytes = pre_file.read_bytes()
    assert restored_bytes == orig_content
    assert hashlib.sha256(restored_bytes).hexdigest() == orig_hash

    # 4. Nanosecond timestamp fidelity is preserved
    assert pre_file.stat().st_mtime_ns == pre_mtime_ns


def test_dispatcher_restore_replaces_adversarial_junction_without_writing_to_target(tmp_path):
    """R2: If an agent replaces a backed-up file with a Windows directory junction during execution,

    restore() unlinks the junction, does not touch the outside target, and recreates the file.
    """
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    ws = tmp_path / "workspace"
    ws.mkdir()
    codex_dir = ws / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    pre_file = codex_dir / "bug-fixer.toml"
    orig_content = b'# ORIGINAL TOML\nname = "pre-junction"\n'
    pre_file.write_bytes(orig_content)
    orig_hash = hashlib.sha256(orig_content).hexdigest()

    outside_dir = tmp_path / "outside_vault"
    outside_dir.mkdir()
    canary_file = outside_dir / "canary.txt"
    canary_file.write_text("CANARY DATA", encoding="utf-8")

    mock_rt = MagicMock(spec=CodexRuntime)

    def _adversarial_run(**kwargs):
        w = Path(kwargs["workspace"])
        target = w / ".codex" / "agents" / "bug-fixer.toml"
        target.unlink()
        _winapi.CreateJunction(str(outside_dir), str(target))
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=1,
            file_changes=1,
            last_message="Adversarial junction run complete",
            elapsed=0.05,
        )

    mock_rt.run.side_effect = _adversarial_run
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    res = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Adversarial junction test",
        workspace=ws,
    )
    assert res.exit_code == 0

    # 1. External vault was untouched
    assert outside_dir.exists()
    assert canary_file.read_text(encoding="utf-8") == "CANARY DATA"

    # 2. Target file was restored as a real file
    assert pre_file.is_file()
    assert not (hasattr(pre_file, "is_junction") and pre_file.is_junction())
    assert pre_file.read_bytes() == orig_content
    assert hashlib.sha256(pre_file.read_bytes()).hexdigest() == orig_hash


def test_file_backup_restore_replaces_broken_symlink_and_directory_link(tmp_path):
    """R2: Direct FileBackup.restore() recovers files when replaced by broken symlinks or directory symlinks."""
    ws = tmp_path / "ws"
    ws.mkdir()
    target = ws / "config.toml"
    orig_bytes = b"sample_config = 42\n"
    target.write_bytes(orig_bytes)

    backup = FileBackup.capture(target, workspace_root=ws)
    assert backup.workspace_root == ws.resolve()

    # Case 1: Broken symlink replacement
    target.unlink()
    broken_link_target = tmp_path / "nonexistent.file"
    try:
        os.symlink(broken_link_target, target)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    assert not target.exists()
    assert target.is_symlink()

    backup.restore(workspace_root=ws)
    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_bytes() == orig_bytes

    # Case 2: Symlink pointing to outside directory
    target.unlink()
    outside_dir = tmp_path / "outside_dir"
    outside_dir.mkdir(exist_ok=True)
    canary = outside_dir / "canary.txt"
    canary.write_text("safe", encoding="utf-8")

    try:
        os.symlink(outside_dir, target, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlink creation not supported")

    assert target.is_symlink()

    backup.restore(workspace_root=ws)
    assert target.is_file()
    assert not target.is_symlink()
    assert target.read_bytes() == orig_bytes
    assert canary.read_text(encoding="utf-8") == "safe"


def test_r4_dispatcher_restore_fails_closed_on_ancestor_junction_swap(tmp_path):
    """R4 Test 2: In NativeSubagentDispatcher, if adversary swaps ancestor directory (.codex)

    with an external junction during task run, cleanup restore fails closed without creating
    or modifying any files in the external target, and leaks zero temp files.
    """
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    import shutil

    ws = tmp_path / "workspace_dispatch_ancestor"
    ws.mkdir()
    codex_dir = ws / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    pre_file = codex_dir / "bug-fixer.toml"
    orig_content = b'name = "pre-dispatch-agent"\n'
    pre_file.write_bytes(orig_content)

    outside_vault = tmp_path / "outside_dispatch_vault"
    outside_vault.mkdir()
    vault_canary = outside_vault / "vault_canary.txt"
    vault_canary.write_text("IMMUTABLE VAULT DATA", encoding="utf-8")

    mock_rt = MagicMock(spec=CodexRuntime)

    def _adversarial_run(**kwargs):
        w = Path(kwargs["workspace"])
        # Adversary removes w / ".codex" and replaces it with a junction to outside_vault
        shutil.rmtree(w / ".codex")
        _winapi.CreateJunction(str(outside_vault), str(w / ".codex"))
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=1,
            file_changes=1,
            last_message="Adversarial run complete",
            elapsed=0.05,
        )

    mock_rt.run.side_effect = _adversarial_run
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    with pytest.raises((ValueError, PermissionError), match="workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Adversarial ancestor junction attack",
            workspace=ws,
        )

    # External vault was untouched and zero new files/directories created in external vault
    assert vault_canary.read_text(encoding="utf-8") == "IMMUTABLE VAULT DATA"
    assert [p.name for p in outside_vault.iterdir()] == ["vault_canary.txt"]

    # Zero temp files leaked anywhere
    assert list(outside_vault.rglob(".restore_*")) == []
    assert list(ws.rglob(".restore_*")) == []



