#!/usr/bin/env bash
# POSTTRAN - post the daily transaction file (CBTRN02C).
# Inputs : dailytran.txt cardxref.txt acctdata.txt tcatbal.txt
# Outputs: out/transact.txt  posted transactions (TRANSACT KSDS unload)
#          out/dalyrejs.txt  rejected transactions with reason trailer
#          out/acctdata.txt  account master after posting
#          out/tcatbal.txt   category balances after posting
#          out/sysout.txt, out/rc.txt
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
job_init "${1:-}"
W="$WORK_DIR"; : > "$OUT_DIR/sysout.txt"

txt2fb 350 "$DATA_DIR/dailytran.txt" "$W/dalytran.ps"
load_ksds KSDSXREF "$DATA_DIR/cardxref.txt" "$W/cardxref.ksds"
load_ksds KSDSACCT "$DATA_DIR/acctdata.txt" "$W/acctdata.ksds"
load_ksds KSDSTCAT "$DATA_DIR/tcatbal.txt"  "$W/tcatbal.ksds"

export DD_DALYTRAN="$W/dalytran.ps" DD_TRANFILE="$W/transact.ksds" \
       DD_XREFFILE="$W/cardxref.ksds" DD_DALYREJS="$W/dalyrejs.ps" \
       DD_ACCTFILE="$W/acctdata.ksds" DD_TCATBALF="$W/tcatbal.ksds"
run_step CBTRN02C

[ -f "$W/transact.ksds" ] && unload_ksds KSDSTRAN "$W/transact.ksds" "$OUT_DIR/transact.txt"
[ -f "$W/dalyrejs.ps" ]   && fb2txt 430 "$W/dalyrejs.ps" "$OUT_DIR/dalyrejs.txt"
unload_ksds KSDSACCT "$W/acctdata.ksds" "$OUT_DIR/acctdata.txt"
unload_ksds KSDSTCAT "$W/tcatbal.ksds"  "$OUT_DIR/tcatbal.txt"
exit 0
