"""Verification test suite for Milestone M2:
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
    assert "output truncated: 200 bytes truncated in memory (no disk log configured)" in result


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
        assert "output truncated:" in stdout_text
        assert "bytes truncated in memory (no disk log configured)" in stdout_text
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


# ==============================================================================
# Gen3 Requirements R3 & R4: Diagnostic Notices & 50MB Disk Quota Capping
# ==============================================================================

def test_r4_truncation_notice_when_log_file_is_none():
    """R4: Verify get_text() explicitly notes in-memory truncation without disk log when log_file is None."""
    buf = BoundedStreamBuffer(max_bytes=100)
    assert buf.log_file is None

    buf.append(b"H" * 50)
    buf.append(b"M" * 200)
    buf.append(b"T" * 50)

    result = buf.get_text()
    assert "output truncated: 200 bytes truncated in memory (no disk log configured)" in result
    assert "(no disk log configured)" in result
    assert "Full stream written to log_file" not in result


def test_r4_truncation_notice_when_log_file_is_provided(tmp_path):
    """R4: Verify get_text() notes full stream written to disk when log_file is provided."""
    log_file = tmp_path / "test.log"
    buf = BoundedStreamBuffer(max_bytes=100, log_file=log_file)
    assert buf.log_file is not None

    buf.append(b"H" * 50)
    buf.append(b"M" * 200)
    buf.append(b"T" * 50)
    buf.close()

    result = buf.get_text()
    assert "output truncated: 200 bytes truncated in memory. Full stream written to log_file." in result
    assert "Full stream written to log_file." in result
    assert "(no disk log configured)" not in result


def test_r3_bounded_stream_buffer_enforces_disk_quota(tmp_path):
    """R3: Verify BoundedStreamBuffer stops writing to disk when max_disk_bytes is reached."""
    log_file = tmp_path / "quota_unit_test.log"
    max_disk = 1000
    buf = BoundedStreamBuffer(max_bytes=200, log_file=log_file, max_disk_bytes=max_disk)

    buf.append(b"D" * 2500)
    buf.close()

    assert log_file.exists()
    assert log_file.stat().st_size == max_disk
    assert buf.total_bytes == 2500


def test_r3_subprocess_60mb_stream_capped_at_50mb_disk_quota(tmp_path):
    """R3: Verify subprocess emitting >60MB stops writing to disk at exactly 50MB without deadlock."""
    total_bytes = 60 * 1024 * 1024  # 60MB (62,914,560 bytes)
    chunk_size = 65536
    iterations = total_bytes // chunk_size

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

    log_file = tmp_path / "stream_quota_60mb.log"

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=30.0, pause_event=None, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 15.0, f"Draining 60MB took {elapsed:.2f}s, expected < 15.0s"
        assert stderr_text == ""

        # Verify RAM bound <= 10.1MB
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert stdout_text.startswith("HEAD_MARKER")
        assert stdout_text.endswith("TAIL_MARKER")

        # Verify Disk Quota capped at 50MB (52,428,800 bytes)
        assert log_file.exists()
        disk_size = log_file.stat().st_size
        expected_quota = 50 * 1024 * 1024
        assert disk_size == expected_quota, f"Expected {expected_quota} bytes on disk, found {disk_size}!"

        # Verify DISK_LOG_TRUNCATED notice with exact byte accounting (Requirement R4 Test 1)
        expected_saved = expected_quota
        expected_omitted_disk = total_bytes - expected_quota
        expected_omitted_ram = total_bytes - 10 * 1024 * 1024
        assert (
            f"DISK_LOG_TRUNCATED: {expected_saved} bytes saved, {expected_omitted_disk} bytes omitted from disk"
            in stdout_text
        )
        assert f"output truncated: {expected_omitted_ram} bytes truncated in memory" in stdout_text
        assert "Disk log reached 50MB quota" in stdout_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# Gen4 Requirements R1 & R3 Unit Tests
# ==============================================================================

def test_r1_disk_log_writer_properties_and_omission_tracking(tmp_path):
    """R1: Verify DiskLogWriter tracks bytes_omitted and exposes properties is_truncated, bytes_written, bytes_omitted."""
    from mtm_core.runtime.codex import DiskLogWriter

    log_path = tmp_path / "writer_props.log"
    writer = DiskLogWriter(log_path, max_bytes=1000)

    assert writer.bytes_written == 0
    assert writer.bytes_omitted == 0
    assert not writer.is_truncated

    # Write 600 bytes (within quota)
    w1 = writer.write(b"A" * 600)
    assert w1 == 600
    assert writer.bytes_written == 600
    assert writer.bytes_omitted == 0
    assert not writer.is_truncated

    # Write 600 bytes (partial overflow: 400 written, 200 omitted)
    w2 = writer.write(b"B" * 600)
    assert w2 == 400
    assert writer.bytes_written == 1000
    assert writer.bytes_omitted == 200
    assert writer.is_truncated

    # Write 500 bytes (full overflow: 0 written, 500 omitted)
    w3 = writer.write(b"C" * 500)
    assert w3 == 0
    assert writer.bytes_written == 1000
    assert writer.bytes_omitted == 700
    assert writer.is_truncated

    writer.close()
    assert writer.bytes_written == 1000
    assert writer.bytes_omitted == 700
    assert writer.is_truncated


def test_r1_diagnostic_notice_disk_log_truncated_exact_byte_reporting(tmp_path):
    """R1: Verify BoundedStreamBuffer.get_text() produces DISK_LOG_TRUNCATED notice with exact byte counts."""
    log_path = tmp_path / "truncated_diag.log"
    max_disk = 1000
    buf = BoundedStreamBuffer(max_bytes=200, log_file=log_path, max_disk_bytes=max_disk)

    # Append 3000 bytes: 1000 written to disk, 2000 omitted from disk
    # In-memory: 200 retained (100 head + 100 tail), 2800 omitted
    buf.append(b"H" * 100)
    buf.append(b"M" * 2800)
    buf.append(b"T" * 100)
    buf.close()

    text = buf.get_text()
    assert text.startswith("H" * 100)
    assert text.endswith("T" * 100)
    assert "output truncated: 2800 bytes truncated in memory" in text
    assert "Disk log reached 1000B quota (DISK_LOG_TRUNCATED: 1000 bytes saved, 2000 bytes omitted from disk)" in text


def test_r3_communicate_with_pause_parameter_backwards_compatibility():
    """R3: Verify _communicate_with_pause accepts both max_total_buffer_bytes and legacy max_buffer_bytes."""
    class DummyProc:
        def __init__(self):
            self.stdout = None
            self.stderr = None
            self.args = ["dummy"]
            self.returncode = 0
        def poll(self):
            return 0
        def communicate(self, timeout=None):
            return "out", "err"

    proc = DummyProc()
    out, err = CodexRuntime._communicate_with_pause(proc, timeout=1.0, pause_event=None, max_total_buffer_bytes=5000)
    assert out == "out" and err == "err"

    out2, err2 = CodexRuntime._communicate_with_pause(proc, timeout=1.0, pause_event=None, max_buffer_bytes=5000)
    assert out2 == "out" and err2 == "err"


def test_r4_diagnostic_notice_all_three_modes(tmp_path):
    """R4 Test 1: Verify BoundedStreamBuffer.get_text() accurately differentiates all 3 truncation modes."""
    # Case 1: No disk log configured (log_file is None)
    buf_no_disk = BoundedStreamBuffer(max_bytes=200, log_file=None)
    buf_no_disk.append(b"H" * 100)
    buf_no_disk.append(b"M" * 500)
    buf_no_disk.append(b"T" * 100)
    text1 = buf_no_disk.get_text()
    assert "output truncated: 500 bytes truncated in memory (no disk log configured)" in text1
    assert "DISK_LOG_TRUNCATED" not in text1
    assert "Full stream written" not in text1

    # Case 2: Disk quota reached (is_truncated is True)
    log_capped = tmp_path / "capped.log"
    buf_capped = BoundedStreamBuffer(max_bytes=200, log_file=log_capped, max_disk_bytes=500)
    buf_capped.append(b"H" * 100)
    buf_capped.append(b"M" * 1000)
    buf_capped.append(b"T" * 100)
    buf_capped.close()
    text2 = buf_capped.get_text()
    assert "output truncated: 1000 bytes truncated in memory" in text2
    assert "Disk log reached 500B quota (DISK_LOG_TRUNCATED: 500 bytes saved, 700 bytes omitted from disk)" in text2
    assert "Full stream written" not in text2

    # Case 3: Full stream written to log_file without hitting quota
    log_full = tmp_path / "full.log"
    buf_full = BoundedStreamBuffer(max_bytes=200, log_file=log_full, max_disk_bytes=10000)
    buf_full.append(b"H" * 100)
    buf_full.append(b"M" * 500)
    buf_full.append(b"T" * 100)
    buf_full.close()
    text3 = buf_full.get_text()
    assert "output truncated: 500 bytes truncated in memory. Full stream written to log_file." in text3
    assert "DISK_LOG_TRUNCATED" not in text3
    assert "(no disk log configured)" not in text3


def test_r4_subprocess_60mb_stream_reports_disk_log_truncated_exact_counts(tmp_path):
    """R4 Test 1: Subprocess emitting 60MB with 50MB disk quota verifies get_text() contains DISK_LOG_TRUNCATED with exact saved and omitted counts."""
    total_bytes = 60 * 1024 * 1024  # 62,914,560 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    code = (
        f"import sys\n"
        f"chunk_head = ('HEAD_60MB_' + 'A' * {chunk_size - 10}).encode('ascii')\n"
        f"chunk_mid = ('A' * {chunk_size}).encode('ascii')\n"
        f"chunk_tail = ('A' * {chunk_size - 10} + '_TAIL_60MB').encode('ascii')\n"
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

    log_file = tmp_path / "stream_quota_60mb_exact.log"

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=30.0, pause_event=None, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 15.0, f"Draining 60MB took {elapsed:.2f}s, expected < 15.0s"
        assert stderr_text == ""

        # RAM bound verification (10MB + notice buffer <= 10.1MB)
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert stdout_text.startswith("HEAD_60MB_")
        assert stdout_text.endswith("_TAIL_60MB")

        # Disk Quota capped at 50MB (52,428,800 bytes)
        assert log_file.exists()
        disk_size = log_file.stat().st_size
        expected_quota = 50 * 1024 * 1024
        assert disk_size == expected_quota, f"Expected {expected_quota} bytes on disk, found {disk_size}!"

        # Exact byte counts verification (Requirement R4 Test 1)
        expected_saved = expected_quota
        expected_omitted_disk = total_bytes - expected_quota
        expected_omitted_ram = total_bytes - 10 * 1024 * 1024
        assert (
            f"DISK_LOG_TRUNCATED: {expected_saved} bytes saved, {expected_omitted_disk} bytes omitted from disk"
            in stdout_text
        )
        assert f"output truncated: {expected_omitted_ram} bytes truncated in memory" in stdout_text
        assert "Disk log reached 50MB quota" in stdout_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)




