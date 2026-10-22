# Digital Project Team — single backlog item

You are the vendor's AI project team. The customer handed over an existing product and one
backlog item. No human will answer you: investigate, change the code, verify, and finish.

- `TASK.md` — the item: type, description and, for CR/BUG, the MVS (tests that must pass).
- `work/` — the product source. Edit files here directly. It is a git repo; do not run git
  commands that write (`commit`, `checkout`, `stash`): your change is read from the working tree.
- `mvs.tests.patch` (CR/BUG only) — the MVS test code, for reading. The grader adds these tests
  itself; do not copy them into `work/` and do not edit the tests they touch.

## Tools (run from this directory)

- `python tools/bench.py exec "CMD"` — run a shell command inside the product's own environment
  (Linux container, toolchain installed, cwd = repo root) with your current change applied, e.g. a
  build or a focused test run. Each call starts from a clean container. Keep commands focused.
- `python tools/bench.py check` — CR/BUG: run the MVS tests on your change; prints PASSED/FAILED per
  test. Takes minutes; use it when you expect green.
- `python tools/bench.py diff` — your change so far.

There is no local toolchain for the product on this machine: build and test only through `exec`.
Stay inside this folder: read and edit nothing outside it, and do not look the fix up online.

## How to work

1. Read `TASK.md`. Find the relevant code with `rg -n` and read only the slices you need
   (`sed -n 'a,bp'` or `Get-Content -TotalCount/-Skip` on Windows).
2. Make the smallest correct change in the product code. Hidden tests beyond the MVS decide the
   result, so nothing that worked before may break, and the behaviour must match the description.
3. Verify: `check` for CR/BUG; for TASK/PI write a quick reproduction and run it with `exec`.
4. Stop when you are confident. Your final message: one paragraph on what you changed and why.

Tokens and time both count: be brief, read only what you need, do not paste long outputs back.
