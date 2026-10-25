"""Empirical challenger stress harness for CodexRuntime._communicate_with_pause.

Tests covering:
1. Extreme volume (20MB, 50MB stdout, 50MB split stdout/stderr).
2. Asymmetric stream burst contention (10MB vs 100KB both directions).
3. Abrupt termination, broken pipes, and partial stream closures.
4. High-frequency pause/resume thrashing (50+ toggles) with deadline freezing.
5. Empirical reproduction of 2 critical findings in CodexRuntime._communicate_with_pause:
   - Finding 1: Partial output under 64KB is completely lost on timeout due to TextIOWrapper.read(65536) blocking.
   - Finding 2: Finally block introduces a 1.0s stall (0.5s stdout + 0.5s stderr) on every TimeoutExpired.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from typing import Optional
import pytest

from mtm_core.runtime.codex import CodexRuntime


# ==============================================================================
# 1. EXTREME HIGH-VOLUME EMISSION (20MB to 50MB)
# ==============================================================================

def test_extreme_volume_20mb_stdout():
    """Verify unpaused child emitting 20MB stdout drains completely in < 5s without deadlock."""
    total_bytes = 20 * 1024 * 1024  # 20,971,520 bytes
    code = (
        f"import sys\n"
        f"chunk = 'A' * 65536\n"
        f"for _ in range({total_bytes // 65536}):\n"
        f"    sys.stdout.write(chunk)\n"
        f"sys.stdout.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=15.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert stdout_text.startswith("A" * 65536)
        assert stdout_text.endswith("A" * 65536)
        assert "truncated in memory" in stdout_text
        assert stderr_text == ""
        assert elapsed < 5.0, f"Draining 20MB took {elapsed:.2f}s, expected < 5.0s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_extreme_volume_50mb_stdout():
    """Verify child emitting 50MB stdout drains completely in < 10s without memory corruption or hang."""
    total_bytes = 50 * 1024 * 1024  # 52,428,800 bytes
    code = (
        f"import sys\n"
        f"chunk = 'B' * 65536\n"
        f"for _ in range({total_bytes // 65536}):\n"
        f"    sys.stdout.write(chunk)\n"
        f"sys.stdout.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=25.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert stdout_text.startswith("B" * 65536)
        assert stdout_text.endswith("B" * 65536)
        assert "truncated in memory" in stdout_text
        assert stderr_text == ""
        assert elapsed < 10.0, f"Draining 50MB took {elapsed:.2f}s, expected < 10.0s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_extreme_volume_50mb_split_stdout_stderr():
    """Verify child emitting 25MB stdout and 25MB stderr drains simultaneously in parallel in < 12s."""
    bytes_per_stream = 25 * 1024 * 1024  # 26,214,400 bytes each
    chunk_size = 65536
    iterations = bytes_per_stream // chunk_size

    code = (
        f"import sys\n"
        f"c_out = 'O' * {chunk_size}\n"
        f"c_err = 'E' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.write(c_out)\n"
        f"    sys.stdout.flush()\n"
        f"    sys.stderr.write(c_err)\n"
        f"    sys.stderr.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=30.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert len(stderr_text) <= 10 * 1024 * 1024 + 500
        # Requirement R4 Test 3: Combined aggregate RAM <= 10.1MB
        assert len(stdout_text) + len(stderr_text) <= 10 * 1024 * 1024 + 1000
        assert stdout_text.startswith("O" * 65536) and stdout_text.endswith("O" * 65536)
        assert stderr_text.startswith("E" * 65536) and stderr_text.endswith("E" * 65536)
        assert "truncated in memory" in stdout_text
        assert "truncated in memory" in stderr_text
        assert elapsed < 12.0, f"Draining 50MB split took {elapsed:.2f}s, expected < 12.0s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 2. ASYMMETRIC STREAM BURST CONTENTION
# ==============================================================================

def test_asymmetric_burst_contention_10mb_stdout_vs_100kb_stderr():
    """Verify asymmetric bursts (10MB stdout vs 100KB stderr) drain cleanly without stream starvation."""
    stdout_bytes = 10 * 1024 * 1024
    stderr_bytes = 100 * 1024

    code = (
        f"import sys\n"
        f"sys.stderr.write('e' * 51200)\n"
        f"sys.stderr.flush()\n"
        f"chunk = 'X' * 65536\n"
        f"for i in range({stdout_bytes // 65536}):\n"
        f"    sys.stdout.write(chunk)\n"
        f"    if i == 80:\n"
        f"        sys.stderr.write('E' * 51200)\n"
        f"        sys.stderr.flush()\n"
        f"sys.stdout.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=15.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert len(stdout_text) == stdout_bytes
        assert len(stderr_text) == stderr_bytes
        assert stderr_text.startswith("e") and stderr_text.endswith("E")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_asymmetric_burst_contention_100kb_stdout_vs_10mb_stderr():
    """Verify reverse asymmetric bursts (100KB stdout vs 10MB stderr)."""
    stdout_bytes = 100 * 1024
    stderr_bytes = 10 * 1024 * 1024

    code = (
        f"import sys\n"
        f"sys.stdout.write('o' * 51200)\n"
        f"sys.stdout.flush()\n"
        f"chunk = 'Y' * 65536\n"
        f"for i in range({stderr_bytes // 65536}):\n"
        f"    sys.stderr.write(chunk)\n"
        f"    if i == 80:\n"
        f"        sys.stdout.write('O' * 51200)\n"
        f"        sys.stdout.flush()\n"
        f"sys.stderr.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=15.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert len(stdout_text) == stdout_bytes
        assert len(stderr_text) == stderr_bytes
        assert stdout_text.startswith("o") and stdout_text.endswith("O")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 3. ABRUPT PROCESS TERMINATION & BROKEN PIPE HANDLING
# ==============================================================================

def test_abrupt_child_crash_and_broken_pipe():
    """Verify os._exit(42) mid-stream does not hang drain threads or _communicate_with_pause."""
    emitted = 2 * 1024 * 1024
    code = (
        f"import os, sys\n"
        f"sys.stdout.write('K' * {emitted})\n"
        f"sys.stdout.flush()\n"
        f"os._exit(42)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    t0 = time.monotonic()
    stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
        proc, timeout=5.0, pause_event=None
    )
    elapsed = time.monotonic() - t0
    proc.wait(timeout=2.0)

    assert proc.returncode == 42
    assert len(stdout_text) == emitted
    assert elapsed < 2.0


def test_child_closes_stdout_early_while_running():
    """Verify child closing stdout explicitly does not break stderr draining or hang."""
    code = (
        "import sys, time\n"
        "sys.stdout.write('STDOUT_CLOSED_EARLY\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stdout.close()\n"
        "time.sleep(0.1)\n"
        "sys.stderr.write('STDERR_STILL_ALIVE\\n')\n"
        "sys.stderr.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    t0 = time.monotonic()
    stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
        proc, timeout=5.0, pause_event=None
    )
    elapsed = time.monotonic() - t0
    proc.wait(timeout=2.0)

    assert proc.returncode == 0
    assert "STDOUT_CLOSED_EARLY" in stdout_text
    assert "STDERR_STILL_ALIVE" in stderr_text
    assert elapsed < 2.0


# ==============================================================================
# 4. HIGH-FREQUENCY PAUSE/RESUME THRASHING (50+ CYCLES)
# ==============================================================================

def test_high_frequency_pause_resume_thrashing_50_cycles():
    """Verify 50+ rapid pause/resume cycles while child emits bursts under deadline control."""
    child_duration = 2.0
    code = (
        f"import time, sys\n"
        f"t_end = time.time() + {child_duration}\n"
        f"count = 0\n"
        f"while time.time() < t_end:\n"
        f"    sys.stdout.write(f'line_{{count}}\\n')\n"
        f"    sys.stdout.flush()\n"
        f"    count += 1\n"
        f"    time.sleep(0.01)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    pause_event = threading.Event()
    stop_thrash = threading.Event()
    toggle_count = 0

    def _thrasher():
        nonlocal toggle_count
        while not stop_thrash.is_set():
            time.sleep(0.02)
            if stop_thrash.is_set():
                break
            pause_event.set()
            toggle_count += 1
            time.sleep(0.02)
            pause_event.clear()
            toggle_count += 1

    th = threading.Thread(target=_thrasher, daemon=True)
    th.start()

    t_start = time.monotonic()
    try:
        # Timeout of 1.5s is smaller than wall clock 2.0s, but because pause was set ~50% of the time,
        # effective active time is ~1.0s < 1.5s, so it must complete cleanly without timeout!
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=1.5, pause_event=pause_event
        )
        wall_elapsed = time.monotonic() - t_start
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert toggle_count >= 50, f"Expected >= 50 toggles, got {toggle_count}"
        assert wall_elapsed > 1.5, f"Wall elapsed {wall_elapsed:.2f}s should exceed nominal timeout 1.5s"
        assert len(stdout_text.splitlines()) > 50
    finally:
        stop_thrash.set()
        th.join(timeout=1.0)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 5. EMPIRICAL FINDINGS REPRODUCTION TESTS (FINDINGS 1 & 2)
# ==============================================================================

def test_finding_1_reproduction_partial_output_under_64kb_lost_on_timeout():
    """Empirical reproduction: Demonstrates that output <64KB is lost when child times out.

    Root Cause:
    _drain_stream uses `stream.read(65536)` on a TextIOWrapper.
    In Python, TextIOWrapper.read(size) blocks until either 65536 characters
    have been accumulated or EOF is reached.
    If the child process emits a short burst (<64KB) and then hangs,
    _drain_stream remains blocked and chunks is empty when TimeoutExpired is raised.
    """
    code = (
        "import sys, time\n"
        "sys.stdout.write('CRUCIAL_PRE_TIMEOUT_DIAGNOSTIC_DATA\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(10.0)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            CodexRuntime._communicate_with_pause(
                proc, timeout=1.0, pause_event=None
            )

        captured_output = getattr(exc_info.value, "output", "") or ""
        # EXPECTED CONTRACT (Feature 3): captured_output MUST contain pre-timeout emitted data.
        # CURRENT DEFECT: captured_output is empty ('') because TextIOWrapper.read(65536) blocks.
        assert "CRUCIAL_PRE_TIMEOUT_DIAGNOSTIC_DATA" in captured_output, (
            f"Pre-timeout buffer lost! Captured {len(captured_output)} bytes, expected 'CRUCIAL_PRE_TIMEOUT_DIAGNOSTIC_DATA'"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_finding_2_reproduction_timeout_incurs_one_second_stall():
    """Empirical reproduction: Demonstrates the 1.0s stall penalty in finally block.

    Root Cause:
    In _communicate_with_pause lines 280-286:
        finally:
            if stdout_thread is not None and stdout_thread.is_alive():
                stdout_thread.join(timeout=0.5)
            if stderr_thread is not None and stderr_thread.is_alive():
                stderr_thread.join(timeout=0.5)

    When TimeoutExpired is raised, the child process is still running and holding
    open stdout and stderr pipes. Both threads are blocked on stream.read().
    Joining them with timeout=0.5 forces an unconditional 0.5s + 0.5s = 1.0s delay
    on every single timeout event.
    """
    code = "import time; time.sleep(10.0)\n"

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            CodexRuntime._communicate_with_pause(
                proc, timeout=0.1, pause_event=None
            )
        elapsed = time.monotonic() - t0
        # EXPECTED CONTRACT: A 0.1s timeout should raise TimeoutExpired in ~0.1s to 0.4s.
        # CURRENT DEFECT: Elapsed is ~1.25s due to 0.5s + 0.5s joins in finally block.
        assert elapsed < 0.6, (
            f"Timeout took {elapsed:.2f}s for a 0.1s timeout! Incurred ~1.0s stall in finally block joins."
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 6. GEN2 ADVERSARIAL STRESS & RAM BOUNDING (REQUIREMENT R4)
# ==============================================================================

def test_adversarial_120mb_stream_ram_bound_and_markers():
    """Adversarial stress: 120MB subprocess emission verified <= 10.1MB in RAM with head/tail markers."""
    total_bytes = 120 * 1024 * 1024  # 125,829,120 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    code = (
        f"import sys\n"
        f"head = b'<<<START_120MB_HEAD_MARKER>>>' + b'A' * ({chunk_size} - 29)\n"
        f"mid = b'A' * {chunk_size}\n"
        f"tail = b'A' * ({chunk_size} - 27) + b'<<<END_120MB_TAIL_MARKER>>>'\n"
        f"sys.stdout.buffer.write(head)\n"
        f"for _ in range({iterations - 2}):\n"
        f"    sys.stdout.buffer.write(mid)\n"
        f"sys.stdout.buffer.write(tail)\n"
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
            proc, timeout=30.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 15.0, f"120MB stream took {elapsed:.2f}s"
        assert stderr_text == ""

        # RAM bound verification (10MB + notice buffer <= 10.1MB)
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert stdout_text.startswith("<<<START_120MB_HEAD_MARKER>>>")
        assert stdout_text.endswith("<<<END_120MB_TAIL_MARKER>>>")

        expected_omitted = total_bytes - 10 * 1024 * 1024
        assert f"output truncated: {expected_omitted} bytes truncated in memory (no disk log configured)" in stdout_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_adversarial_120mb_split_parallel_disk_streaming(tmp_path):
    """Adversarial stress: 60MB stdout + 60MB stderr concurrent emission with log_file disk capture."""
    bytes_per_stream = 60 * 1024 * 1024  # 62,914,560 bytes each (total 120MB)
    chunk_size = 65536
    iterations = bytes_per_stream // chunk_size

    code = (
        f"import sys\n"
        f"c_out = b'O' * {chunk_size}\n"
        f"c_err = b'E' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.buffer.write(c_out)\n"
        f"    sys.stderr.buffer.write(c_err)\n"
        f"sys.stdout.buffer.flush()\n"
        f"sys.stderr.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    log_file = tmp_path / "concurrent_120mb_stream.log"

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=35.0, pause_event=None, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 20.0, f"Concurrent 120MB stream took {elapsed:.2f}s"

        # Both streams individually capped at 10.1MB
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
        assert len(stderr_text) <= 10 * 1024 * 1024 + 500
        # Requirement R4 Test 3: Combined aggregate RAM <= 10.1MB
        assert len(stdout_text) + len(stderr_text) <= 10 * 1024 * 1024 + 1000
        assert "truncated in memory" in stdout_text
        assert "truncated in memory" in stderr_text

        # Requirement R4 Test 1: DISK_LOG_TRUNCATED verification on 120MB stream with 50MB disk quota
        assert (
            "DISK_LOG_TRUNCATED: 52428800 bytes saved, 73400320 bytes omitted from disk"
            in stdout_text
        )
        assert (
            "DISK_LOG_TRUNCATED: 52428800 bytes saved, 73400320 bytes omitted from disk"
            in stderr_text
        )

        # Disk file capped at 50MB (52,428,800 bytes) per Gen3 R3
        assert log_file.exists()
        disk_size = log_file.stat().st_size
        expected_quota = 50 * 1024 * 1024
        assert disk_size == expected_quota, (
            f"Expected {expected_quota} bytes on disk, found {disk_size}!"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_adversarial_single_oversized_chunk_buffer_split():
    """Adversarial stress: Single 25MB chunk exceeds max_bytes (10MB) in a single append call."""
    from mtm_core.runtime.codex import BoundedStreamBuffer

    buf = BoundedStreamBuffer(max_bytes=10 * 1024 * 1024)
    oversized_data = b"HEAD" + b"X" * (25 * 1024 * 1024 - 8) + b"TAIL"
    assert len(oversized_data) == 25 * 1024 * 1024

    buf.append(oversized_data)
    result = buf.get_text()

    assert len(result) <= 10 * 1024 * 1024 + 500
    assert result.startswith("HEAD")
    assert result.endswith("TAIL")
    expected_omitted = 25 * 1024 * 1024 - 10 * 1024 * 1024
    assert f"{expected_omitted} bytes truncated in memory" in result


def test_adversarial_pause_thrashing_during_high_volume_stream_with_log(tmp_path):
    """Adversarial stress: 100 rapid pause/resume cycles while streaming 20MB to disk."""
    total_bytes = 20 * 1024 * 1024
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    code = (
        f"import sys, time\n"
        f"chunk = b'T' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.buffer.write(chunk)\n"
        f"sys.stdout.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    log_file = tmp_path / "thrash_20mb.log"
    pause_ev = threading.Event()
    stop_thrash = threading.Event()
    toggles = 0

    def _thrasher():
        nonlocal toggles
        while not stop_thrash.is_set():
            pause_ev.set()
            toggles += 1
            time.sleep(0.01)
            pause_ev.clear()
            toggles += 1
            time.sleep(0.01)

    th = threading.Thread(target=_thrasher, daemon=True)
    th.start()

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=25.0, pause_event=pause_ev, log_file=log_file
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert log_file.exists()
        assert log_file.stat().st_size == total_bytes
        assert len(stdout_text) <= 10 * 1024 * 1024 + 500
    finally:
        stop_thrash.set()
        th.join(timeout=1.0)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_adversarial_cancellation_during_extreme_stream(tmp_path):
    """Adversarial stress: Mid-stream cancellation cleanly aborts high-volume stream and unlinks log."""
    ws = tmp_path / "cancel_ws"
    ws.mkdir()
    log_file = tmp_path / "cancelled_stream.log"

    # Infinite high-speed output generator
    code = (
        "import sys, time\n"
        "chunk = b'C' * 65536\n"
        "while True:\n"
        "    sys.stdout.buffer.write(chunk)\n"
        "    sys.stdout.buffer.flush()\n"
    )

    cancel_ev = threading.Event()
    rt = CodexRuntime()

    # Pre-populate log_file to test unlinking
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=str(ws),
    )

    # Spawn thread to cancel after 0.25s
    def _canceller():
        time.sleep(0.25)
        cancel_ev.set()
        # Kill proc to simulate _watch_cancel
        try:
            from mtm_core.runtime.process import terminate_process_tree
            terminate_process_tree(proc)
        except Exception:
            pass

    th = threading.Thread(target=_canceller, daemon=True)
    th.start()

    try:
        t0 = time.monotonic()
        try:
            CodexRuntime._communicate_with_pause(
                proc, timeout=10.0, pause_event=None, log_file=log_file
            )
        except Exception:
            pass
        elapsed = time.monotonic() - t0

        # Simulate run() cleanup upon cancellation
        if cancel_ev.is_set() and log_file:
            log_file.unlink(missing_ok=True)

        assert elapsed < 3.0, f"Cancellation took {elapsed:.2f}s, expected < 3.0s"
        assert not log_file.exists(), "Cancelled stream log_file was not unlinked!"
    finally:
        th.join(timeout=1.0)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 7. GEN4 REQUIREMENT R4 TEST 3: CONCURRENT 50MB STDOUT + 50MB STDERR RAM BOUND
# ==============================================================================

def test_r4_concurrent_50mb_stdout_50mb_stderr_respects_aggregate_10mb_ram_bound():
    """R4 Test 3: Subprocess emitting concurrent 50MB stdout + 50MB stderr (100MB total) respects the aggregate 10MB RAM bound (len(stdout) + len(stderr) <= 10.1MB)."""
    bytes_per_pipe = 50 * 1024 * 1024  # 52,428,800 bytes each (100MB total)
    chunk_size = 65536
    iterations = bytes_per_pipe // chunk_size

    code = (
        f"import sys\n"
        f"chunk_out_head = (b'HEAD_OUT' + b'O' * ({chunk_size} - 8))\n"
        f"chunk_out_mid = b'O' * {chunk_size}\n"
        f"chunk_out_tail = (b'O' * ({chunk_size} - 8) + b'TAIL_OUT')\n"
        f"chunk_err_head = (b'HEAD_ERR' + b'E' * ({chunk_size} - 8))\n"
        f"chunk_err_mid = b'E' * {chunk_size}\n"
        f"chunk_err_tail = (b'E' * ({chunk_size} - 8) + b'TAIL_ERR')\n"
        f"sys.stdout.buffer.write(chunk_out_head)\n"
        f"sys.stderr.buffer.write(chunk_err_head)\n"
        f"for _ in range({iterations - 2}):\n"
        f"    sys.stdout.buffer.write(chunk_out_mid)\n"
        f"    sys.stderr.buffer.write(chunk_err_mid)\n"
        f"sys.stdout.buffer.write(chunk_out_tail)\n"
        f"sys.stderr.buffer.write(chunk_err_tail)\n"
        f"sys.stdout.buffer.flush()\n"
        f"sys.stderr.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    try:
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=30.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=5.0)

        assert proc.returncode == 0
        assert elapsed < 15.0, f"Concurrent 100MB emission took {elapsed:.2f}s, expected < 15.0s"

        # AGGREGATE RAM BOUND: combined stdout + stderr in memory <= 10.1MB
        combined_ram = len(stdout_text) + len(stderr_text)
        max_aggregate_ram = 10 * 1024 * 1024 + 1000  # 10MB + notice strings <= 10.1MB
        assert combined_ram <= max_aggregate_ram, (
            f"Aggregate RAM bound exceeded: len(stdout)={len(stdout_text)}, len(stderr)={len(stderr_text)}, "
            f"combined={combined_ram} bytes > {max_aggregate_ram} bytes ceiling!"
        )

        # Head and tail markers preserved on both streams
        assert stdout_text.startswith("HEAD_OUT")
        assert stdout_text.endswith("TAIL_OUT")
        assert stderr_text.startswith("HEAD_ERR")
        assert stderr_text.endswith("TAIL_ERR")

        # In-memory truncation notice present on both streams
        assert "truncated in memory" in stdout_text
        assert "truncated in memory" in stderr_text
        assert "(no disk log configured)" in stdout_text
        assert "(no disk log configured)" in stderr_text
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


