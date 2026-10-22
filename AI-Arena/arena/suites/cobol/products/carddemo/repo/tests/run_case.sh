#!/usr/bin/env bash
# Run one regression case and compare its results with the expected files.
#
# usage: tests/run_case.sh <case-dir> [work-dir]
#
# A case directory contains:
#   cmd        job to run and its arguments, e.g. "posttran" or "intcalc 2022071800"
#   checks     list of result files (relative to <data>/out) to compare
#   input/     data files that replace the shipped app/data/ASCII files
#   expected/  expected copies of the files listed in checks
# Programs must be built first (./build.sh). Exit status 0 = case passed.
set -uo pipefail
APP_ROOT="${APP_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CASE_DIR="$(cd "${1:?usage: run_case.sh <case-dir> [work-dir]}" && pwd)"
WORK="${2:-$(mktemp -d)}"
export COB_CURRENT_DATE="${COB_CURRENT_DATE:-2022/07/18 10:00:00.00}"

rm -rf "$WORK"
mkdir -p "$WORK/data"
cp "$APP_ROOT"/app/data/ASCII/* "$WORK/data/"
[ -d "$CASE_DIR/input" ] && cp -r "$CASE_DIR/input/." "$WORK/data/"

read -r JOB ARGS < "$CASE_DIR/cmd"
# shellcheck disable=SC2086
if ! timeout 300 bash "$APP_ROOT/jobs/$JOB.sh" "$WORK/data" $ARGS > "$WORK/job.log" 2>&1; then
  echo "job $JOB failed:"; tail -20 "$WORK/job.log"
  exit 1
fi

status=0
while read -r f; do
  [ -z "$f" ] && continue
  if [ ! -f "$WORK/data/out/$f" ]; then
    echo "MISSING out/$f"; status=1; continue
  fi
  if ! diff -u "$CASE_DIR/expected/$f" "$WORK/data/out/$f" > "$WORK/$f.diff" 2>&1; then
    echo "DIFF out/$f"; head -40 "$WORK/$f.diff"; status=1
  fi
done < "$CASE_DIR/checks"
exit $status
