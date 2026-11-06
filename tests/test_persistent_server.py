"""serve-start keeps one server per worker; serve-stop reaps it; prewarm runs in parallel."""
import os
import sys
import time
import urllib.request

import pytest

from runner.project_runtime import ProjectRuntime
import runner.web_session as ws

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process-group teardown")


def _runtime(tmp_path, worker="worker-p"):
    root = tmp_path / "match"
    root.mkdir(exist_ok=True)
    (root / ".env.example").write_text("AUTH_SECRET=stable-test-secret\n")
    return ProjectRuntime(root, f"persist-{os.getpid()}", worker)


SERVER = '''import http.server, os
class H(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
 def log_message(self, *a): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), H).serve_forever()
'''


def _url(rt):
    return f"http://127.0.0.1:{rt.env['PORT']}/"


def test_start_reuse_and_stop(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_PREWARM_ROUTES", "/")
    rt = _runtime(tmp_path)
    try:
        first = ws.start_background_server(rt, [sys.executable, "-u", "-c", SERVER], readiness_timeout=15)
        assert first["status"] == "READY" and first["reused"] is False
        assert urllib.request.urlopen(_url(rt), timeout=2).read() == b"ok"
        again = ws.start_background_server(rt, [sys.executable, "-u", "-c", SERVER], readiness_timeout=15)
        assert again["status"] == "READY" and again["reused"] is True and again["pid"] == first["pid"]
    finally:
        stopped = ws.stop_background_server(rt)
    assert stopped["status"] == "STOPPED"
    for _ in range(40):
        if not ws._responds(_url(rt), 0.3):
            break
        time.sleep(0.1)
    else:
        pytest.fail("persistent server survived serve-stop")
    assert not ws._server_state_path(rt).exists()


def test_server_that_dies_is_reported(tmp_path):
    rt = _runtime(tmp_path, "worker-dies")
    res = ws.start_background_server(rt, [sys.executable, "-c", "import sys; sys.exit(3)"], readiness_timeout=10)
    assert res["status"] == "FAILED" and "exit 3" in res["reason"]
    assert not ws._server_state_path(rt).exists()


def test_prewarm_is_parallel(monkeypatch):
    calls = []

    def slow(url, timeout=0):
        calls.append(url)
        time.sleep(0.5)
        raise OSError("no server")

    monkeypatch.setattr(ws, "urlopen", slow)
    t0 = time.monotonic()
    ws.prewarm_routes(1, ["/a", "/b", "/c", "/d"], timeout=1)
    assert len(calls) == 4 and time.monotonic() - t0 < 1.5   # sequential would take >= 2 s
