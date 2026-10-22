#!/usr/bin/env bash
# TRANREPT - daily transaction detail report (SORT + CBTRN03C).
# Inputs : transact.txt (transaction master, one record per line)
#          dateparm.txt (one line: 'YYYY-MM-DD YYYY-MM-DD' start/end date)
#          cardxref.txt trantype.txt trancatg.txt
# Outputs: out/tranrept.txt  report (133 byte lines)
#          out/sysout.txt, out/rc.txt
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
job_init "${1:-}"
W="$WORK_DIR"; : > "$OUT_DIR/sysout.txt"

read -r START END _ < "$DATA_DIR/dateparm.txt"
# SORT step: keep transactions processed within the date range
# (TRAN-PROC-TS, pos 305 len 10) and order them by card number
# (pos 263 len 16).
awk -v s="$START" -v e="$END" \
    '{ d = substr($0, 305, 10); if (d >= s && d <= e) print }' \
    "$DATA_DIR/transact.txt" \
  | LC_ALL=C sort -s -t $'\001' -k1.263,1.278 > "$W/transact.daly.txt"

txt2fb 350 "$W/transact.daly.txt" "$W/transact.daly"
txt2fb 80  "$DATA_DIR/dateparm.txt" "$W/dateparm"
load_ksds KSDSXREF "$DATA_DIR/cardxref.txt" "$W/cardxref.ksds"
load_ksds KSDSTTYP "$DATA_DIR/trantype.txt" "$W/trantype.ksds"
load_ksds KSDSTCTG "$DATA_DIR/trancatg.txt" "$W/trancatg.ksds"

export DD_TRANFILE="$W/transact.daly" DD_CARDXREF="$W/cardxref.ksds" \
       DD_TRANTYPE="$W/trantype.ksds" DD_TRANCATG="$W/trancatg.ksds" \
       DD_DATEPARM="$W/dateparm" DD_TRANREPT="$W/tranrept"
run_step CBTRN03C

[ -f "$W/tranrept" ] && fb2txt 133 "$W/tranrept" "$OUT_DIR/tranrept.txt"
exit 0
