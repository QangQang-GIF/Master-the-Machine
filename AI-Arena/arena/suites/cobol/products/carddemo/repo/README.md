# CardDemo - nightly batch

Batch programs of the CardDemo credit card management application, running on
Linux with GnuCOBOL 3.2. The online (CICS) part of CardDemo is not included.

## Jobs

| Job script          | Program  | What it does |
|---------------------|----------|--------------|
| `jobs/posttran.sh`  | CBTRN02C | Posts the daily transaction file: validates each transaction (card, account, credit limit, expiration date), writes rejects with a reason code to DALYREJS, updates account balances and transaction category balances, writes posted transactions to the transaction master. |
| `jobs/intcalc.sh`   | CBACT04C | Monthly interest: for every transaction category balance looks up the interest rate of the account's disclosure group (falls back to group `DEFAULT`), writes one interest transaction per balance and adds the interest to the account balance. Takes the run date as PARM (default `2022071800`). |
| `jobs/tranrept.sh`  | CBTRN03C | Daily transaction detail report for a date range, ordered by card, with account totals, page totals and a grand total. |

Each job works on a data directory that holds the input files (same names and
layouts as `app/data/ASCII`) and writes its results to `<dir>/out/`:

```
./build.sh                         # compile everything into ./bin
cp -r app/data/ASCII /tmp/run1     # data to work on
bash jobs/posttran.sh /tmp/run1    # results in /tmp/run1/out
```

`out/sysout.txt` is the program's DISPLAY output (job log) and `out/rc.txt` its
return code.

## Layout

```
app/cbl/        batch programs
app/cpy/        copybooks (record layouts)
app/data/ASCII/ sample data, one record per line
jobs/           job scripts (replace the JCL members POSTTRAN, INTCALC, TRANREPT)
tools/          platform utilities, see below
tests/          regression cases
```

Record layouts: account `CVACT01Y`, card cross reference `CVACT03Y`, category
balance `CVTRA01Y`, disclosure group `CVTRA02Y`, transaction type `CVTRA03Y`,
transaction category `CVTRA04Y`, transaction `CVTRA05Y`, daily transaction
`CVTRA06Y`, report lines `CVTRA07Y`. Signed numeric fields use the mainframe
zoned-decimal sign convention (last digit `{`=+0, `A`-`I`=+1..+9, `}`=-0,
`J`-`R`=-1..-9); the programs are compiled with `-fsign=EBCDIC` for that.

## Running on GnuCOBOL

- `SELECT ... ASSIGN TO <DDNAME>`: GnuCOBOL takes the file name from the
  environment variable `DD_<DDNAME>`; the job scripts set them the way the JCL
  DD statements did.
- VSAM KSDS files are GnuCOBOL indexed files. `tools/KSDS*.cbl` load them from
  the text files and unload them again (IDCAMS REPRO).
- Fixed-length sequential files (RECFM=F) are converted from/to one record per
  line by `jobs/lib.sh`.
- `tools/PARMRUN.cbl` passes a JCL `EXEC PARM=` string to programs that take
  one (CBACT04C).
- `tools/CEE3ABD.cbl` replaces the Language Environment abend service: it
  prints the abend code and ends the run with RC=16.
- The run date/time used by the programs can be fixed with
  `COB_CURRENT_DATE`, e.g. `COB_CURRENT_DATE='2022/07/18 10:00:00.00'`.

## Tests

`tests/cases/<name>/` holds regression cases: `cmd` (job and arguments),
`input/` (files replacing the sample data), `expected/` (expected results for
the files listed in `checks`).

```
./build.sh
bash tests/run_case.sh tests/cases/<name>    # one case, shows differences
bash tests/run_all.sh                        # all cases
```

The test runner uses `COB_CURRENT_DATE=2022/07/18 10:00:00.00`.

## License

Apache License 2.0, see `LICENSE` and `NOTICE`. Based on AWS CardDemo
(https://github.com/aws-samples/aws-mainframe-modernization-carddemo),
adapted to run with GnuCOBOL as described above.
