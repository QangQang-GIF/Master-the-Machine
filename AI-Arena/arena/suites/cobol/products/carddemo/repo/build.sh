#!/usr/bin/env bash
# Compile the CardDemo batch programs and utilities with GnuCOBOL.
# Output goes to $BIN_DIR (default: ./bin).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="${BIN_DIR:-$ROOT/bin}"
mkdir -p "$BIN_DIR"
COBFLAGS=(-fsign=EBCDIC -I "$ROOT/app/cpy")

# Batch programs run directly (EXEC PGM=...)
for p in CBTRN02C CBTRN03C; do
  cobc -x "${COBFLAGS[@]}" -o "$BIN_DIR/$p" "$ROOT/app/cbl/$p.cbl"
done
# Batch programs that take a JCL PARM are built as modules and run via PARMRUN
for p in CBACT04C; do
  cobc -m "${COBFLAGS[@]}" -o "$BIN_DIR/$p.so" "$ROOT/app/cbl/$p.cbl"
done

# Utilities
for t in KSDSACCT KSDSXREF KSDSTCAT KSDSDISC KSDSTTYP KSDSTCTG KSDSTRAN PARMRUN; do
  cobc -x -o "$BIN_DIR/$t" "$ROOT/tools/$t.cbl"
done
cobc -m -o "$BIN_DIR/CEE3ABD.so" "$ROOT/tools/CEE3ABD.cbl"
echo "build ok: $BIN_DIR"
