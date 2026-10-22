# Shared helpers for the batch job scripts (sourced, not executed).
# A job works on a data directory: it reads the flat text files found
# there (same layout as app/data/ASCII) and writes its results to <dir>/out.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN_DIR="${BIN_DIR:-$ROOT/bin}"
export COB_LIBRARY_PATH="$BIN_DIR${COB_LIBRARY_PATH:+:$COB_LIBRARY_PATH}"
# Line sequential files keep their full record length (like the source data).
export COB_LS_FIXED=TRUE

job_init() {
  DATA_DIR="$(cd "${1:?usage: $0 <data-dir>}" && pwd)"
  OUT_DIR="$DATA_DIR/out"
  WORK_DIR="$DATA_DIR/work"
  rm -rf "$WORK_DIR"
  mkdir -p "$OUT_DIR" "$WORK_DIR"
}

# Flat text file -> fixed-length record file (RECFM=F) of LRECL bytes.
txt2fb() {
  local lrecl="$1" in="$2" out="$3" line
  : > "$out"
  while IFS= read -r line || [ -n "$line" ]; do
    printf "%-${lrecl}.${lrecl}s" "$line" >> "$out"
  done < "$in"
}

# Fixed-length record file -> flat text file, one record per line.
# Low-values (X'00') are shown as spaces.
fb2txt() {
  local lrecl="$1" in="$2" out="$3" rec
  : > "$out"
  while IFS= read -r -N "$lrecl" rec || [ -n "$rec" ]; do
    printf '%s\n' "$rec" >> "$out"
  done < <(tr '\000' ' ' < "$in")
}

# load_ksds <utility> <text file> <ksds file>
load_ksds() {
  DD_TXTFILE="$2" DD_KSDSFILE="$3" "$BIN_DIR/$1" LOAD
}

# unload_ksds <utility> <ksds file> <text file>  (low-values shown as spaces)
unload_ksds() {
  DD_TXTFILE="$3.tmp" DD_KSDSFILE="$2" "$BIN_DIR/$1" UNLOAD
  tr '\000' ' ' < "$3.tmp" > "$3"
  rm -f "$3.tmp"
}

# run_step <program> [args...] : run a program, capture SYSOUT, keep RC
run_step() {
  local pgm="$1"; shift
  set +e
  "$BIN_DIR/$pgm" "$@" >> "$OUT_DIR/sysout.txt" 2>&1
  STEP_RC=$?
  set -e
  echo "$STEP_RC" > "$OUT_DIR/rc.txt"
}
