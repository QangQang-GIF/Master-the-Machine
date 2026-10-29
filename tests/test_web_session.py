import os
import signal
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import URLError

import pytest

from runner.project_runtime import ProjectRuntime
from runner.web_session import run_web_session


def _runtime(tmp_path: Path) -> ProjectRuntime:
    root = tmp_path / "match"
    root.mkdir()
    (root / ".env.example").write_text("AUTH_SECRET=stable-test-secret\n")
    return ProjectRuntime(root, "web-test", "worker-a")


def _server_script(pid_file: Path) -> str:
    return f'''import http.server, os, subprocess, sys
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
open({str(pid_file)!r}, "w").write(str(child.pid))
class Handler(http.server.BaseHTTPRequestHandler):
 def do_GET(self):
  body = os.environ["AUTH_SECRET"].encode()
  self.send_response(200); self.end_headers(); self.wfile.write(body)
 def log_message(self, *args): pass
http.server.HTTPServer(("127.0.0.1", int(os.environ["PORT"])), Handler).serve_forever()
'''


def test_session_shares_environment_and_reaps_server_tree(tmp_path):
    runtime = _runtime(tmp_path)
    pid_file = tmp_path / "child.pid"
    server = [sys.executable, "-u", "-c", _server_script(pid_file)]
    check_code = '''import os, urllib.request
url = "http://127.0.0.1:%s/" % os.environ["PORT"]
assert urllib.request.urlopen(url, timeout=2).read().decode() == os.environ["AUTH_SECRET"]
'''
    result = run_web_session(runtime, server, [sys.executable, "-c", check_code], timeout=8)
    assert result.ok, result.stderr
    assert pid_file.exists()
    child_pid = int(pid_file.read_text())
    # The child belongs to the session's process group and must be gone too.
    for _ in range(30):
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("web-session descendant survived teardown")


def test_readiness_timeout_returns_diagnostic_and_stops_process(tmp_path):
    runtime = _runtime(tmp_path)
    result = run_web_session(
        runtime, [sys.executable, "-c", "import time; time.sleep(30)"],
        [sys.executable, "-c", "raise SystemExit(0)"], timeout=2,
        readiness_timeout=0.35,
    )
    assert not result.ok
    assert "readiness timed out" in result.stderr
    assert not result.timed_out  # bounded startup failure, not check timeout


@pytest.mark.parametrize("url", [
    "https://example.com/",
    "http://127.0.0.1:1/",
    "http://user@127.0.0.1:1/",
])
def test_rejects_external_or_wrong_port_readiness_urls(tmp_path, url):
    runtime = _runtime(tmp_path)
    with pytest.raises(ValueError, match="readiness URL"):
        run_web_session(runtime, [sys.executable, "-c", "pass"],
                        [sys.executable, "-c", "pass"], readiness_url=url)


def test_check_failure_is_preserved_and_server_is_stopped(tmp_path):
    runtime = _runtime(tmp_path)
    pid_file = tmp_path / "child.pid"
    result = run_web_session(
        runtime, [sys.executable, "-u", "-c", _server_script(pid_file)],
        [sys.executable, "-c", "raise SystemExit(7)"], timeout=8,
    )
    assert result.returncode == 7
    assert not result.ok
    assert pid_file.exists()


def test_sigterm_of_wrapper_reaps_server_group(tmp_path):
    runtime = _runtime(tmp_path)
    pid_file = tmp_path / "child.pid"
    wrapper = f'''from runner.project_runtime import ProjectRuntime
from runner.web_session import run_web_session
from pathlib import Path
runtime = ProjectRuntime(Path({str(runtime.match_root)!r}), "web-test", "worker-a")
server = [sys.executable, "-u", "-c", { _server_script(pid_file)!r}]
run_web_session(runtime, server, [sys.executable, "-c", "import time; time.sleep(30)"], timeout=60)
'''
    wrapper = "import sys\n" + wrapper
    proc = __import__("subprocess").Popen(
        [sys.executable, "-c", wrapper], cwd=Path(__file__).resolve().parents[1],
        stdout=__import__("subprocess").DEVNULL, stderr=__import__("subprocess").PIPE,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if pid_file.exists():
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{runtime.env['PORT']}/", timeout=0.2)
                    break
                except URLError:
                    pass
            time.sleep(0.05)
        else:
            pytest.fail("test web server did not become ready")
        child_pid = int(pid_file.read_text())
        os.killpg(proc.pid, signal.SIGTERM)
        assert proc.wait(timeout=6) == 128 + signal.SIGTERM

        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{runtime.env['PORT']}/", timeout=0.1)
            except URLError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("server port remained open after wrapper SIGTERM")

        for _ in range(50):
            try:
                os.kill(child_pid, 0)
                # A zombie has exited and cannot keep serving work.
                state = __import__("subprocess").run(
                    ["ps", "-o", "stat=", "-p", str(child_pid)],
                    capture_output=True, text=True, check=False,
                ).stdout.strip()
                if not state or state.startswith("Z"):
                    break
            except ProcessLookupError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("server descendant survived wrapper SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=2)
