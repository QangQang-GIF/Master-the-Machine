#!/usr/bin/env bash
# Verifier for a COBOLEval task: compile the program in /app/src with every
# hidden test caller from /tests, run it and compare the result it writes.
set -u
LOG=/logs/verifier
mkdir -p "$LOG"
echo 0 > "$LOG/reward.txt"
cd /app
PROGRAM="/app/$(python3 -c 'import json;print(json.load(open("/tests/tests.json"))["program_path"])')"
python3 /tests/check.py /tests "$PROGRAM" "$LOG/output.json" > "$LOG/check.log" 2>&1
python3 - "$LOG" <<'PY'
import json, os, sys
log = sys.argv[1]
names = json.load(open("/tests/tests.json"))["required"]
try:
    res = {t["name"]: t for t in json.load(open(os.path.join(log, "output.json")))["tests"]}
except Exception:
    res = {}
tests = [{"name": n, "status": res.get(n, {}).get("status", "FAILED")} for n in names]
details = {n: res.get(n, {}).get("detail", "not run") for n in names}
json.dump({"tests": tests}, open(os.path.join(log, "output.json"), "w"), indent=1)
json.dump(details, open(os.path.join(log, "details.json"), "w"), indent=1)
ok = bool(tests) and all(t["status"] == "PASSED" for t in tests)
open(os.path.join(log, "reward.txt"), "w").write("1\n" if ok else "0\n")
print("reward", 1 if ok else 0)
PY
