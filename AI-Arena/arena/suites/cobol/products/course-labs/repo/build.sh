#!/usr/bin/env bash
# Compile the account programs and the data conversion utility with GnuCOBOL.
# Output goes to $BIN_DIR (default: ./bin).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$ROOT/bin}"
mkdir -p "$BIN_DIR"
for p in CBL0001 CBL0006 CBL0011 SRCHBIN SRCHSER; do
  cobc -x -o "$BIN_DIR/$p" "$ROOT/cbl/$p.cbl"
done
cobc -x -o "$BIN_DIR/ACCTCONV" "$ROOT/tools/ACCTCONV.cbl"
echo "build ok: $BIN_DIR"
