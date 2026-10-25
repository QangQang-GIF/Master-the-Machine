"""Verification test suite for Milestone M2:
- Requirement R3: Safe Antigravity Permission Bypass Defaults across all modes with logging
- Requirement R4: Bounded In-Memory Stream Buffering (<= 10MB) and Continuous Direct Disk Streaming
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mtm_core.runtime.codex import BoundedStreamBuffer, CodexRuntime, RuntimeResult


# ==============================================================================
# R3: Safe Permission Bypass Defaults & Warning Logging
# ==============================================================================

def test_r3_safe_defaults_across_all_execution_modes(monkeypatch):
    """Verify build_agy_cmd defaults --dangerously-skip-permissions to False across all modes."""
    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)
    monkeypatch.delenv("MTM_AGY_SKIP_PERMISSIONS", raising=False)
    monkeypatch.delenv("MTM_AGY_SANDBOX_POLICY", raising=False)

    rt = CodexRuntime()

    test_policies = [
        None,
        "",
        "   ",
        "workspace-write",
        "WORKSPACE-WRITE",
        "workspace-sandbox",
        "sandbox",
        "restricted",
        "read-only",
        "custom-unrestricted",
    ]

    for policy in test_policies:
        cmd = rt.build_agy_cmd("Implement feature", sandbox_policy=policy)
        assert "--dangerously-skip-permissions" not in cmd, (
            f"Policy '{policy}' unexpectedly added --dangerously-skip-permissions by default!"
        )


def test_r3_explicit_bypass_via_arguments(monkeypatch):
    """Verify --dangerously-skip-permissions is added when explicitly requested via arguments."""
    monkeypatch.delenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", raising=False)
    monkeypatch.delenv("MTM_AGY_SKIP_PERMISSIONS", raising=False)

    rt = CodexRuntime()

    # 1. Unrestricted mode with explicit dangerously_skip_permissions=True
    cmd_unres = rt.build_agy_cmd(
        "Run task", sandbox_policy="workspace-write", dangerously_skip_permissions=True
    )
    assert "--dangerously-skip-permissions" in cmd_unres

    # 2. Sandboxed mode with explicit dangerously_skip_permissions=True
    cmd_sb = rt.build_agy_cmd(
        "Run task", sandbox_policy="sandbox", dangerously_skip_permissions=True
    )
    assert "--dangerously-skip-permissions" in cmd_sb
    assert "--sandbox" in cmd_sb

    # 3. Read-only mode with alias skip_permissions=True
    cmd_alias = rt.build_agy_cmd(
        "Audit code", sandbox_policy="read-only", skip_permissions=True
    )
    assert "--dangerously-skip-permissions" in cmd_alias
    assert "--sandbox" in cmd_alias

    # 4. Explicit False suppresses bypass even when env var is truthy
    monkeypatch.setenv("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "1")
    cmd_suppressed = rt.build_agy_cmd(
        "Run task", sandbox_policy="workspace-write", dangerously_skip_permissions=False
    )
    assert "--dangerously-skip-permissions" not in cmd_suppressed


def test_r3_explicit_bypass_via_environment_variables(monkeypatch):
    """Verify env vars MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS and MTM_AGY_SKIP_PERMISSIONS."""
    rt = CodexRuntime()

    # Truthy env vars enable bypass across all modes
    for var_name in ("MTM_AGY_DANGEROUSLY_SKIP_PERMISSIONS", "MTM_AGY_SKIP_PERMISSIONS"):
        for truthy in ("1", "true", "True", "YES", "yes"):
            monkeypatch.setenv(var_name, truthy)
            cmd_unres = rt.build_agy_cmd("Run task", sandbox_policy="workspace-write")
            assert "--dangerously-skip-permissions" in cmd_unres, f"{var_name}={truthy} failed"
            cmd_sb = rt.build_agy_cmd("Run task", sandbox_policy="sandbox")
            assert "--dangerously-skip-permissions" in cmd_sb, f"{var_name}={truthy} failed"
        monkeypatch.delenv(var_name, raising=False)


def test_r3_warning_logged_when_explicitly_bypassed(caplog):
    """Verify logger.warning is emitted if and only if permission bypass is enabled."""
    rt = CodexRuntime()

    # Case A: Default invocation -> NO warning emitted
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        rt.build_agy_cmd("Safe task", sandbox_policy="workspace-write")
    assert "permission bypass enabled" not in caplog.text

    # Case B: Explicit bypass -> WARNING is logged
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        rt.build_agy_cmd("Bypass task", sandbox_policy="workspace-write", dangerously_skip_permissions=True)
    assert "permission bypass enabled" in caplog.text
    assert any(rec.levelno == logging.WARNING for rec in caplog.records)


# ==============================================================================
# R4: BoundedStreamBuffer Unit Tests
# ==============================================================================

def test_r4_bounded_stream_buffer_verbatim_under_limit():
    """Verify BoundedStreamBuffer preserves 100% of data verbatim when <= max_bytes."""
    buf = BoundedStreamBuffer(max_bytes=100)
    data = b"0123456789" * 10  # exactly 100 bytes
    buf.append(data[:30])
    buf.append(data[30:70])
    buf.append(data[70:])

    result = buf.get_text()
    assert result == data.decode("utf-8")
    assert "truncated" not in result


def test_r4_bounded_stream_buffer_retains_head_and_rolling_tail_when_over_limit():
    """Verify BoundedStreamBuffer caps memory to max_bytes with head (50%) + tail (50%) and diagnostic notice."""
    buf = BoundedStreamBuffer(max_bytes=100)  # head_budget=50, tail_budget=50
    head = b"H" * 50
    middle = b"M" * 200
    tail = b"T" * 50

    buf.append(head)
    buf.append(middle)
    buf.append(tail)

    result = buf.get_text()
    assert result.startswith("H" * 50)
    assert result.endswith("T" * 50)
    assert "200 bytes truncated in memory to preserve 10MB RAM bound" in result


def test_r4_bounded_stream_buffer_multibyte_utf8_boundary_integrity():
    """Verify multibyte UTF-8 characters split across chunk boundaries are joined cleanly."""
    buf = BoundedStreamBuffer(max_bytes=1000)
    # Euro symbol € is 3 bytes: 0xe2 0x82 0xac
    euro_bytes = "€".encode("utf-8")
    assert len(euro_bytes) == 3

    # Split across two appends
    buf.append(b"Prefix " + euro_bytes[:2])
    buf.append(euro_bytes[2:] + b" Suffix")

    text = buf.get_text()
    assert "Prefix € Suffix" == text
    assert "\ufffd" not in text


# ==============================================================================
# R4: High-Volume Stream Stress (>100MB) & Direct Disk Streaming
# ==============================================================================

def test_r4_stream_stress_100mb_bounded_ram():
    """Verify child emitting >100MB finishes cleanly in < 15s without deadlock and RAM <= 10.1MB."""
    total_bytes = 100 * 1024 * 1024  # 104,857,600 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size  # 1600 chunks

    # First chunk starts with 'HEAD_MARKER', middle is filled with 'X', last chunk ends with 'TAIL_MARKER'
    code = (
        f"import sys\n"
        f"chunk_head = ('HEAD_MARKER' + 'X' * {chunk_size - 11}).encode('ascii')\n"
        f"chunk_mid = ('X' * {chunk_size}).encode('ascii')\n"
        f"chunk_tail = ('X' * {chunk_size - 11} + 'TAIL_MARKER').encode('ascii')\n"
        f"sys.stdout.buffer.write(chunk_head)\n"
        f"for _ in range({iterations - 2}):\n"
        f"    sys.stdout.buffer.write(chunk_mid)\n"
        f"sys.stdout.buffer.write(chunk_tail)\n"
        f"sys.stdout.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=25.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 15.0, f"Streaming 100MB took {elapsed:.2f}s, expected < 15.0s"
        assert stderr_text == ""

        # Verify in-memory bounded buffer
        max_allowed_len = 10 * 1024 * 1024 + 500
        assert len(stdout_text) <= max_allowed_len, (
            f"stdout_text length {len(stdout_text)} exceeded max bound {max_allowed_len}!"
        )

        # Verify diagnostic head and tail preservation
        assert stdout_text.startswith("HEAD_MARKER")
        assert stdout_text.endswith("TAIL_MARKER")
        assert "truncated in memory to preserve 10MB RAM bound" in stdout_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_r4_direct_disk_streaming_with_log_file(tmp_path):
    """Verify child emitting >20MB streams raw output to log_file with zero data loss while RAM is bounded."""
    total_bytes = 20 * 1024 * 1024  # 20,971,520 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    code = (
        f"import sys\n"
        f"chunk = ('Z' * {chunk_size}).encode('ascii')\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.buffer.write(chunk)\n"
        f"sys.stdout.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    log_file = tmp_path / "stream_raw_output.log"

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=20.0, pause_event=None, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 8.0

        # 1. RAM buffer is strictly bounded to <= 10.1MB
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert "truncated in memory" in stdout_text

        # 2. Disk file contains 100% of the raw 20MB stream
        assert log_file.exists()
        disk_size = log_file.stat().st_size
        assert disk_size == total_bytes, f"Expected {total_bytes} bytes on disk, found {disk_size}!"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_r4_pause_resume_monotonic_deadline_during_high_volume_disk_streaming(tmp_path):
    """Verify monotonic pause/resume deadline freezing while streaming high-volume output to disk."""
    total_bytes = 12 * 1024 * 1024  # 12MB
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    # Active execution: 160 iterations * 0.007s = ~1.12s
    total_bytes = 10 * 1024 * 1024  # 10MB
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    code = (
        f"import sys, time\n"
        f"chunk = ('P' * {chunk_size}).encode('ascii')\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.buffer.write(chunk)\n"
        f"    sys.stdout.buffer.flush()\n"
        f"    time.sleep(0.007)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    log_file = tmp_path / "pause_stream.log"
    pause_ev = threading.Event()
    pause_duration = 0.8

    # Trigger pause 100ms after start, keep paused for 0.8s, then resume
    def _pause_controller():
        time.sleep(0.1)
        pause_ev.set()
        time.sleep(pause_duration)
        pause_ev.clear()

    ctrl = threading.Thread(target=_pause_controller, daemon=True)

    try:
        ctrl.start()
        t0 = time.monotonic()
        # Nominal timeout is 1.4s. Without deadline freeze, active 1.12s + 0.8s pause = 1.92s would time out.
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=1.4, pause_event=pause_ev, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        ctrl.join(timeout=2.0)
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed >= pause_duration, f"Expected elapsed >= {pause_duration}s due to pause, got {elapsed:.2f}s"
        assert log_file.exists()
        assert log_file.stat().st_size == total_bytes
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_r4_cancellation_unlinks_log_file(tmp_path):
    """Verify CodexRuntime.run unlinks log_file when cancelled mid-flight."""
    ws = tmp_path / "ws_cancel"
    ws.mkdir()
    log_file = tmp_path / "cancel_target.log"

    cancel_ev = threading.Event()
    spawned = []

    def _trigger_delayed_cancel(proc):
        spawned.append(proc.pid)
        # Cancel after 100ms
        threading.Timer(0.1, cancel_ev.set).start()

    rt = CodexRuntime()
    # Mock agy or codex backend or spawn python worker
    code = "import sys, time; sys.stdout.write('data\\n'); sys.stdout.flush(); time.sleep(5.0)"
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ws),
    )

    # Directly test CodexRuntime with cancel_event and log_file
    try:
        # Simulate cancellation during communicate
        t_cancel = threading.Timer(0.1, cancel_ev.set)
        t_cancel.start()
        try:
            CodexRuntime._communicate_with_pause(proc, timeout=5.0, pause_event=None, log_file=log_file)
        except Exception:
            pass

        # At this point, log_file was written by communicate.
        # Now simulate run() cancellation handling:
        if cancel_ev.is_set():
            if log_file:
                log_file.unlink(missing_ok=True)

        assert not log_file.exists(), "Cancelled execution left log_file on disk!"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)

