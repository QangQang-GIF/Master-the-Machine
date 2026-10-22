# Backlog item — lean worker

One backlog item, no human. `TASK.md` is the item; `work/` is the product (a git repo: never run git
commands that write). For CR/BUG, `mvs.tests.patch` is the test code the grader adds; do not copy it
into `work/` and do not edit the tests it touches.

Tools (run from this directory; there is no local toolchain for the product):
- `python tools/bench.py exec "CMD"` — one shell command inside the product's container, with your
  current change applied (cwd = repo root). Fresh container each call; keep commands focused.
- `python tools/bench.py check` — CR/BUG: run the MVS tests; prints PASSED/FAILED per test. Minutes.
- `python tools/bench.py diff` — your change so far.

Token and time rules (they are scored):
1. Do not narrate: no message before the final one. This file and the item are already in your context;
   never read AGENTS.md or TASK.md with a command.
2. Locate with `rg -n <pattern> work/<dir>` (never `rg --files` on the whole tree, never `ls -R`).
3. Read slices, not files: `rg -n` with `-A/-B` for context, or `sed -n 'a,bp'` (`Get-Content
   -TotalCount`/`-Skip` on Windows); at most ~120 lines per read. Never print a whole file.
4. Make the smallest correct change. Hidden tests beyond the MVS decide the result, so match the
   description exactly and keep existing behaviour.
5. Verify once: `check` for CR/BUG; one `exec` of a focused test or reproduction for TASK/PI. A second
   run only if the first fails; stop after the third.
6. When verified, stop at once. Final message: one sentence on what changed and why.
