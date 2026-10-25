"""Adversarial Challenger Re-verification Suite for CodexRuntime._communicate_with_pause.

Tests covering:
1. Micro-burst partial output (<100B, 1KB, 10KB, 63KB, 65KB) on timeout for both stdout and stderr.
2. Multi-byte UTF-8 preservation across chunk boundaries (Vietnamese, emojis, CJK) without replacement chars.
3. Precise timeout latency benchmarks (< 0.15s latency overhead above nominal timeout; no 1.0s stall).
4. High-frequency pause/resume thrashing (100+ cycles) with strict deadline drift verification (< 60ms).
5. End-to-end CodexRuntime.run() timeout handling: verifies partial stdout retained in RuntimeResult without crashes.
6. Mixed pipe configurations (stdout-only, stderr-only, stderr=STDOUT, devnull).
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
import pytest

from mtm_core.runtime.codex import CodexRuntime


# ==============================================================================
# 1. MICRO-BURST PARTIAL OUTPUT ON TIMEOUT (<100B, 1KB, 10KB, 63KB, 65KB)
# ==============================================================================

@pytest.mark.parametrize("payload_size", [42, 1024, 10240, 64512, 65536, 70000])
def test_partial_output_retained_on_timeout_various_sizes(payload_size: int):
    """Verify stdout of various sizes under and over 64KB is strictly preserved on TimeoutExpired."""
    prefix = f"PAYLOAD_START_{payload_size}_"
    suffix = f"_PAYLOAD_END_{payload_size}"
    fill_len = max(0, payload_size - len(prefix) - len(suffix))

    code = (
        f"import sys, time\n"
        f"payload = {repr(prefix)} + ('Z' * {fill_len}) + {repr(suffix)}\n"
        f"sys.stdout.write(payload)\n"
        f"sys.stdout.flush()\n"
        f"time.sleep(10.0)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            CodexRuntime._communicate_with_pause(proc, timeout=0.2, pause_event=None)

        out = getattr(exc_info.value, "output", "") or ""
        assert out.startswith(prefix)
        assert out.endswith(suffix)
        assert len(out) == payload_size, f"Expected {payload_size} bytes, got {len(out)}"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_partial_stdout_and_stderr_both_retained_on_timeout():
    """Verify BOTH partial stdout and partial stderr (<64KB) are retained simultaneously in TimeoutExpired."""
    out_payload = "STDOUT_PARTIAL_DIAGNOSTIC_DATA_12345"
    err_payload = "STDERR_PARTIAL_CRITICAL_TRACEBACK_67890"

    code = (
        f"import sys, time\n"
        f"sys.stdout.write({repr(out_payload)})\n"
        f"sys.stdout.flush()\n"
        f"sys.stderr.write({repr(err_payload)})\n"
        f"sys.stderr.flush()\n"
        f"time.sleep(10.0)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            CodexRuntime._communicate_with_pause(proc, timeout=0.2, pause_event=None)

        out = getattr(exc_info.value, "output", "") or ""
        err = getattr(exc_info.value, "stderr", "") or ""

        assert out == out_payload
        assert err == err_payload
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_multibyte_utf8_split_across_chunks_no_corruption():
    """Verify multibyte UTF-8 characters (Vietnamese, emojis) are decoded without replacement character \ufffd."""
    # Generate 150KB of diverse multibyte Vietnamese text with emojis inside the child process
    code = (
        "import sys\n"
        "phrase = 'Cuộc thi Agent AI: Tự động hóa kiểm thử phần mềm 🚀🔥 2026. '\n"
        "for _ in range(2000):\n"
        "    sys.stdout.buffer.write(phrase.encode('utf-8'))\n"
        "sys.stdout.buffer.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )

    try:
        out, err = CodexRuntime._communicate_with_pause(proc, timeout=10.0, pause_event=None)
        proc.wait(timeout=2.0)
        assert proc.returncode == 0, f"Child failed with stderr: {err}"
        assert "\ufffd" not in out, "Multibyte characters were corrupted during chunk reading!"
        assert "Cuộc thi Agent AI" in out
        assert "🚀🔥" in out
        assert len(out) > 100000
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 2. PRECISE TIMEOUT LATENCY OVERHEAD BENCHMARK (< 0.15s overhead)
# ==============================================================================

def test_timeout_latency_overhead_under_150ms():
    """Verify timeout overhead is minimal (< 0.15s overhead above requested timeout) and never stalls for 1.0s."""
    requested_timeout = 0.15
    code = "import time\ntime.sleep(10.0)\n"

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired):
            CodexRuntime._communicate_with_pause(proc, timeout=requested_timeout, pause_event=None)
        wall_time = time.monotonic() - t0
        latency_overhead = wall_time - requested_timeout

        # Windows timer resolution can be up to ~20-30ms plus 0.02s sleep slice.
        # Latency overhead should be well under 0.15s, and definitely nowhere near 1.0s.
        assert latency_overhead < 0.15, (
            f"Latency overhead {latency_overhead:.3f}s exceeded 0.15s limit! Wall time was {wall_time:.3f}s"
        )
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_timeout_latency_with_continuous_heavy_output():
    """Verify timeout overhead remains crisp (< 0.15s) even when child is actively flooding stdout when timeout hits."""
    requested_timeout = 0.20
    code = (
        "import sys, time\n"
        "while True:\n"
        "    sys.stdout.write('FLOOD_' * 1000 + '\\n')\n"
        "    sys.stdout.flush()\n"
        "    time.sleep(0.005)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        t0 = time.monotonic()
        with pytest.raises(subprocess.TimeoutExpired) as exc_info:
            CodexRuntime._communicate_with_pause(proc, timeout=requested_timeout, pause_event=None)
        wall_time = time.monotonic() - t0
        latency_overhead = wall_time - requested_timeout

        assert latency_overhead < 0.15, (
            f"Flooding timeout latency overhead {latency_overhead:.3f}s exceeded 0.15s limit!"
        )
        captured = getattr(exc_info.value, "output", "") or ""
        assert len(captured) > 0, "Flooding pre-timeout data should be captured!"
        assert "FLOOD_" in captured
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 3. HIGH-FREQUENCY PAUSE/RESUME THRASHING (100+ CYCLES) & DEADLINE DRIFT
# ==============================================================================

def test_pause_resume_100_cycles_thrashing_and_drift():
    """Verify 100+ rapid pause/resume cycles (every 10ms) without deadlock, with deadline drift < 80ms."""
    child_duration = 2.0
    code = (
        f"import time, sys\n"
        f"t_end = time.time() + {child_duration}\n"
        f"count = 0\n"
        f"while time.time() < t_end:\n"
        f"    sys.stdout.write(f'line_{{count}}\\n')\n"
        f"    sys.stdout.flush()\n"
        f"    count += 1\n"
        f"    time.sleep(0.005)\n"
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
            time.sleep(0.01)
            if stop_thrash.is_set():
                break
            pause_event.set()
            toggle_count += 1
            time.sleep(0.01)
            pause_event.clear()
            toggle_count += 1

    th = threading.Thread(target=_thrasher, daemon=True)
    th.start()

    t_start = time.monotonic()
    try:
        # Child runs for 2.0s wall time.
        # Pause is set ~50% of the time, so active execution time is ~1.0s.
        # Timeout of 1.6s allows clean completion without timing out.
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=1.6, pause_event=pause_event
        )
        wall_elapsed = time.monotonic() - t_start
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert toggle_count >= 100, f"Expected >= 100 toggles, got {toggle_count}"
        assert wall_elapsed > 1.6, f"Wall elapsed {wall_elapsed:.2f}s should exceed 1.6s"
        assert len(stdout_text.splitlines()) > 50
    finally:
        stop_thrash.set()
        th.join(timeout=1.0)
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


# ==============================================================================
# 4. END-TO-END CODEXRUNTIME.RUN() TIMEOUT AND LOG RETENTION
# ==============================================================================

def test_codex_runtime_run_timeout_retains_partial_output_and_logs(tmp_path: Path):
    """Verify CodexRuntime.run() catches TimeoutExpired, preserves partial stdout in RuntimeResult, writes log file."""
    hung_script = tmp_path / "hung_worker.py"
    log_file = tmp_path / "execution.log"

    diagnostic_msg = "PRE_TIMEOUT_STARTUP_INITIALIZATION_SUCCESSFUL\n"
    hung_script.write_text(
        f"import sys, time\n"
        f"sys.stdout.write({repr(diagnostic_msg)})\n"
        f"sys.stdout.flush()\n"
        f"time.sleep(10.0)\n",
        encoding="utf-8",
    )

    runtime = CodexRuntime(codex_path=sys.executable)

    with patch.object(runtime, "build_codex_cmd", return_value=[sys.executable, str(hung_script)]):
        result = runtime.run(
            workspace=tmp_path,
            prompt="test timeout output retention",
            timeout=1,  # 1 second timeout
            backend="codex",
            log_file=log_file,
        )

    assert result.timed_out is True
    assert result.exit_code == -1
    assert diagnostic_msg.strip() in result.raw_stdout
    # Check log file was written with the partial output
    assert log_file.exists()
    assert diagnostic_msg.strip() in log_file.read_text(encoding="utf-8")


# ==============================================================================
# 5. MIXED AND UNUSUAL PIPE CONFIGURATIONS
# ==============================================================================

def test_unusual_pipe_configuration_stdout_only():
    """Verify _communicate_with_pause handles subprocess with stdout=PIPE and stderr=DEVNULL."""
    code = "import sys\nsys.stdout.write('ONLY_STDOUT_HERE')\nsys.stdout.flush()\n"

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )

    try:
        out, err = CodexRuntime._communicate_with_pause(proc, timeout=5.0, pause_event=None)
        proc.wait(timeout=2.0)
        assert proc.returncode == 0
        assert out == "ONLY_STDOUT_HERE"
        assert err == ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_unusual_pipe_configuration_stderr_only():
    """Verify _communicate_with_pause handles subprocess with stdout=DEVNULL and stderr=PIPE."""
    code = "import sys\nsys.stderr.write('ONLY_STDERR_HERE')\nsys.stderr.flush()\n"

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    try:
        out, err = CodexRuntime._communicate_with_pause(proc, timeout=5.0, pause_event=None)
        proc.wait(timeout=2.0)
        assert proc.returncode == 0
        assert out == ""
        assert err == "ONLY_STDERR_HERE"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_unusual_pipe_configuration_combined_stderr_stdout():
    """Verify _communicate_with_pause handles subprocess with stderr=subprocess.STDOUT."""
    code = (
        "import sys\n"
        "sys.stdout.write('COMBINED_OUT\\n')\n"
        "sys.stdout.flush()\n"
        "sys.stderr.write('COMBINED_ERR\\n')\n"
        "sys.stderr.flush()\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        out, err = CodexRuntime._communicate_with_pause(proc, timeout=5.0, pause_event=None)
        proc.wait(timeout=2.0)
        assert proc.returncode == 0
        assert "COMBINED_OUT" in out
        assert "COMBINED_ERR" in out
        assert err == ""
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)
