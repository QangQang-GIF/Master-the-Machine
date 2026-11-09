---
name: mtm-bug-worker
description: BUG worker - read the brief and grading first, reproduce with the official MVS file, fix the cause at the named module (keeping its API), add tests for the hidden boundary cases, capture every required evidence item.
---

# BUG — reproduce, fix the cause, prove every scenario

Follow `mtm-init`, `mtm-worker`, `mtm-verification`, `mtm-evidence`. Root owns Start Work, push and Submit.
Lessons from graded matches: a scenario reported `NOT_RUN` scores 0 even when the fix works; about half of a BUG's
points come from organizer hidden tests on boundaries; the grader restores the original test/config files and runs
the whole suite and the type check itself.

## 1. Read before touching code (first 2–3 minutes)
Read in this order and write down what you find:
1. `challenge/<key>.md`: **Requirements** (the module/function to fix and the names that must not change),
   **Acceptance criteria**, **Grading** (MVS replay? hidden tests? browser replay? reported figures?),
   **Out of scope**, the deadline and the maximum score.
2. `challenge/<key>-minimum-verification-scenarios.md`: every TC id, its data, expected result, the exact
   evidence file names, and the exact command that writes each log.
3. `challenge/<key>-submit.json` + schema: rows to keep, extra fields to fill (figures, records).
4. Every other provided file (`*-mvs.test.ts`, `*-seed.ts`, `*-report.ts`, `*-run-checks.mjs`, check scripts)
   and, for an app that must be built/served, the commands in `env-require/README.md` ("Commands during the match").
5. If the brief cites company standards, a ticket or "the knowledge base": `kb_search` them and save the articles.
   The standard, not your intuition, defines correct behaviour; the hidden tests follow it to the letter.

## 2. Reproduce
- Copy the official `*-mvs.test.ts` to the location the MVS file names, unchanged. Run it on the unfixed code:
  it must fail for the reported reason. That failure is your reproduction.
- Official data: get it with `fetch_fixture` / the QA test data store and save it **unchanged** under the exact
  evidence name. A fixture that is not the store's file zeroes every scenario that uses it. Never write your own.
- Bugs with several causes (tickets, imports, transfers): list each symptom and find each cause separately.

## 3. Fix the cause, not the example
- Change the module the Requirements name. Keep module paths, exported names, signatures, handler names,
  database schema and migrations exactly; hidden tests call them directly.
- Implement the rule for every case the brief/KB describes (boundaries such as "on the due date", "exactly 7 days",
  "at the end of any window", currencies, empty/zero/negative, repeated operations). Hard-coding examples fails.
- Keep behaviour the brief says must not change (other statuses, other currencies, other drop positions...).

## 4. Tests that the grader will count
- Add NEW test files only (where the brief says, e.g. `src/tests/` or next to the module); never edit, skip or
  weaken existing tests. Cover each acceptance criterion and each boundary, including "unchanged" behaviour.
- Run the targeted tests, the whole existing suite and the type check through ProjectRuntime
  (`python3 "<metadata.project_runtime_path>" ... run -- <command from the MVS/README>`). Use the project's own
  commands (`yarn`/`node .../vitest.mjs` or `pnpm`, as the files say); do not assume a package manager.

## 5. Evidence for every TC row (cheap points)
- Write each log with the exact command and path from the MVS file.
- Screenshots: from the app you built and served yourself (README commands), at the viewport the MVS states
  (desktop ≥ 1280×720 unless told otherwise), named exactly as required. A required PNG missing turns the row into
  `NOT_RUN` = 0 points, so capture screenshots before polishing anything else.
- Reported figures (balances, counts, lists): compute them by running the fixed application/scripts on the official
  data, never by hand.

## 6. Hand off
- `submit.json`: keep the template rows/fields; `PASS` only when the check really passed and its evidence exists;
  honest `FAIL`/`NOT_RUN` otherwise; `status` DONE only when every row is PASS. Fill the engineering record
  (observed vs expected, root cause, change, verification).
- Commit source, new tests, MVS test copy, evidence and `submit.json`; remove temporary helper copies the brief says
  not to commit. Write `worker_result.json` with the real test commands and exit codes.
- Short deadline (≤ 20 min): reproduce → fix → MVS log → submit.json first; extra tests and polish only after.
