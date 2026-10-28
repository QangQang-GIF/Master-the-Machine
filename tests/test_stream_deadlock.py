"""High-volume stream stress and deadlock immunity acceptance tests (R1 & R4).

Spawns real OS child processes emitting high data volumes (>10MB stdout/stderr)
across unpaused, split, paused, resumed, and timeout conditions to empirically verify:
1. No OS pipe buffer saturation or deadlock in _communicate_with_pause.
2. Deadline deduction slices strictly freeze during pause_event.
3. Subprocess.TimeoutExpired preserves pre-timeout buffers.
4. CodexRuntime.run() handles high-volume workers end-to-end.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from mtm_core.runtime.codex import CodexRuntime


def test_unpaused_high_volume_stream_10mb():
    """R4 / R1: Verify unpaused child emitting >10MB stdout drains completely in < 3s without deadlock."""
    total_bytes = 10 * 1024 * 1024  # 10,485,760 bytes
    code = (
        f"import sys\n"
        f"sys.stdout.write('X' * {total_bytes})\n"
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
        pause_event = threading.Event()  # Unset event (unpaused)
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=10.0, pause_event=pause_event
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert len(stdout_text) == total_bytes
        assert stdout_text.startswith("X") and stdout_text.endswith("X")
        assert stderr_text == ""
        assert elapsed < 3.0, f"Draining 10MB took {elapsed:.2f}s, expected < 3.0s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_split_high_volume_stream_stdout_and_stderr():
    """R4 / R1: Verify interleaved 5MB stdout and 5MB stderr drain simultaneously without pipe deadlock."""
    bytes_per_stream = 5 * 1024 * 1024  # 5,242,880 bytes
    chunk_size = 65536
    iterations = bytes_per_stream // chunk_size  # 80 chunks

    code = (
        f"import sys\n"
        f"chunk_out = 'O' * {chunk_size}\n"
        f"chunk_err = 'E' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.write(chunk_out)\n"
        f"    sys.stdout.flush()\n"
        f"    sys.stderr.write(chunk_err)\n"
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
            proc, timeout=10.0, pause_event=None
        )
        elapsed = time.monotonic() - t0
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert len(stdout_text) == bytes_per_stream
        assert len(stderr_text) == bytes_per_stream
        assert stdout_text.startswith("O")
        assert stderr_text.startswith("E")
        assert elapsed < 4.0, f"Draining split 5MB+5MB took {elapsed:.2f}s, expected < 4.0s"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_high_volume_stream_pause_resume_deadline_freeze():
    """R4 / R1: Verify child emitting 10MB while pause_event is set longer than nominal timeout completes cleanly."""
    total_bytes = 10 * 1024 * 1024  # 10,485,760 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size  # 160 chunks

    # Child writes in chunks with short sleeps: active execution duration = 160 * 0.007s = 1.12s
    code = (
        f"import sys, time\n"
        f"chunk = 'D' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.write(chunk)\n"
        f"    sys.stdout.flush()\n"
        f"    time.sleep(0.007)\n"
    )

    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    pause_event = threading.Event()
    # Nominal timeout budget is 1.0s (less than total active time + pause hold).
    # Pause is held for 1.3s (longer than the entire 1.0s timeout budget).
    nominal_timeout = 1.0
    pause_hold_duration = 1.3

    def _pause_controller():
        time.sleep(0.1)
        pause_event.set()
        time.sleep(pause_hold_duration)
        pause_event.clear()

    ctrl_thread = threading.Thread(target=_pause_controller, daemon=True)

    try:
        ctrl_thread.start()
        t0 = time.monotonic()
        stdout_text, stderr_text = CodexRuntime._communicate_with_pause(
            proc, timeout=nominal_timeout, pause_event=pause_event
        )
        elapsed = time.monotonic() - t0
        ctrl_thread.join(timeout=2.0)
        proc.wait(timeout=2.0)

        assert proc.returncode == 0
        assert len(stdout_text) == total_bytes
        # Without pause freezing, proc would have failed with TimeoutExpired at 0.7s
        assert elapsed >= pause_hold_duration, f"Elapsed {elapsed:.2f}s should reflect freeze duration"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_high_volume_stream_genuine_timeout_preserves_buffer():
    """R4 / R1: Verify genuine timeout raises TimeoutExpired and preserves pre-timeout captured buffer."""
    bytes_emitted = 2 * 1024 * 1024  # 2,097,152 bytes (2MB)
    code = (
        f"import sys, time\n"
        f"sys.stdout.write('T' * {bytes_emitted})\n"
        f"sys.stdout.flush()\n"
        f"time.sleep(5.0)\n"
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
                proc, timeout=0.4, pause_event=threading.Event()
            )

        assert exc_info.value.timeout == 0.4
        output_buffer = getattr(exc_info.value, "output", "") or ""
        assert len(output_buffer) == bytes_emitted
        assert output_buffer.startswith("T")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2.0)


def test_codex_runtime_run_with_high_volume_worker(tmp_path):
    """R4: Verify CodexRuntime.run() executes a high-volume emitting script under pause_event control."""
    worker_script = tmp_path / "high_volume_worker.py"
    total_bytes = 10 * 1024 * 1024  # 10,485,760 bytes
    chunk_size = 65536
    iterations = total_bytes // chunk_size

    worker_script.write_text(
        f"import sys, time\n"
        f"chunk = 'W' * {chunk_size}\n"
        f"for _ in range({iterations}):\n"
        f"    sys.stdout.write(chunk)\n"
        f"    sys.stdout.flush()\n"
        f"    time.sleep(0.003)\n",
        encoding="utf-8",
    )

    runtime = CodexRuntime(codex_path=sys.executable)

    pause_event = threading.Event()

    def _pause_controller():
        time.sleep(0.1)
        pause_event.set()
        time.sleep(0.4)
        pause_event.clear()

    ctrl_thread = threading.Thread(target=_pause_controller, daemon=True)
    ctrl_thread.start()

    with patch.object(runtime, "build_codex_cmd", return_value=[sys.executable, str(worker_script)]):
        result = runtime.run(
            workspace=tmp_path,
            prompt="run high volume worker",
            timeout=10,
            pause_event=pause_event,
            backend="codex",
        )

    ctrl_thread.join(timeout=2.0)

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.success is True
    assert len(result.raw_stdout) == total_bytes
