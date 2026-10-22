# AI Arena benchmark suite

One catalog over several public coding-agent benchmarks plus our own COBOL suite, with one
command to install, one to evaluate an agent and one to report. Items are typed like the MTM
backlog (BUG / CR / TASK / PI), graded by each benchmark's own hidden tests in Docker.

| Suite | Source | Grader |
|---|---|---|
| `swe-verified` | SWE-bench Verified (HF `SWE-bench/SWE-bench_Verified`) | eval script + `swebench` log parsers |
| `swe-multilingual` | SWE-bench Multilingual (HF `SWE-bench/SWE-bench_Multilingual`) | same |
| `swe-pro` | SWE-bench Pro public V2 (`scaleapi/SWE-bench_Pro-os` v2/tasks) | Harbor `tests/test.sh` → reward |
| `multi-swe` | Multi-SWE-bench (HF `ByteDance-Seed/Multi-SWE-bench`) | `/home/fix-run.sh` + `multi_swe_bench` parsers |
| `cobol` | COBOLEval + legacy batch programs ([suites/cobol](suites/cobol)) | Harbor-style `tests/test.sh` on GnuCOBOL |

An issue counts once: Multi-SWE-bench's Python part is SWE-bench Verified and some of it overlaps
Multilingual, so those copies are left out of the catalog. 56 Kotlin issues whose repos have no
harness class in `multi_swe_bench` are skipped.

Docs (tiếng Việt): [docs/SETUP.md](docs/SETUP.md) cài đặt và thêm agent mới, [docs/BENCHMARK.md](docs/BENCHMARK.md)
đặc tả bộ benchmark (nguồn, set, thống kê, cách chấm), [docs/VS_CONTEST.md](docs/VS_CONTEST.md) đối chiếu với luật thi.

What the repo carries and what it does not: the catalog sources, Docker images, run folders and
results (`data/sources`, `data/runs`, `data/results`) are **not** committed: everyone measures on their
own machine. The preset sets (`data/sets/*.json`) **are** committed, so every machine evaluates the same
items. Our measured numbers are in `docs/REPORT_*.md` and `docs/OPTIMIZATION.md`.

Tested on Windows 11 (Git Bash / PowerShell) with Docker Desktop (WSL 2). On macOS/Linux replace
`.venv/Scripts/python` with `.venv/bin/python`; the Codex Windows sandbox key is only added on Windows.

## Install

Needs Docker Desktop (amd64) and Codex CLI logged in.

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv swebench datasets pip
.venv/Scripts/python -m pip install multi-swe-bench   # uv refuses this wheel on Windows (case-clashing paths)
.venv/Scripts/python -m arena setup                   # ~2 GB of metadata (Multi-SWE-bench full is 1.8 GB); no images yet
```

`setup --msb mini` uses the 400-issue Multi-SWE-bench mini instead. `setup --suites cobol` redoes one
suite; `setup --suites` alone rebuilds the catalog. Docker images are pulled only for items you run.

## Use

```bash
.venv/Scripts/python -m arena stats                          # issues per suite x type x language -> data/stats.md
.venv/Scripts/python -m arena sample --name dev --per-suite 4 # fixed eval set, same items for every agent
.venv/Scripts/python -m arena check --set dev                # infra: reference patch must pass, empty patch must fail
.venv/Scripts/python -m arena eval --agent codex-medium --set dev
.venv/Scripts/python -m arena report --set dev               # agent x suite table -> data/report.md
```

### Preset sets: xs, s, m, full

Four nested sets, picked with `--size` on `sample`, `check`, `eval` and `report`. Every size keeps a
quota of each FPT stack (Java, .NET, React, Angular, COBOL: the company's main stacks, so the likeliest
match products) plus a quota of other stacks; both are balanced over the item types, and app repos are
preferred to libraries. A stack with no items in the catalog (today .NET and Angular) leaves its slot
empty and `sample` says so.

| Size | Per FPT stack | Other stacks | Items (today) | Timeout/item | Use |
|---|---|---|---|---|---|
| `xs` | 1 (no Java: slow builds) | 0 | 2 (4 once .NET/Angular exist) | 15 min | is the pipeline or prompt broken? |
| `s` | 1 | 5 | 8 (10) | 15 min | did a prompt or model change hurt? |
| `m` | 2 | 10 | 16 (20) | 30 min | fill `p`, `t` in `routing.json` |
| `full` | 4 | 20 | 32 (40) | 30 min | settle model and effort before the real match |

The sets nest (`xs` ⊂ `s` ⊂ `m` ⊂ `full`), so after `eval --size s` an `eval --size m` only runs the
extra items. Items whose reference patch failed `check` (`data/results/oracle.jsonl`) are never
picked, nor the items in `cli.EXCLUDED_ITEMS` (e.g. logstash-15928, whose MVS check takes >12 min);
the seed fixes the choice.

```bash
.venv/Scripts/python -m arena sample --size s                # writes all four sets, prints s
.venv/Scripts/python -m arena check --size xs                 # infra only, no Codex tokens
.venv/Scripts/python -m arena eval --agent codex-medium --size s
.venv/Scripts/python -m arena report --size s
```

`stats` prints a per-stack table. Stacks are derived from the catalog when it is loaded
(`core.stack_of`: React and Angular by repo, the rest by language), so no `setup` rerun is needed
after editing the stack lists in `core.py`.

Agents are defined in [agents.json](agents.json) (`runner: codex`, model, effort). `oracle` and `nop`
are built in for `check` and never appear in reports. `eval` resumes: graded items are skipped
(`--redo` grades again). `sample` round-robins over language × type inside each suite, so a small set
still spreads across stacks; `--types BUG CR` limits the types.

## How an item runs

1. The repo at the base commit is copied out of the item's image into `data/runs/<agent>/<item>/work`
   (fresh git repo, no upstream history), with `TASK.md` (PI gets only the first paragraph; CR/BUG get
   the MVS test names and `mvs.tests.patch`) and [agent/AGENTS.md](agent/AGENTS.md).
2. Codex runs sandboxed in that folder. It cannot use Docker, so `tools/bench.py exec "CMD"` runs
   commands in the product's container and `tools/bench.py check` runs the MVS tests (only MVS statuses
   come back, not the hidden tests).
3. `git diff` of `work/`, minus files owned by the test patch, is graded by the suite's hidden tests.
   Result rows go to `data/results/<agent>.jsonl`, grader logs to `data/results/logs/`.

## Metrics (`report`)

When several agents are shown, they are compared on the items all of them have done
(`--all-items` turns that off). Besides the score tables, `report` prints:

| Metric | Meaning |
|---|---|
| Median / P90 min | agent wall time per item (grading excluded) |
| 1st edit min | time until the agent first changed a file (how fast it localises the problem) |
| Timeouts | items cut at `--timeout-min` |
| Tokens/item | input + output tokens |
| Billable/item, /resolved | uncached input + output: what drives Codex credits (cached input is cheap) |
| USD/resolved | only if the agent has `"price": {"input": .., "cached_input": .., "output": ..}` (USD per 1M) in agents.json |
| Pts/min, Pts/1M tok | MTM points earned per agent-minute and per million billable tokens |
| MVS green→failed | share of items where the agent's last MVS check was green but hidden tests failed (over-trusting MVS) |
| Cmds/item | shell commands per item |

A per-type table (median minutes · billable tokens) and a routing table follow: for each item type,
the agent with the most expected points per minute and the cheapest agent within 10 points of the
best resolve rate. Routing needs at least 2 items of that type per agent.

Score per cell: resolved/attempted and an MTM score out of 1,000 weighted by type
(PI 250, CR 300, BUG 200, TASK 150). Item types come from keyword rules (`core.classify`);
SWE-bench Pro uses its own issue labels (`*_bug`, `*_feat`, `*_enh`).

## Match score (Game Rules)

`report` also scores every agent the way the Game Master scores a team (`metrics.match_table`):

- **Official** = sum of the points of PASSED items (PI 250, CR 300, BUG 200, TASK 150). FAILED,
  unfinished, or submitted after the item's deadline = 0. **/1000** normalises to the set's max points.
- **PI** points by speed rank among the agents shown, timed from reveal (agent start) to graded PASSED:
  fastest 100%, 2nd 90%, 3rd 75%, others 60%. The agents in a report play the roles of the teams in a match.
- **Deadlines**: every second BUG/CR/TASK item of a preset set has one (12/15/10 minutes from reveal,
  `cli.DEADLINE_MIN`), stored in the set file; PI never does. A fix submitted later counts 0 ("Late").
- **Tie-breakers** (TB): +10 per 1% faster last completion, +10 per 1% fewer billable tokens, both
  against the average of the agents shown. They only decide a tie; the columns are shown for every agent.
- Not modelled: the 40-minute wall clock across items (items run one at a time, each with its own
  timeout) and the -50 computer-access penalty.

`agents.json` ships `agent-test-0` (gpt-6.1-sol, effort low) and `agent-test-1` (same model, effort
high) as a deliberately different pair for trying the tables on `--size xs`.

## Caveats

- Codex's Windows sandbox can read the whole disk, including `data/sources` (which holds reference
  patches). AGENTS.md keeps it on its own folder; the sandbox does not enforce that.
- Some images are large (SWE-bench Pro, Multi-SWE-bench TS/Java: several GB each). Keep sets small.
