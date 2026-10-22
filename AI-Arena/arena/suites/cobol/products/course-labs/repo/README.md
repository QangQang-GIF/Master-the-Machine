# Account reports

Batch report programs over the customer account file (ACCTREC), running on
Linux with GnuCOBOL 3.2.

| Program   | Output |
|-----------|--------|
| `CBL0001` | Account listing: account number, limit, balance, names, comments. |
| `CBL0006` | Financial report (header with run date, one line per account) and the number of clients from Virginia. |
| `CBL0011` | Financial report with last names in mixed case and a totals line for limits and balances. |
| `SRCHBIN` | Loads the accounts into a table and looks up account 18011809 (binary search). |
| `SRCHSER` | Loads the accounts into a table and looks for last name ROOSEVELT (serial search). |

## Data

ACCTREC is a fixed-length file of 170-byte records (layout in the FD of each
program; limit and balance are packed decimal `S9(7)V99 COMP-3`). It is built
from the text file `data/accounts.txt` by `tools/ACCTCONV.cbl`, one account per
line:

```
cols   1-8   account number
cols   9-19  limit    (+9999999.99)
cols  20-30  balance  (+9999999.99)
cols  31-50  last name       cols  51-65  first name
cols  66-90  street          cols  91-110 city
cols 111-125 state           cols 126-132 reserved
cols 133-182 comments
```

## Build and run

```
./build.sh                                   # compile into ./bin
mkdir /tmp/r && cp data/accounts.txt /tmp/r/
bash jobs/run.sh /tmp/r CBL0011              # results in /tmp/r/out
```

`out/prtline.txt` is the report (PRTLINE, one print record per line),
`out/sysout.txt` the DISPLAY output and `out/rc.txt` the return code. Files are
assigned through `DD_<DDNAME>` environment variables (`DD_ACCTREC`,
`DD_PRTLINE`), set by `jobs/run.sh`. The report date can be fixed with
`COB_CURRENT_DATE`, e.g. `COB_CURRENT_DATE='2022/07/18 10:00:00.00'`.

## Tests

`tests/cases/<name>/`: `cmd` (what to run), `input/accounts.txt` (data),
`expected/` (expected results of the files listed in `checks`).

```
./build.sh
bash tests/run_case.sh tests/cases/<name>
bash tests/run_all.sh
```

## License

The programs come from the Open Mainframe Project COBOL Programming Course
(https://github.com/openmainframeproject/cobol-programming-course) and are
licensed under CC BY 4.0 (see `LICENSE`). Copyright Contributors to the COBOL
Programming Course. Adapted for GnuCOBOL: a period was added after the
PROGRAM-ID names, and the sample data was converted from EBCDIC to the text
layout above (converted to ACCTREC by `tools/ACCTCONV.cbl`).
