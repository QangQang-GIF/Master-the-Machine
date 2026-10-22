#!/usr/bin/env bash
# INTCALC - monthly interest calculation (CBACT04C, PARM=<run date>).
# Usage  : jobs/intcalc.sh <data-dir> [parm]   (parm default: 2022071800)
# Inputs : tcatbal.txt cardxref.txt acctdata.txt discgrp.txt
# Outputs: out/systran.txt   generated interest transactions
#          out/acctdata.txt  account master after interest posting
#          out/sysout.txt, out/rc.txt
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
job_init "${1:-}"
PARM="${2:-2022071800}"
W="$WORK_DIR"; : > "$OUT_DIR/sysout.txt"

load_ksds KSDSTCAT "$DATA_DIR/tcatbal.txt"  "$W/tcatbal.ksds"
load_ksds KSDSXREF "$DATA_DIR/cardxref.txt" "$W/cardxref.ksds"
load_ksds KSDSACCT "$DATA_DIR/acctdata.txt" "$W/acctdata.ksds"
load_ksds KSDSDISC "$DATA_DIR/discgrp.txt"  "$W/discgrp.ksds"

export DD_TCATBALF="$W/tcatbal.ksds" DD_XREFFILE="$W/cardxref.ksds" \
       DD_ACCTFILE="$W/acctdata.ksds" DD_DISCGRP="$W/discgrp.ksds" \
       DD_TRANSACT="$W/systran.ps"
run_step PARMRUN CBACT04C "$PARM"

[ -f "$W/systran.ps" ] && fb2txt 350 "$W/systran.ps" "$OUT_DIR/systran.txt"
unload_ksds KSDSACCT "$W/acctdata.ksds" "$OUT_DIR/acctdata.txt"
exit 0
