# course-labs - provenance

- Upstream: https://github.com/openmainframeproject/cobol-programming-course
- Commit: `780b1c9624e066b9d784a738896e493282c5fbf4`
- License: CC-BY-4.0 (`repo/LICENSE` copied unchanged). Copyright Contributors
  to the COBOL Programming Course.

## Taken from upstream

| repo path | upstream path | change |
|---|---|---|
| `cbl/CBL0001.cbl`, `cbl/CBL0006.cbl`, `cbl/CBL0011.cbl` | `COBOL Programming Course #2 - Learning COBOL/Labs/cbl/*.cobol` | CRLF -> LF; period added after the `PROGRAM-ID` name (GnuCOBOL rejects the paragraph without it) |
| `cbl/SRCHBIN.cbl`, `cbl/SRCHSER.cbl` | same folder | CRLF -> LF |
| `data/accounts.txt` | `.../Labs/data/data` | EBCDIC (cp037) record file with packed limit/balance converted to a readable text layout; same 45 accounts and values |

## Written for this suite

`tools/ACCTCONV.cbl` (text -> 170-byte ACCTREC records with COMP-3 fields, the
layout the programs read), `jobs/run.sh` (DD names via `DD_ACCTREC` /
`DD_PRTLINE`, print file -> text lines), `build.sh`, `README.md`,
`tests/run_case.sh`, `tests/run_all.sh`, `gen_cases.py` and the generated
`cases/`. `expected/` is produced by `build.py --regen`.

Course #4 (EMPPAY, DEPTPAY) is not used: those programs only work on hard-coded
values.
