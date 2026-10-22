#!/usr/bin/env bash
# Verifier: build the programs in /app, install the hidden regression cases
# from /tests and run each of them.
set -u
LOG=/logs/verifier
mkdir -p "$LOG"
echo 0 > "$LOG/reward.txt"
cd /app

mkdir -p /app/tests/cases
cp /tests/run_case.sh /app/tests/run_case.sh
for c in /tests/cases/*/; do
  n="$(basename "$c")"
  rm -rf "/app/tests/cases/$n"
  cp -r "$c" "/app/tests/cases/$n"
done

: > "$LOG/results.txt"
if timeout 900 bash /app/build.sh > "$LOG/build.log" 2>&1; then built=1; else built=0; fi
while read -r n; do
  [ -z "$n" ] && continue
  st=FAILED
  if [ "$built" = 1 ] && APP_ROOT=/app bash /app/tests/run_case.sh "/app/tests/cases/$n" "/tmp/verifier-work/$n" > "$LOG/case-$n.log" 2>&1; then
    st=PASSED
  fi
  echo "$n $st" >> "$LOG/results.txt"
done < /tests/tests.txt

python3 - "$LOG" "$built" <<'PY'
import json, os, sys
log, built = sys.argv[1], sys.argv[2] == "1"
tests = []
for line in open(os.path.join(log, "results.txt")):
    parts = line.split()
    if len(parts) == 2:
        tests.append({"name": parts[0], "status": parts[1]})
json.dump({"tests": tests}, open(os.path.join(log, "output.json"), "w"), indent=1)
ok = built and bool(tests) and all(t["status"] == "PASSED" for t in tests)
open(os.path.join(log, "reward.txt"), "w").write("1\n" if ok else "0\n")
print("build", "ok" if built else "FAILED", "reward", 1 if ok else 0)
PY
