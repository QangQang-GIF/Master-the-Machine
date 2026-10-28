"""Empirical Adversarial Test Suite for NativeSubagentDispatcher.

Focus Areas:
1. Pre-existing files containing arbitrary binary sequences (null bytes, invalid UTF-8, CRLF/LF mix).
2. Pre-existing read-only and restricted attributes on Windows (stat.S_IREAD).
3. Git working tree cleanliness test (git status --porcelain 100% clean post-cleanup).
4. Decoupling stress: conflicting parameters, env vars, non-standard casing, empty strings.
5. Nanosecond timestamp and SHA-256 restoration fidelity.
6. Workspace paths with unicode, spaces, and special characters.
7. WorkspacePreparation backward-compatible unpacking calling conventions.
8. Nested / sequential dispatch restoration integrity.
9. Export failure midway rollback and backup preservation.
"""
import hashlib
import os
from pathlib import Path
import stat
import subprocess
import time
from unittest.mock import MagicMock, patch
import pytest

import sys
from mtm_agents.dispatcher import (
    NativeSubagentDispatcher,
    WorkspacePreparation,
    WrittenFiles,
    FileBackup,
)
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace
from mtm_core.runtime.process import is_pid_alive, terminate_process_tree, _kill_unix_descendants


# ==============================================================================
# Area 1: Pre-existing Files with Arbitrary Binary Sequences
# ==============================================================================

def test_preexisting_arbitrary_binary_sequences_fidelity(tmp_path):
    """Challenge: Verify exact raw bytes, SHA-256 digest, and timestamps are restored

    for files containing null bytes, invalid UTF-8, mixed line endings, control chars,
    large binary buffers, and zero-length files across all agent profile targets.
    """
    codex_dir = tmp_path / ".codex" / "agents"
    agy_dir = tmp_path / ".agents" / "agents"
    codex_dir.mkdir(parents=True)
    agy_dir.mkdir(parents=True)

    test_payloads = {
        codex_dir / "bug-fixer.toml": b"\x00\x00\x01\x00\xfe\xff\x00\x1a\x00\x00\x00\x7f\x80\x00",
        agy_dir / "bug-fixer.md": b"\xff\xfe\xfd\x80\x81\xc0\xc1\x8f\x90\xaa\xbb\xcc\xdd\xee",
        codex_dir / "feature-implementer.toml": b"toml_line1 = 1\r\nkey2 = 2\nkey3 = 3\rkey4 = 4\r\n\x00end",
        agy_dir / "feature-implementer.md": os.urandom(262144),  # 256KB random binary
        codex_dir / "general-task-worker.toml": b"",  # 0-byte file
        agy_dir / "general-task-worker.md": bytes(range(0, 32)),  # all C0 control characters
        codex_dir / "incident-responder.toml": "\ufeff# UTF-8 with BOM\nname = '日本語'\n".encode("utf-8-sig"),
        agy_dir / "incident-responder.md": b"Markdown Header\x00Embedded Null\r\nLine 2\n\x00\x00\xff",
    }

    # Write all test payloads and record pre-dispatch SHA-256 and timestamps
    pre_digests = {}
    pre_mtimes = {}
    for p, payload in test_payloads.items():
        p.write_bytes(payload)
        pre_digests[p] = hashlib.sha256(payload).hexdigest()
        # Set distinct mtime 1 hour in the past (aligned to 100ns for Windows NTFS precision)
        mtime_ns = int((time.time() - 3600) * 1_000_000_000)
        mtime_ns = (mtime_ns // 100) * 100
        os.utime(p, ns=(mtime_ns, mtime_ns))
        pre_mtimes[p] = p.stat().st_mtime_ns

    mock_rt = MagicMock(spec=CodexRuntime)

    def _verify_in_flight(**kwargs):
        ws = Path(kwargs["workspace"])
        # All 8 files must have been overwritten by dispatcher during execution
        for target_p, orig_content in test_payloads.items():
            in_flight_content = target_p.read_bytes()
            assert in_flight_content != orig_content, f"File {target_p} was not overwritten during dispatch"
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=1,
            file_changes=1,
            last_message="Completed",
            elapsed=0.1,
        )

    mock_rt.run.side_effect = _verify_in_flight
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    result = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Adversarial binary content stress test",
        workspace=tmp_path,
    )
    assert result.exit_code == 0

    # Post-cleanup assertions: 100% exact raw byte restoration, SHA-256 match, mtime match
    for p, expected_bytes in test_payloads.items():
        assert p.exists(), f"File {p} does not exist post-cleanup"
        restored_bytes = p.read_bytes()
        assert restored_bytes == expected_bytes, f"Byte mismatch for {p.name}"
        assert hashlib.sha256(restored_bytes).hexdigest() == pre_digests[p], f"SHA-256 mismatch for {p.name}"
        assert p.stat().st_mtime_ns == pre_mtimes[p], f"mtime_ns mismatch for {p.name}"


# ==============================================================================
# Area 2: Pre-existing Read-Only and Restricted Attributes (Windows stat.S_IREAD)
# ==============================================================================

def test_preexisting_windows_read_only_and_restricted_attributes(tmp_path):
    """Challenge: Windows read-only files (stat.S_IREAD) must be backed up, overwritten,

    restored, and retain their read-only attribute post-cleanup without PermissionError.
    Non-colliding read-only files must remain completely untouched.
    """
    codex_dir = tmp_path / ".codex" / "agents"
    agy_dir = tmp_path / ".agents" / "agents"
    codex_dir.mkdir(parents=True)
    agy_dir.mkdir(parents=True)

    ro_colliding_codex = codex_dir / "bug-fixer.toml"
    ro_colliding_agy = agy_dir / "bug-fixer.md"
    ro_non_colliding = codex_dir / "custom-readonly-tool.toml"

    codex_content = b'# READONLY COLLIDING CODEX\nkey = "secret"\n'
    agy_content = b'# READONLY COLLIDING AGY\nrole = "auditor"\n'
    non_colliding_content = b'# NON COLLIDING READONLY\ncustom = true\n'

    ro_colliding_codex.write_bytes(codex_content)
    ro_colliding_agy.write_bytes(agy_content)
    ro_non_colliding.write_bytes(non_colliding_content)

    # Set read-only attribute
    for p in (ro_colliding_codex, ro_colliding_agy, ro_non_colliding):
        os.chmod(p, stat.S_IREAD)
        assert not os.access(p, os.W_OK), f"File {p} is not read-only"

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
            prompt="Read-only stress test",
            workspace=tmp_path,
        )

        # 1. Colliding files restored and read-only attribute preserved
        assert ro_colliding_codex.exists()
        assert ro_colliding_codex.read_bytes() == codex_content
        assert not os.access(ro_colliding_codex, os.W_OK)

        assert ro_colliding_agy.exists()
        assert ro_colliding_agy.read_bytes() == agy_content
        assert not os.access(ro_colliding_agy, os.W_OK)

        # 2. Non-colliding read-only file untouched
        assert ro_non_colliding.exists()
        assert ro_non_colliding.read_bytes() == non_colliding_content
        assert not os.access(ro_non_colliding, os.W_OK)

        # 3. Ephemeral files cleanly deleted
        assert not (codex_dir / "feature-implementer.toml").exists()
        assert not (agy_dir / "feature-implementer.md").exists()
    finally:
        # Restore write permissions for pytest temp cleanup
        for p in (ro_colliding_codex, ro_colliding_agy, ro_non_colliding):
            try:
                os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
            except OSError:
                pass


# ==============================================================================
# Area 3: Git Working Tree Cleanliness Post-Dispatch
# ==============================================================================

def test_git_working_tree_cleanliness_post_dispatch(tmp_path):
    """Challenge: Initialize a real git repo, commit pre-existing agent configurations,

    run dispatcher, and verify `git status --porcelain` is 100% clean post-cleanup.
    """
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Challenger2"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "challenger2@example.com"], cwd=str(tmp_path), check=True)

    codex_dir = tmp_path / ".codex" / "agents"
    agy_dir = tmp_path / ".agents" / "agents"
    codex_dir.mkdir(parents=True)
    agy_dir.mkdir(parents=True)

    # Pre-existing committed agent configs
    (codex_dir / "bug-fixer.toml").write_bytes(b'name = "git-committed-bug-fixer"\nversion = "1.0.0"\n')
    (agy_dir / "bug-fixer.md").write_bytes(b'# GIT COMMITTED AGY AGENT\nrole: bug-fixer\n')
    (codex_dir / "custom-committed-agent.toml").write_bytes(b'name = "custom-persisted"\n')
    (tmp_path / "README.md").write_text("# Test Repo\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "Initial commit"], cwd=str(tmp_path), check=True)

    # Verify baseline is 100% clean
    status_pre = subprocess.run(["git", "status", "--porcelain"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert status_pre.stdout.strip() == "", f"Pre-dispatch git repo not clean: {status_pre.stdout}"

    mock_rt = MagicMock(spec=CodexRuntime)

    def _check_in_flight(**kwargs):
        # In-flight status should show modified files and untracked ephemeral files
        st = subprocess.run(["git", "status", "--porcelain"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
        assert len(st.stdout.strip()) > 0, "In-flight git repo should have changes"
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=1,
            file_changes=1,
            last_message="OK",
            elapsed=0.05,
        )

    mock_rt.run.side_effect = _check_in_flight
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Git cleanliness verification",
        workspace=tmp_path,
    )

    # Post-cleanup assertions: git status --porcelain must be completely empty!
    status_post = subprocess.run(["git", "status", "--porcelain"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert status_post.stdout.strip() == "", f"Git working tree is dirty post-cleanup!\nOutput:\n{status_post.stdout}"

    diff_post = subprocess.run(["git", "diff"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert diff_post.stdout.strip() == "", f"Git diff is not empty post-cleanup!\nDiff:\n{diff_post.stdout}"


def test_git_working_tree_cleanliness_after_runtime_crash(tmp_path):
    """Challenge: In a committed git repo, if the runtime crashes with an exception,

    cleanup must still run and restore git status --porcelain to 100% clean.
    """
    subprocess.run(["git", "init"], cwd=str(tmp_path), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Challenger2"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "challenger2@example.com"], cwd=str(tmp_path), check=True)

    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    (codex_dir / "bug-fixer.toml").write_bytes(b'name = "pre-crash-config"\n')
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "commit", "-m", "Commit before crash"], cwd=str(tmp_path), check=True)

    mock_rt = MagicMock(spec=CodexRuntime)
    mock_rt.run.side_effect = RuntimeError("Fatal hardware failure in child process")

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)
    with pytest.raises(RuntimeError, match="Fatal hardware failure"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Crash test",
            workspace=tmp_path,
        )

    status_post = subprocess.run(["git", "status", "--porcelain"], cwd=str(tmp_path), capture_output=True, text=True, check=True)
    assert status_post.stdout.strip() == "", f"Git repo dirty after runtime crash: {status_post.stdout}"


# ==============================================================================
# Area 6: Workspace Paths with Unicode, Spaces, and Special Characters
# ==============================================================================

def test_workspace_with_unicode_and_spaces(tmp_path):
    """Challenge: Dispatcher must function cleanly in directories containing spaces and unicode."""
    ws = tmp_path / "Dự Án Đội Tuyển #1 (Subagent Workspace)"
    ws.mkdir(parents=True)

    codex_dir = ws / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    custom_file = codex_dir / "bug-fixer.toml"
    initial_content = b'# UNICODE PATH PRE-EXISTING FILE\nvalid = true\n'
    custom_file.write_bytes(initial_content)
    init_hash = hashlib.sha256(initial_content).hexdigest()

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
        prompt="Unicode path test",
        workspace=ws,
    )

    assert custom_file.exists()
    assert custom_file.read_bytes() == initial_content
    assert hashlib.sha256(custom_file.read_bytes()).hexdigest() == init_hash


# ==============================================================================
# Area 7: WorkspacePreparation Backward-Compatible Unpacking Conventions
# ==============================================================================

def test_cleanup_calling_conventions_backward_compatibility(tmp_path):
    """Challenge: Verify all 3 calling conventions of cleanup_workspace_agents restore backups:

    1. cleanup_workspace_agents(prep)
    2. written, created_dirs = prep; cleanup_workspace_agents(written, created_dirs)
    3. cleanup_workspace_agents((written, created_dirs))
    """
    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())

    # 1. Direct prep call
    ws1 = tmp_path / "ws1"
    ws1.mkdir()
    (ws1 / ".codex" / "agents").mkdir(parents=True)
    f1 = ws1 / ".codex" / "agents" / "bug-fixer.toml"
    f1.write_bytes(b'case1 = true\n')
    prep1 = dispatcher.prepare_workspace_agents(ws1)
    assert f1.read_bytes() != b'case1 = true\n'
    dispatcher.cleanup_workspace_agents(prep1)
    assert f1.read_bytes() == b'case1 = true\n'

    # 2. Legacy tuple unpack call: written, created_dirs
    ws2 = tmp_path / "ws2"
    ws2.mkdir()
    (ws2 / ".codex" / "agents").mkdir(parents=True)
    f2 = ws2 / ".codex" / "agents" / "bug-fixer.toml"
    f2.write_bytes(b'case2 = true\n')
    prep2 = dispatcher.prepare_workspace_agents(ws2)
    written2, created_dirs2 = prep2
    assert isinstance(written2, WrittenFiles)
    assert f2.read_bytes() != b'case2 = true\n'
    dispatcher.cleanup_workspace_agents(written2, created_dirs2)
    assert f2.read_bytes() == b'case2 = true\n'

    # 3. Legacy 2-tuple passed as single argument
    ws3 = tmp_path / "ws3"
    ws3.mkdir()
    (ws3 / ".codex" / "agents").mkdir(parents=True)
    f3 = ws3 / ".codex" / "agents" / "bug-fixer.toml"
    f3.write_bytes(b'case3 = true\n')
    prep3 = dispatcher.prepare_workspace_agents(ws3)
    written3, created_dirs3 = prep3
    assert f3.read_bytes() != b'case3 = true\n'
    dispatcher.cleanup_workspace_agents((written3, created_dirs3))
    assert f3.read_bytes() == b'case3 = true\n'


# ==============================================================================
# Area 8: Nested Sequential Dispatch Integrity
# ==============================================================================

def test_nested_subagent_dispatch_exact_restoration(tmp_path):
    """Challenge: Sequential and nested dispatches in the same workspace restore exact original content."""
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target_file = codex_dir / "bug-fixer.toml"
    orig_bytes = b'name = "grandparent-custom-profile"\n'
    target_file.write_bytes(orig_bytes)
    orig_hash = hashlib.sha256(orig_bytes).hexdigest()

    mock_rt = MagicMock(spec=CodexRuntime)

    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    # First dispatch runs and during execution invokes a second dispatch
    def _inner_dispatch(**kwargs):
        # Nested dispatch call
        inner_prep = dispatcher.prepare_workspace_agents(tmp_path)
        try:
            assert target_file.exists()
        finally:
            dispatcher.cleanup_workspace_agents(inner_prep)
        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=0,
            file_changes=0,
            last_message="Nested OK",
            elapsed=0.01,
        )

    mock_rt.run.side_effect = _inner_dispatch
    dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Outer dispatch",
        workspace=tmp_path,
    )

    assert target_file.exists()
    assert target_file.read_bytes() == orig_bytes
    assert hashlib.sha256(target_file.read_bytes()).hexdigest() == orig_hash


# ==============================================================================
# Area 9: Export Failure Midway Rollback and Backup Preservation
# ==============================================================================

def test_export_failure_midway_rollback_restores_backup(tmp_path):
    """Challenge: If export_native_agents raises an exception midway,

    prepare_workspace_agents must restore backed-up files and re-raise.
    """
    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target_file = codex_dir / "bug-fixer.toml"
    orig_bytes = b'name = "safe-rollback-content"\n'
    target_file.write_bytes(orig_bytes)

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())

    with patch("mtm_agents.dispatcher.export_native_agents") as mock_export:
        mock_export.side_effect = OSError("Disk write I/O error during agent export")
        with pytest.raises(OSError, match="Disk write I/O error"):
            dispatcher.prepare_workspace_agents(tmp_path)

    # Post-failure verification: original file content is intact!
    assert target_file.exists()
    assert target_file.read_bytes() == orig_bytes


# ==============================================================================
# Area 10: Pre-existing Windows Exclusively Locked File Collision (Fail-Closed)
# ==============================================================================

def test_preexisting_windows_exclusive_locked_file_collision_fails_closed(tmp_path):
    """Challenge R1: If an existing agent config is locked exclusively by another process (share_mode=0),

    dispatch() immediately fails closed with PermissionError without writing or deleting any file.
    """
    if os.name != "nt":
        pytest.skip("Windows exclusive file locking requires NT")

    import ctypes
    kernel32 = ctypes.windll.kernel32

    codex_dir = tmp_path / ".codex" / "agents"
    codex_dir.mkdir(parents=True)
    target = codex_dir / "bug-fixer.toml"
    initial_bytes = b'# LOCKED EXCLUSIVE PRE-EXISTING CONFIG\n'
    target.write_bytes(initial_bytes)

    # Open target exclusively with dwShareMode=0
    # GENERIC_READ | GENERIC_WRITE = 0xC0000000, OPEN_EXISTING = 3, FILE_ATTRIBUTE_NORMAL = 0x80
    handle = kernel32.CreateFileW(
        str(target),
        0xC0000000,
        0,  # dwShareMode = 0 (exclusive lock)
        None,
        3,  # OPEN_EXISTING
        0x80,
        None,
    )
    assert handle != -1, "Failed to acquire exclusive lock handle"

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    try:
        with pytest.raises((PermissionError, OSError), match="Cannot backup pre-existing"):
            dispatcher.dispatch(
                envelope={"task_type": "BUG"},
                prompt="Locked collision test",
                workspace=tmp_path,
            )
    finally:
        kernel32.CloseHandle(handle)

    # Verify fail-closed: target file is unmodified, zero ephemeral files created
    assert target.exists()
    assert target.read_bytes() == initial_bytes
    remaining_files = list(codex_dir.glob("*.toml"))
    assert remaining_files == [target], f"Unexpected files created: {remaining_files}"


# ==============================================================================
# Area 11: Complex Symlink & Directory Junction Escapes
# ==============================================================================

def test_dispatcher_blocks_parent_agents_directory_junction_escape(tmp_path):
    """Challenge R2: If .agents is a Windows directory junction pointing outside workspace,

    dispatch() detects the escape immediately and aborts without writing outside.
    """
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_agents_vault"
    outside.mkdir()
    sensitive_file = outside / "sensitive.key"
    sensitive_file.write_text("SUPER_SECRET_KEY", encoding="utf-8")

    junction_path = ws / ".agents"
    _winapi.CreateJunction(str(outside), str(junction_path))

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Agents junction escape test",
            workspace=ws,
        )

    # Verification: sensitive file untouched and zero files written to outside
    assert list(outside.iterdir()) == [sensitive_file]
    assert sensitive_file.read_text(encoding="utf-8") == "SUPER_SECRET_KEY"


def test_dispatcher_blocks_relative_directory_symlink_escape(tmp_path):
    """Challenge R2: If .codex/agents is a relative directory symlink pointing outside workspace,

    dispatch() detects the escape and raises ValueError.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / ".codex").mkdir()
    outside = tmp_path / "outside_rel_vault"
    outside.mkdir()
    outside_marker = outside / "canary.txt"
    outside_marker.write_text("CANARY", encoding="utf-8")

    symlink_path = ws / ".codex" / "agents"
    # Relative target pointing 2 levels up into outside_rel_vault
    rel_target = os.path.join("..", "..", outside.name)
    try:
        os.symlink(rel_target, symlink_path, target_is_directory=True)
    except OSError:
        pytest.skip("Symlink creation not supported in this environment")

    dispatcher = NativeSubagentDispatcher(runtime=MagicMock())
    with pytest.raises((ValueError, PermissionError), match="outside workspace"):
        dispatcher.dispatch(
            envelope={"task_type": "BUG"},
            prompt="Relative symlink escape test",
            workspace=ws,
        )

    assert list(outside.iterdir()) == [outside_marker]
    assert outside_marker.read_text(encoding="utf-8") == "CANARY"


def test_cleanup_junction_preserves_external_directory_and_children(tmp_path):
    """Challenge R2: Ephemeral cleanup on a junction unlinks junction only, preserving external dir & files."""
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    ws = tmp_path / "workspace"
    ws.mkdir()
    outside = tmp_path / "outside_preserved_dir"
    outside.mkdir()
    for i in range(5):
        (outside / f"doc_{i}.txt").write_text(f"content {i}", encoding="utf-8")

    junction_path = ws / "ephemeral_junction"
    _winapi.CreateJunction(str(outside), str(junction_path))

    NativeSubagentDispatcher.cleanup_workspace_agents(
        written_files=[junction_path],
        created_dirs=[],
    )

    assert not junction_path.exists()
    assert outside.exists()
    assert len(list(outside.iterdir())) == 5
    for i in range(5):
        assert (outside / f"doc_{i}.txt").read_text(encoding="utf-8") == f"content {i}"


# ==============================================================================
# Area 13: Gen 3 Empirical Challenge — Anti-TOCTOU Symlink & Junction Substitution
# ==============================================================================

def test_challenger_anti_toctou_multi_target_adversarial_substitution(tmp_path):
    """Gen 3 Challenge R2: Empirically verify multi-target adversarial substitution defense.

    Simulates an agent during task execution replacing multiple backed-up files with:
    1. External symlink to a sensitive host file
    2. Windows directory junction (or directory symlink) to a sensitive external directory
    3. A broken symlink to a nonexistent target
    4. An in-place corrupted file

    Proves FileBackup.restore() unlinks links/junctions, never writes to external targets,
    and recreates original files with exact content, SHA-256 hash, and nanosecond timestamps.
    """
    ws = tmp_path / "workspace"
    ws.mkdir()
    codex_dir = ws / ".codex" / "agents"
    agy_dir = ws / ".agents" / "agents"
    codex_dir.mkdir(parents=True)
    agy_dir.mkdir(parents=True)

    # 1. Target 1: bug-fixer.toml
    f1 = codex_dir / "bug-fixer.toml"
    content1 = b'# CUSTOM BUG FIXER\nname = "bug-fixer"\nversion = "2.0.0"\n'
    f1.write_bytes(content1)
    hash1 = hashlib.sha256(content1).hexdigest()
    t1_ns = int((time.time() - 7200) * 1_000_000_000) // 100 * 100
    os.utime(f1, ns=(t1_ns, t1_ns))
    mtime1_ns = f1.stat().st_mtime_ns

    # 2. Target 2: feature-implementer.toml
    f2 = codex_dir / "feature-implementer.toml"
    content2 = b'# CUSTOM FEATURE IMPLEMENTER\nname = "feature-implementer"\n'
    f2.write_bytes(content2)
    hash2 = hashlib.sha256(content2).hexdigest()
    t2_ns = int((time.time() - 3600) * 1_000_000_000) // 100 * 100
    os.utime(f2, ns=(t2_ns, t2_ns))
    mtime2_ns = f2.stat().st_mtime_ns

    # 3. Target 3: general-task-worker.md
    f3 = agy_dir / "general-task-worker.md"
    content3 = b'# General Task Worker\nCustom profile instructions\n'
    f3.write_bytes(content3)
    hash3 = hashlib.sha256(content3).hexdigest()
    t3_ns = int((time.time() - 1800) * 1_000_000_000) // 100 * 100
    os.utime(f3, ns=(t3_ns, t3_ns))
    mtime3_ns = f3.stat().st_mtime_ns

    # 4. Target 4: incident-responder.md
    f4 = agy_dir / "incident-responder.md"
    content4 = b'# Incident Responder\nOriginal emergency procedures\n'
    f4.write_bytes(content4)
    hash4 = hashlib.sha256(content4).hexdigest()
    t4_ns = int((time.time() - 900) * 1_000_000_000) // 100 * 100
    os.utime(f4, ns=(t4_ns, t4_ns))
    mtime4_ns = f4.stat().st_mtime_ns

    # External sensitive targets
    outside = tmp_path / "outside_host"
    outside.mkdir()
    sensitive_file = outside / "secret_vault.key"
    sensitive_file_bytes = b"TOP_SECRET_MASTER_ENCRYPTION_KEY_DO_NOT_TOUCH"
    sensitive_file.write_bytes(sensitive_file_bytes)

    sensitive_vault_dir = outside / "confidential_dir"
    sensitive_vault_dir.mkdir()
    vault_canary = sensitive_vault_dir / "canary.log"
    vault_canary_bytes = b"CANARY_VAULT_RECORD"
    vault_canary.write_bytes(vault_canary_bytes)

    mock_rt = MagicMock(spec=CodexRuntime)

    def _hostile_agent_run(**kwargs):
        w = Path(kwargs["workspace"])
        c_dir = w / ".codex" / "agents"
        a_dir = w / ".agents" / "agents"

        # Attack 1: Replace bug-fixer.toml with symlink to outside sensitive file
        target1 = c_dir / "bug-fixer.toml"
        target1.unlink()
        try:
            os.symlink(sensitive_file, target1)
        except OSError:
            pytest.skip("Symlink creation not supported in this environment")

        # Attack 2: Replace feature-implementer.toml with junction to outside vault
        target2 = c_dir / "feature-implementer.toml"
        target2.unlink()
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(sensitive_vault_dir), str(target2))
        else:
            os.symlink(sensitive_vault_dir, target2, target_is_directory=True)

        # Attack 3: Replace general-task-worker.md with broken symlink
        target3 = a_dir / "general-task-worker.md"
        target3.unlink()
        os.symlink(outside / "nonexistent_file.txt", target3)

        # Attack 4: Overwrite incident-responder.md with adversarial payload
        target4 = a_dir / "incident-responder.md"
        target4.write_bytes(b"MALICIOUS_INJECTED_INSTRUCTIONS_ROOTKIT")

        return RuntimeResult(
            exit_code=0,
            timed_out=False,
            trace=ExecutionTrace(),
            usage={},
            commands=2,
            file_changes=4,
            last_message="Adversarial substitutions complete",
            elapsed=0.08,
        )

    mock_rt.run.side_effect = _hostile_agent_run
    dispatcher = NativeSubagentDispatcher(runtime=mock_rt)

    res = dispatcher.dispatch(
        envelope={"task_type": "BUG"},
        prompt="Adversarial multi-target substitution challenge",
        workspace=ws,
    )
    assert res.exit_code == 0

    # EMPIRICAL ORACLE 1: External files & directories were NEVER written to or modified
    assert sensitive_file.exists()
    assert sensitive_file.read_bytes() == sensitive_file_bytes
    assert sensitive_vault_dir.exists()
    assert vault_canary.exists()
    assert vault_canary.read_bytes() == vault_canary_bytes

    # EMPIRICAL ORACLE 2: None of the restored targets are symlinks or junctions
    for p in (f1, f2, f3, f4):
        assert p.exists(), f"Restored file {p} does not exist"
        assert p.is_file(), f"Restored file {p} is not a regular file"
        assert not p.is_symlink(), f"Restored file {p} is still a symlink"
        if hasattr(p, "is_junction"):
            assert not p.is_junction(), f"Restored file {p} is still a junction"

    # EMPIRICAL ORACLE 3: Exact binary content & SHA-256 restored
    assert f1.read_bytes() == content1
    assert hashlib.sha256(f1.read_bytes()).hexdigest() == hash1

    assert f2.read_bytes() == content2
    assert hashlib.sha256(f2.read_bytes()).hexdigest() == hash2

    assert f3.read_bytes() == content3
    assert hashlib.sha256(f3.read_bytes()).hexdigest() == hash3

    assert f4.read_bytes() == content4
    assert hashlib.sha256(f4.read_bytes()).hexdigest() == hash4

    # EMPIRICAL ORACLE 4: Nanosecond timestamps preserved accurately
    assert f1.stat().st_mtime_ns == mtime1_ns
    assert f2.stat().st_mtime_ns == mtime2_ns
    assert f3.stat().st_mtime_ns == mtime3_ns
    assert f4.stat().st_mtime_ns == mtime4_ns


def test_challenger_anti_toctou_parent_directory_junction_escape_blocked(tmp_path):
    """Gen 3 Challenge R2: If adversary replaces .codex/agents parent directory with an

    external directory junction before restore, FileBackup.restore() verifies destination
    parent containment and aborts without writing to the external target.
    """
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    ws = tmp_path / "workspace"
    ws.mkdir()
    agents_dir = ws / ".codex" / "agents"
    agents_dir.mkdir(parents=True)
    target = agents_dir / "bug-fixer.toml"
    orig_content = b'name = "legit-agent"\n'
    target.write_bytes(orig_content)

    backup = FileBackup.capture(target, workspace_root=ws)

    # External sensitive directory
    outside_dir = tmp_path / "outside_parent_vault"
    outside_dir.mkdir()
    canary = outside_dir / "bug-fixer.toml"
    canary.write_bytes(b"HOST_CANARY_DO_NOT_TOUCH")

    # Adversary replaces the parent directory (.codex/agents) with junction to outside_dir
    target.unlink()
    agents_dir.rmdir()
    _winapi.CreateJunction(str(outside_dir), str(agents_dir))

    # restore() must detect destination parent containment escape and fail closed
    with pytest.raises((ValueError, PermissionError), match="workspace"):
        backup.restore(workspace_root=ws)

    # Outside canary was untouched and zero files created in external target (Requirement R4 Test 2)
    assert canary.read_bytes() == b"HOST_CANARY_DO_NOT_TOUCH"
    assert [p.name for p in outside_dir.iterdir()] == ["bug-fixer.toml"]

    # Zero temp files leaked anywhere
    assert list(outside_dir.rglob(".restore_*")) == []
    assert list(ws.rglob(".restore_*")) == []


def test_r4_intermediate_ancestor_junction_swap_fail_closed_with_zero_leaks(tmp_path):
    """R4 Test 2: If adversary replaces an intermediate ancestor directory (e.g. .codex)

    with an external Windows directory junction before restore, FileBackup.restore()
    verifies intermediate ancestors top-down and aborts fail-closed, leaving zero files
    created in the external target and zero temp files leaked.
    """
    if os.name != "nt":
        pytest.skip("Windows junction test only applicable on NT")

    import _winapi
    import shutil
    ws = tmp_path / "workspace_ancestor"
    ws.mkdir()
    codex_dir = ws / ".codex"
    nested_dir = codex_dir / "deep" / "agents"
    nested_dir.mkdir(parents=True)
    target = nested_dir / "bug-fixer.toml"
    orig_content = b'name = "legit-agent-deep"\n'
    target.write_bytes(orig_content)

    backup = FileBackup.capture(target, workspace_root=ws)

    # External sensitive directory
    outside_dir = tmp_path / "outside_ancestor_vault"
    outside_dir.mkdir()
    canary = outside_dir / "canary.txt"
    canary.write_bytes(b"HOST_CANARY_STRICTLY_PROTECTED")

    # Adversary replaces intermediate grandparent directory (.codex) with junction to outside_dir
    shutil.rmtree(codex_dir)
    _winapi.CreateJunction(str(outside_dir), str(codex_dir))

    # restore() must fail closed on intermediate ancestor link
    with pytest.raises((ValueError, PermissionError), match="workspace"):
        backup.restore(workspace_root=ws)

    # Outside target contains ONLY the original canary file (zero files or directories created)
    assert [p.name for p in outside_dir.iterdir()] == ["canary.txt"]
    assert canary.read_bytes() == b"HOST_CANARY_STRICTLY_PROTECTED"

    # Zero temp files leaked anywhere
    assert list(outside_dir.rglob(".restore_*")) == []
    assert list(ws.rglob(".restore_*")) == []


def test_r4_intermediate_ancestor_symlink_swap_fail_closed_with_zero_leaks(tmp_path):
    """R4 Test 2: If adversary replaces an intermediate ancestor directory with an

    external symlink before restore, FileBackup.restore() aborts fail-closed,
    leaving zero files created in external targets and zero temp files leaked.
    """
    import shutil
    ws = tmp_path / "workspace_symlink"
    ws.mkdir()
    codex_dir = ws / ".codex"
    nested_dir = codex_dir / "agents"
    nested_dir.mkdir(parents=True)
    target = nested_dir / "feature-implementer.toml"
    orig_content = b'name = "feature-agent"\n'
    target.write_bytes(orig_content)

    backup = FileBackup.capture(target, workspace_root=ws)

    outside_dir = tmp_path / "outside_symlink_vault"
    outside_dir.mkdir()
    canary = outside_dir / "secret.key"
    canary.write_bytes(b"VERY_CONFIDENTIAL_KEY")

    # Adversary replaces .codex with external directory symlink
    shutil.rmtree(codex_dir)
    try:
        os.symlink(outside_dir, codex_dir, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks not supported in this environment")

    with pytest.raises((ValueError, PermissionError), match="workspace"):
        backup.restore(workspace_root=ws)

    # Outside target contains ONLY the original canary file
    assert [p.name for p in outside_dir.iterdir()] == ["secret.key"]
    assert canary.read_bytes() == b"VERY_CONFIDENTIAL_KEY"

    # Zero temp files leaked anywhere
    assert list(outside_dir.rglob(".restore_*")) == []
    assert list(ws.rglob(".restore_*")) == []


# ==============================================================================
# Area 14: Gen 3 Empirical Challenge — Process Termination & Zombie Detection
# ==============================================================================

def test_challenger_is_pid_alive_exhaustive_proc_status_matrix(tmp_path, monkeypatch):
    """Gen 3 Challenge R1: Empirically stress-test is_pid_alive with all Linux /proc status variations."""
    monkeypatch.setattr(sys, "platform", "linux")

    mock_proc_root = tmp_path / "proc"
    mock_proc_root.mkdir()

    def fake_path(*args):
        if args:
            p_str = str(args[0]).replace("\\", "/")
            if p_str == "/proc" or p_str.startswith("/proc/"):
                rel = p_str[len("/proc"):].lstrip("/")
                return mock_proc_root / rel if rel else mock_proc_root
        return Path(*args)

    monkeypatch.setattr("mtm_core.runtime.process.Path", fake_path)

    pid = 99999

    # Case 1: /proc/<pid> absent -> must return False immediately
    assert not is_pid_alive(pid)

    # Helper to setup mock status file
    pid_dir = mock_proc_root / str(pid)
    pid_dir.mkdir()
    status_file = pid_dir / "status"

    # Case 2: State variations that mean dead / zombie -> MUST return False
    zombie_variations = [
        "State:\tZ (zombie)",
        "State:\tZ",
        "State:\tz (zombie)",
        "State:\tz",
        "State:  Z (zombie)",
        "State: Z",
        "state: Z (zombie)",
        "State:\tX (dead)",
        "State:\tX",
        "State:\tx (dead)",
        "State:\tx",
        "State:  X",
        "state: x",
    ]

    for variant in zombie_variations:
        status_file.write_text(f"Name:\tpython\n{variant}\nTgid:\t{pid}\n", encoding="utf-8")
        assert not is_pid_alive(pid), f"Failed for variant: {variant}"

    # Case 3: State: S (sleeping) or R (running) with os.kill succeeding -> returns True
    monkeypatch.setattr(os, "kill", lambda p, sig: None)
    status_file.write_text("Name:\tpython\nState:\tS (sleeping)\n", encoding="utf-8")
    assert is_pid_alive(pid)

    status_file.write_text("Name:\tpython\nState:\tR (running)\n", encoding="utf-8")
    assert is_pid_alive(pid)

    # Case 4: State: S but os.kill raises ProcessLookupError -> returns False
    def _raise_lookup(p, sig):
        raise ProcessLookupError()
    monkeypatch.setattr(os, "kill", _raise_lookup)
    assert not is_pid_alive(pid)

    # Case 5: status file missing, falling back to stat file with zombie state
    status_file.unlink()
    stat_file = pid_dir / "stat"
    stat_file.write_text(f"{pid} (python) Z 1 1 1 0 -1", encoding="utf-8")
    assert not is_pid_alive(pid)

    stat_file.write_text(f"{pid} (python) X 1 1 1 0 -1", encoding="utf-8")
    assert not is_pid_alive(pid)


def test_challenger_terminate_process_tree_deep_hierarchy_grandchild_kill(tmp_path):
    """Gen 3 Challenge R1: Empirically verify terminate_process_tree kills child and grandchild trees without hang."""
    child_pid_file = tmp_path / "child.pid"
    grandchild_pid_file = tmp_path / "grandchild.pid"

    gc_file_posix = grandchild_pid_file.as_posix()
    c_file_posix = child_pid_file.as_posix()

    # Python script for child that spawns a grandchild and both wait
    script_content = f"""
import subprocess, sys, time, os
from pathlib import Path

grandchild_code = '''
import time, os, sys
from pathlib import Path
Path({repr(gc_file_posix)}).write_text(str(os.getpid()), encoding="utf-8")
while True:
    time.sleep(0.1)
'''
# Spawn grandchild
gc = subprocess.Popen([sys.executable, "-c", grandchild_code])
Path({repr(c_file_posix)}).write_text(str(os.getpid()), encoding="utf-8")
while True:
    time.sleep(0.1)
"""
    parent_proc = subprocess.Popen([sys.executable, "-c", script_content])

    # Wait for grandchild and child to write PIDs
    start_wait = time.time()
    while time.time() - start_wait < 10.0:
        if child_pid_file.exists() and grandchild_pid_file.exists():
            break
        time.sleep(0.1)

    assert child_pid_file.exists(), "Child PID file was not created"
    assert grandchild_pid_file.exists(), "Grandchild PID file was not created"

    c_pid = int(child_pid_file.read_text(encoding="utf-8").strip())
    gc_pid = int(grandchild_pid_file.read_text(encoding="utf-8").strip())

    # Both child and grandchild should be alive before termination
    assert is_pid_alive(c_pid)
    assert is_pid_alive(gc_pid)
    assert is_pid_alive(parent_proc.pid)

    # Terminate process tree
    terminate_process_tree(parent_proc)

    # Verify both child and grandchild are terminated within 5s
    quiesce_deadline = time.time() + 5.0
    while time.time() < quiesce_deadline:
        if not is_pid_alive(gc_pid) and not is_pid_alive(c_pid) and not is_pid_alive(parent_proc.pid):
            break
        time.sleep(0.1)

    assert not is_pid_alive(gc_pid), f"Grandchild PID {gc_pid} still alive after terminate_process_tree"
    assert not is_pid_alive(c_pid), f"Child PID {c_pid} still alive after terminate_process_tree"
    assert not is_pid_alive(parent_proc.pid), f"Parent PID {parent_proc.pid} still alive after terminate_process_tree"
    assert parent_proc.poll() is not None, "Parent process poll() returned None (did not terminate)"

