#!/usr/bin/env bash
# Run every case under tests/cases and print a summary.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pass=0; fail=0
for c in "$HERE"/cases/*/; do
  name="$(basename "$c")"
  if bash "$HERE/run_case.sh" "$c" "${TMPDIR:-/tmp}/carddemo-tests/$name" > /dev/null 2>&1; then
    echo "PASS $name"; pass=$((pass+1))
  else
    echo "FAIL $name"; fail=$((fail+1))
  fi
done
echo "passed=$pass failed=$fail"
[ "$fail" -eq 0 ]
