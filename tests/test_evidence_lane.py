"""Evidence lane (E): background dev server lifecycle and one-pass multi-screenshot capture."""
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from runner.project_runtime import ProjectRuntime
from runner.web_session import session_record_path, start_background_server, stop_recorded_session, _pid_alive

REPO = Path(__file__).resolve().parents[1]

APP = r'''
import http.server, os, urllib.parse
LOGIN = b"""<html><body><form method=post action=/login>
<input id=email name=email><input id=password name=password type=password>
<button type=submit>Sign in</button></form></body></html>"""
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def _send(self, code, body, headers=()):
        self.send_response(code)
        for k, v in headers: self.send_header(k, v)
        self.send_header("Content-Type", "text/html"); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        authed = "session=ok" in (self.headers.get("Cookie") or "")
        if self.path.startswith("/login"): return self._send(200, LOGIN)
        if self.path.startswith("/private") and not authed: return self._send(302, b"", [("Location", "/login")])
        return self._send(200, ("<html><body><h1>%s</h1></body></html>" % self.path).encode())
    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0); self.rfile.read(n)
        self._send(302, b"", [("Location", "/dashboard"), ("Set-Cookie", "session=ok; Path=/")])
http.server.ThreadingHTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


def runtime(tmp_path, worker="worker-e"):
    root = tmp_path / "match"
    root.mkdir(exist_ok=True)
    (root / ".env.example").write_text("AUTH_SECRET=stable-test-secret\n")
    return ProjectRuntime(root, "evidence-test", worker)


def test_session_start_stop_lifecycle(tmp_path):
    rt = runtime(tmp_path)
    info = start_background_server(rt, [sys.executable, "-c", APP], routes=["/dashboard", "/private"],
                                   readiness_timeout=15)
    try:
        assert info["status"] == "READY", info
        assert session_record_path(rt.match_root).is_file()
        assert not list(rt.match_root.glob('.mtm-session*'))
        assert set(info["prewarmed"]) == {"/dashboard", "/private"}
        body = urllib.request.urlopen(f"http://127.0.0.1:{rt.env['PORT']}/dashboard", timeout=3).read()
        assert b"/dashboard" in body
        again = start_background_server(rt, [sys.executable, "-c", APP], readiness_timeout=15)
        assert again["status"] == "ALREADY_RUNNING"
    finally:
        stopped = stop_recorded_session(rt.match_root)
    assert stopped["status"] == "STOPPED"
    assert not session_record_path(rt.match_root).exists()
    for _ in range(30):
        if not _pid_alive(info["pid"]):
            break
        time.sleep(0.1)
    assert not _pid_alive(info["pid"])
    assert stop_recorded_session(rt.match_root)["status"] == "NOT_RUNNING"


def test_session_start_reports_crash(tmp_path):
    rt = runtime(tmp_path)
    info = start_background_server(rt, [sys.executable, "-c", "raise SystemExit(3)"], readiness_timeout=5)
    assert info["status"] == "FAILED" and "exited" in info["error"]
    assert not session_record_path(rt.match_root).exists()


def _playwright_available():
    if not shutil.which("node"):
        return False
    probe = subprocess.run(["node", "-e", "require.resolve('playwright')"], capture_output=True)
    return probe.returncode == 0


needs_browser = pytest.mark.skipif(not _playwright_available(), reason="node + playwright required")


@pytest.fixture
def app_server(tmp_path):
    rt = runtime(tmp_path, "worker-cap")
    info = start_background_server(rt, [sys.executable, "-c", APP], routes=["/dashboard"], readiness_timeout=15)
    assert info["status"] == "READY", info
    yield rt
    stop_recorded_session(rt.match_root)


def _capture(rt, spec, *extra):
    spec_file = rt.match_root / "spec.json"
    spec_file.write_text(json.dumps(spec))
    env = {**os.environ, "PORT": rt.env["PORT"]}
    proc = subprocess.run(["node", str(REPO / "scripts" / "capture_multi.mjs"), str(spec_file), *extra],
                          capture_output=True, text=True, timeout=120, env=env, cwd=rt.match_root)
    return proc, json.loads(proc.stdout or "[]")


@needs_browser
def test_capture_multi_logs_in_once_and_writes_every_shot(app_server):
    rt = app_server
    out_dir = rt.match_root / "answers" / "BUG-02" / "evidence"
    proc, report = _capture(rt, {"outDir": str(out_dir), "shots": [
        {"route": "/private/a", "file": "tc-01.png"},
        {"route": "/private/b", "file": "tc-02.png", "viewport": {"width": 390, "height": 844}},
        {"route": "/", "file": "tc-03.png", "auth": False}]})
    assert proc.returncode == 0, proc.stderr
    assert [r["file"] for r in report] == ["tc-01.png", "tc-02.png", "tc-03.png"]
    assert all(r["ok"] for r in report)
    for name in ("tc-01.png", "tc-02.png", "tc-03.png"):
        data = (out_dir / name).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) > 500


@needs_browser
def test_capture_multi_skips_after_cutoff_without_placeholder(app_server):
    rt = app_server
    out_dir = rt.match_root / "ev"
    proc, report = _capture(rt, {"outDir": str(out_dir), "login": False,
                                 "shots": [{"route": "/", "file": "late.png"}]},
                            "--cutoff-epoch", str(time.time() - 5))
    assert proc.returncode == 0
    assert report == [{"file": "late.png", "ok": False, "skipped": True, "error": "evidence cutoff reached"}]
    assert not (out_dir / "late.png").exists()


@needs_browser
def test_capture_multi_rejects_path_escape(app_server):
    rt = app_server
    proc, report = _capture(rt, {"outDir": str(rt.match_root / "ev"), "login": False,
                                 "shots": [{"route": "/", "file": "../escape.png"}]})
    assert proc.returncode == 1
    assert report[0]["ok"] is False and not (rt.match_root / "escape.png").exists()


def test_envelope_carries_evidence_cutoff(tmp_path):
    from tests.test_lane_scheduler import make, project, item, run, NOW
    c = make(tmp_path)
    project(c, item("BUG-02", deadline_epoch=NOW + 1080))
    env = run(c, "BUG-02")["taskEnvelope"]
    # deadline - 150s is earlier than the 420s budget and the match end.
    assert env["evidence_cutoff_epoch"] == pytest.approx(min(NOW + 420, NOW + 1080 - 150, NOW + 2400 - 240), abs=2)
    assert env["evidence_cutoff_local_epoch"] > time.time()
