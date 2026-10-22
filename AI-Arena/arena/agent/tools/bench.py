"""Bench client for one backlog item (Docker runs outside the sandbox).

  python tools/bench.py check            run the MVS tests (CR/BUG) on your current change
  python tools/bench.py exec "CMD"       run a shell command in the product's environment, with your
                                         change applied (cwd = the repo root inside the container)
  python tools/bench.py diff             show your change vs the handed-over code
"""
import json
import sys
import urllib.request
from pathlib import Path

RUN_DIR = Path.cwd()
CFG = json.loads((RUN_DIR / "bench.json").read_text(encoding="utf-8"))


def call(path, body=None):
    req = urllib.request.Request(CFG["bench_url"] + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=3700) as r:
        return json.loads(r.read())


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cmd = sys.argv[1]
    if cmd == "check":
        r = call("/check", {})
        for name, status in (r.get("tests") or {}).items():
            print(f"{status:8} {name}")
        print(r.get("output", ""))
        (RUN_DIR / "evidence.txt").write_text(json.dumps(r, indent=1), encoding="utf-8")
        print(f"\nMVS {'PASSED' if r['ok'] else 'NOT PASSED'}; saved to evidence.txt")
    elif cmd == "exec":
        r = call("/exec", {"cmd": " ".join(sys.argv[2:])})
        print(r["output"])
        print(f"\n(exit {r.get('exit_code')})")
    elif cmd == "diff":
        r = call("/diff")
        print(r.get("diff") or "(no changes)")
    else:
        sys.exit(__doc__)
    sys.exit(0 if r.get("ok") else 1)


if __name__ == "__main__":
    main()
