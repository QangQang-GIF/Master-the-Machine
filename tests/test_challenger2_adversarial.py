"""Empirical Adversarial Test Suite for NativeSubagentDispatcher & build_agy_cmd.

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

from mtm_agents.dispatcher import (
    NativeSubagentDispatcher,
    WorkspacePreparation,
    WrittenFiles,
    FileBackup,
)
from mtm_core.runtime import CodexRuntime, RuntimeResult, ExecutionTrace


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
# Area 4: Decoupling Stress & Conflicting Parameters
# ==============================================================================

def test_decoupling_conflicting_parameters_and_env_vars(monkeypatch):
    """Challenge: Test build_agy_cmd under adversarial combinations:

    - Conflicting dangerously_skip_permissions=False vs MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS='1'
    - Conflicting dangerously_skip_permissions=True vs MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS='0'
    - Conflicting dangerously_skip_permissions vs skip_permissions
    - Env vars with mixed case, whitespace, boolean representations
    - Non-standard sandbox_policy casing and whitespace
    - Empty strings for prompt, sandbox_policy, agent, model, effort
    """
    rt = CodexRuntime()

    # 1. Parameter dangerously_skip_permissions=False OVERRIDES env var '1'
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    cmd = rt.build_agy_cmd("Test prompt", sandbox_policy="workspace-write", dangerously_skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd, "Argument dangerously_skip_permissions=False must override env var 1"

    cmd_sb = rt.build_agy_cmd("Test prompt", sandbox_policy="sandbox", dangerously_skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd_sb

    # 2. Parameter dangerously_skip_permissions=True OVERRIDES env var '0'
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "0")
    cmd_true_wins = rt.build_agy_cmd("Test prompt", sandbox_policy="sandbox", dangerously_skip_permissions=True)
    assert "--dangerously-skip-permissions" in cmd_true_wins, "Argument dangerously_skip_permissions=True must override env var 0"

    # 3. Parameter skip_permissions=False OVERRIDES env var '1'
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    cmd_alias_false = rt.build_agy_cmd("Test prompt", sandbox_policy="workspace-write", skip_permissions=False)
    assert "--dangerously-skip-permissions" not in cmd_alias_false

    # 4. Parameter skip_permissions=True OVERRIDES env var '0'
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "0")
    cmd_alias_true = rt.build_agy_cmd("Test prompt", sandbox_policy="sandbox", skip_permissions=True)
    assert "--dangerously-skip-permissions" in cmd_alias_true

    # 5. Conflicting parameters: dangerously_skip_permissions takes precedence over skip_permissions
    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)
    cmd_conflict_1 = rt.build_agy_cmd("Test", dangerously_skip_permissions=False, skip_permissions=True)
    assert "--dangerously-skip-permissions" not in cmd_conflict_1, "dangerously_skip_permissions must take precedence"

    cmd_conflict_2 = rt.build_agy_cmd("Test", sandbox_policy="sandbox", dangerously_skip_permissions=True, skip_permissions=False)
    assert "--dangerously-skip-permissions" in cmd_conflict_2, "dangerously_skip_permissions must take precedence"

    # 6. Env var truthy representations with whitespace & mixed casing
    for truthy_val in ("  1  ", "tRuE", "  TrUe  ", "YES", "  yEs  "):
        monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", truthy_val)
        cmd_truthy = rt.build_agy_cmd("Test", sandbox_policy="sandbox")
        assert "--dangerously-skip-permissions" in cmd_truthy, f"Truthy env var '{truthy_val}' failed"

    # 7. Env var falsy representations explicitly disabling bypass in unrestricted mode
    for falsy_val in ("  0  ", "fAlSe", "  FaLsE  ", "NO", "  nO  ", "unknown"):
        monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", falsy_val)
        cmd_falsy = rt.build_agy_cmd("Test", sandbox_policy="workspace-write")
        assert "--dangerously-skip-permissions" not in cmd_falsy, f"Falsy env var '{falsy_val}' failed"

    # 8. Empty string env var treats var as unset (falling back to mode default)
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "")
    cmd_empty_env_unres = rt.build_agy_cmd("Test", sandbox_policy="workspace-write")
    assert "--dangerously-skip-permissions" in cmd_empty_env_unres  # Unrestricted defaults to bypass

    cmd_empty_env_sandboxed = rt.build_agy_cmd("Test", sandbox_policy="sandbox")
    assert "--dangerously-skip-permissions" not in cmd_empty_env_sandboxed  # Sandboxed defaults to NO bypass

    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)

    # 9. Non-standard casing & leading/trailing whitespace for sandbox_policy
    for policy_str in ("  ReAd-OnLy  ", "READ-ONLY", "\tread-only\n"):
        cmd_ro = rt.build_agy_cmd("Test", sandbox_policy=policy_str)
        assert "--sandbox" in cmd_ro
        assert "plan" in cmd_ro
        assert "--dangerously-skip-permissions" not in cmd_ro

    for policy_str in ("  SaNdBoX  ", "SANDBOX", "  restricted  ", "WORKSPACE-SANDBOX"):
        cmd_sb_stress = rt.build_agy_cmd("Test", sandbox_policy=policy_str)
        assert "--sandbox" in cmd_sb_stress
        assert "accept-edits" in cmd_sb_stress
        assert "--dangerously-skip-permissions" not in cmd_sb_stress

    # 10. Empty strings & whitespace-only strings
    cmd_empty_policy = rt.build_agy_cmd("Test", sandbox_policy="")
    assert "--sandbox" not in cmd_empty_policy
    assert "--dangerously-skip-permissions" in cmd_empty_policy  # Unrestricted default

    cmd_whitespace_policy = rt.build_agy_cmd("Test", sandbox_policy="   \t  ")
    assert "--sandbox" not in cmd_whitespace_policy
    assert "--dangerously-skip-permissions" in cmd_whitespace_policy

    cmd_empty_agent = rt.build_agy_cmd("Test", agent="", model="", effort="")
    assert "--agent" not in cmd_empty_agent
    assert "--model" not in cmd_empty_agent
    assert "--effort" not in cmd_empty_agent

    cmd_empty_prompt = rt.build_agy_cmd("")
    assert "-p" in cmd_empty_prompt
    assert cmd_empty_prompt[cmd_empty_prompt.index("-p") + 1] == ""


# ==============================================================================
# Area 5: MTM_AGY_SANDBOX_POLICY Env Var Precedence & Decoupling
# ==============================================================================

def test_agy_sandbox_policy_env_var_handling(monkeypatch):
    """Challenge: MTM_AGY_SANDBOX_POLICY env var must be parsed with casing/whitespace resilience."""
    rt = CodexRuntime()

    monkeypatch.setenv("MTM_AGY_SANDBOX_POLICY", "  ReAd-OnLy  ")
    cmd = rt.build_agy_cmd("Test audit")
    assert "--sandbox" in cmd
    assert "plan" in cmd
    assert "--dangerously-skip-permissions" not in cmd

    # Explicit argument overrides env var
    cmd_arg_wins = rt.build_agy_cmd("Test write", sandbox_policy="workspace-write")
    assert "--sandbox" not in cmd_arg_wins
    assert "--dangerously-skip-permissions" in cmd_arg_wins


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
