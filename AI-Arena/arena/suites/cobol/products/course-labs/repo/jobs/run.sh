#!/usr/bin/env bash
# Run one of the account programs against a data directory.
#
# usage: jobs/run.sh <data-dir> <PROGRAM> [args...]
#
# Input : <data-dir>/accounts.txt   accounts in text layout (see tools/ACCTCONV.cbl)
# Output: <data-dir>/out/prtline.txt  report written to PRTLINE (one record per line)
#         <data-dir>/out/sysout.txt   DISPLAY output
#         <data-dir>/out/rc.txt       return code of the program
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${BIN_DIR:-$ROOT/bin}"
DATA_DIR="$(cd "${1:?usage: run.sh <data-dir> <PROGRAM> [args...]}" && pwd)"
PGM="${2:?usage: run.sh <data-dir> <PROGRAM> [args...]}"
shift 2
OUT="$DATA_DIR/out"; WORK="$DATA_DIR/work"
rm -rf "$WORK"; mkdir -p "$OUT" "$WORK"

# Build the ACCTREC data set (fixed 170-byte records with packed fields)
DD_TXTFILE="$DATA_DIR/accounts.txt" DD_ACCTREC="$WORK/acctrec.dat" "$BIN_DIR/ACCTCONV"

# PRTLINE record length of the program (print line layout in its FD)
case "$PGM" in
  CBL0001) LRECL=119 ;;
  *)       LRECL=62 ;;
esac

set +e
DD_ACCTREC="$WORK/acctrec.dat" DD_PRTLINE="$WORK/prtline.dat" \
  "$BIN_DIR/$PGM" "$@" > "$OUT/sysout.txt" 2>&1
echo $? > "$OUT/rc.txt"
set -e

# Print records -> text lines. Records are fixed length; WRITE ... ADVANCING
# also puts line feeds in the file, which are treated as line breaks.
: > "$OUT/prtline.txt"
if [ -f "$WORK/prtline.dat" ]; then
  while IFS= read -r chunk || [ -n "$chunk" ]; do
    while [ -n "$chunk" ]; do
      printf '%s\n' "${chunk:0:$LRECL}" >> "$OUT/prtline.txt"
      chunk="${chunk:$LRECL}"
    done
  done < "$WORK/prtline.dat"
fi
exit 0
