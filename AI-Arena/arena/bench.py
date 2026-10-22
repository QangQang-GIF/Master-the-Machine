"""Bench for one eval item: runs Docker on the agent's behalf (Codex's sandbox blocks Docker).

  POST /check   MVS run (CR/BUG): statuses of the MVS tests + the log lines about them
  POST /exec    {"cmd"} run a shell command in the item's image, with the current change applied
  GET  /diff    current change vs the handed-over code
"""
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from arena import core

OUTPUT_LIMIT = 12000


def clip(text: str, limit=OUTPUT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:2000] + "\n...[truncated]...\n" + text[-(limit - 2000):]


def mvs_excerpt(log: str, tests: dict) -> str:
    """Only the log lines about failing MVS tests (the full log would reveal the hidden tests)."""
    lines, out = log.splitlines(), []
    for name, status in tests.items():
        if status == "PASSED":
            continue
        key = re.split(r"[ :|#]", name.strip())[-1] or name
        hits = [i for i, l in enumerate(lines) if key in l]
        for i in hits[:3]:
            out.extend(lines[i:i + 25])
    if not out and any(s != "PASSED" for s in tests.values()):
        out = lines[-40:]  # build/compile failure: the tail says why
    return clip("\n".join(out), 8000)


class Bench:
    def __init__(self, suite, row, work):
        self.suite, self.row, self.work = suite, row, work
        self.lock = threading.Lock()

    def diff(self):
        return core.git_diff(self.work)

    def check(self):
        if not self.row.get("mvs"):
            return {"ok": False, "output": "this item has no MVS; write and run your own checks with exec"}
        r = self.suite.check(self.row, strip_paths(self.diff(), self.row))
        return {"ok": r["ok"], "tests": r["tests"], "output": mvs_excerpt(r["log"], r["tests"])}

    def exec(self, cmd):
        code, log = core.run_with_patch(self.row["image"], self.row["workdir"], self.diff(), cmd + "\n",
                                        timeout=1200, setup=lambda c: self.suite.prepare(self.row, c))
        return {"ok": code == 0, "exit_code": code, "output": clip(log)}


def patch_files(patch: str) -> set[str]:
    return set(re.findall(r"^diff --git a/(\S+) b/", patch or "", re.M))


def strip_paths(patch: str, row) -> str:
    """Drop the agent's edits to files the item's test patch owns (the grader brings its own)."""
    owned = patch_files((row.get("mvs") or {}).get("test_patch") or row.get("test_patch")
                        or (row.get("pr") or {}).get("test_patch") or "")
    if not owned:
        return patch
    parts = re.split(r"(?=^diff --git )", patch, flags=re.M)
    return "".join(p for p in parts if not (p.startswith("diff --git") and
                                            re.match(r"diff --git a/(\S+)", p).group(1) in owned))


def serve(bench: Bench) -> ThreadingHTTPServer:
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.startswith("/diff"):
                return self.send({"ok": True, "diff": bench.diff()})
            self.send({"ok": False, "output": "not found"})

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                with bench.lock:
                    if self.path == "/check":
                        return self.send(bench.check())
                    if self.path == "/exec":
                        return self.send(bench.exec(str(body.get("cmd", ""))))
            except Exception as e:
                return self.send({"ok": False, "output": f"{type(e).__name__}: {e}"})
            self.send({"ok": False, "output": "not found"})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
