# COBOL suite

COBOL backlog items for the local coding-agent benchmark, in a Harbor-like
task-directory format, run in the Docker image `arena-cobol:1`
(Debian bookworm, GnuCOBOL 3.2 built from the GNU release, python3, git, bash).

| type | count | source |
|------|------:|--------|
| TASK | 146 | COBOLEval (one task per problem, hidden tests = all upstream test callers) |
| BUG  | 12  | carddemo 9, course-labs 3 |
| PI   | 9   | carddemo 7, course-labs 2 |
| CR   | 5   | carddemo 3, course-labs 2 |

Gold patches: all BUG/PI/CR items, and 7 TASKs (`cobol-eval-000 002 013 015
030 035 042`, solutions in `coboleval/solutions/`).

## Layout

```
Dockerfile          arena-cobol:1
build.py            generator / validator / expected-output regeneration
items.json          BUG/PI/CR specs (mutation or reference change, texts, tests, MVS)
harness/test.sh     verifier for BUG/PI/CR tasks
coboleval/          verifier for TASKs (check.py = port of COBOLEval's comparison), solutions
products/<name>/    repo/ (product as delivered to the agent, reference version),
                    changes/<item>/ (CR reference implementations, full files),
                    gen_cases.py + cases/ (test inputs), expected/ (reference outputs),
                    SOURCE.md (provenance, license, adaptations)
```

## Usage

```
docker build -t arena-cobol:1 arena/suites/cobol
python arena/suites/cobol/build.py --out <dir>            # all 172 tasks
python arena/suites/cobol/build.py --validate <dir>       # oracle + nop in Docker
python arena/suites/cobol/build.py --validate <dir> --ids cobol-carddemo-01
python arena/suites/cobol/build.py --regen                # after changing items/products/cases
```

`--out` is pure Python and deterministic; it downloads the COBOLEval data once
from the pinned commit (sha256-checked) into `.cache/` (or `$ARENA_COBOL_CACHE`).
`--validate` mimics the grader: copy `repo/` to `/app`, `git init/add/commit`,
`git apply` the patch (gold = oracle, none = nop), copy `tests/` to `/tests`,
`bash /tests/test.sh`, read `/logs/verifier/reward.txt`; for BUG/CR it also
checks that the MVS `test_patch` applies and the MVS test names are reported.

## Task format

`meta.json`, `repo/` (base state), `tests/test.sh` + hidden test files,
`solution/patch.diff` (where a gold patch exists). BUG/PI/CR verifier: runs
`/app/build.sh`, installs the hidden cases into `/app/tests/cases`, runs each
with the case runner and compares the job outputs byte for byte with the
reference outputs; reward 1 only if the build succeeds and every case passes.
Test names are the case names (e.g. `posttran_edge`); `mvs.test_patch` adds
the MVS cases under `repo/tests/cases/`. The base repo already ships up to two
cases that pass before the change, so the agent sees the test format. TASK
verifier: compiles `src/<PROGRAM-ID>.cbl` with each caller
(`cobc -w -fformat=variable -x`), runs it and compares the result file using
COBOLEval's parse/compare rules (tests `test_0..n`).

## Provenance and licenses

- COBOLEval: https://github.com/BloopAI/COBOLEval, commit
  `0bb96c3114bb2bb28e221e9d6000614781f8609d`, `data/CobolEval.jsonl`, MIT.
  Fetched at build time, not vendored. The prompt's WORKING-STORAGE/LINKAGE
  order is swapped exactly as COBOLEval's own generator does
  (`swap_sections`), otherwise GnuCOBOL rejects the skeleton.
- carddemo: AWS CardDemo batch programs CBTRN02C, CBACT04C, CBTRN03C,
  Apache-2.0, see `products/carddemo/SOURCE.md`.
- course-labs: Open Mainframe Project COBOL Programming Course, CBL0001,
  CBL0006, CBL0011, SRCHBIN, SRCHSER, CC-BY-4.0, see
  `products/course-labs/SOURCE.md`.
- GnuCOBOL 3.2 (GPL/LGPL) is built inside the image from
  https://ftp.gnu.org/gnu/gnucobol/gnucobol-3.2.tar.xz (sha256-pinned).

## Caveats

- Some COBOLEval tests are inconsistent upstream (e.g. HumanEval/34 test_1
  expects 123 although that value is not in the transpiled input), so not
  every TASK is solvable; their nop runs still give 0.
- Reference behaviour = upstream programs as they are, including their own
  quirks (see SOURCE.md); BUG/PI expected outputs come from the unmodified
  programs, CR expected outputs from `changes/<item>/`.
- Outputs are made deterministic with `COB_CURRENT_DATE=2022/07/18 10:00:00.00`
  (set by the case runner).
