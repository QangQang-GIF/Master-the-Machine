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
        assert len(stdout_text) == total_bytes
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
        assert len(stdout_text) == total_bytes
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
        assert len(stdout_text) == bytes_per_stream
        assert len(stderr_text) == bytes_per_stream
        assert stdout_text.startswith("O") and stdout_text.endswith("O")
        assert stderr_text.startswith("E") and stderr_text.endswith("E")
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
                proc, timeout=0.2, pause_event=None
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
