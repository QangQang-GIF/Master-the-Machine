---
name: mtm-task-worker
description: Complete an announced MTM TASK item with required checks and artifacts inside the ProjectRuntime sandbox.
---

# TASK (Engineering Task — Sandbox Fast Path)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. **Mandatory Sandbox Commands**:
   - Run all CLI/test/check commands via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <cmd>`
   - Only use `project_runtime.py ... session` if `submit.json` explicitly requires `.png` screenshot evidence!

2. **Fast paths by TASK nature** (decide from the brief and the required evidence, never from the item number):
   - **Trace / compute / answer fields** (no screenshot required): **no browser, no web server.** Trace or implement the logic, run the targeted test via `project_runtime.py ... run`, write the exact answer fields into `answers/<key>/submit.json` and the calculation log into `answers/<key>/evidence/`, commit.
   - **Data computation with an official fixture**: use `answers/<key>/evidence/<key.lower()>-test-data.json` (pre-fetched from the QA store; never overwrite it with synthetic data) and compute every field the brief and `challenge/<KEY>-submit.schema.json` require with a script; save the script output as evidence.
   - **Check runners**: if `challenge/<KEY>-run-checks.*` exists, run it as its header says via `project_runtime.py ... run`, and map `testResults` 1-to-1 to the evidence files the template requires; capture required screenshots in a single `session` pass.

3. **Specification items (write tests / implement a spec)**:
   - Every normative sentence is a rule, including the general statements in the introduction (e.g. "every rule holds for any number of lines in one import and any number of transactions in one change"); hidden checks are named after them. List them before writing cases.
   - For each entry point include at least one high-volume case and boundary cases at both ends of every window or limit. Size the volume case from the code, not by feel: find the page/chunk/batch constants on the path (`pageCount`, `chunk`, `batchSize`, `LIMIT`, `slice(`...) and go well past the largest (at least 2x, e.g. 1200 rows when a page is 500), in ONE call. While reading paging code, check each slice's start and end indices (64FIF7VM: `terms.slice(i, pageCount)` instead of `slice(i, i + pageCount)` dropped everything after row 500; a 220-row case missed it). If a volume case fails, fix the application, not the case.
   - **Mutation check (suites graded against broken builds):** for every rule a case claims, temporarily break the application for that rule only (e.g. match on the payee before rules instead of after), run the suite, and confirm at least one case FAILS; then restore the file (`git checkout -- <file>`) and confirm the suite passes again. Never commit a mutant. A case that still passes on the mutant must be redesigned so the correct and the broken behaviour give different observable results (64FIF7VM: the ruled-payee line came after an unruled one, so nearest-fallback produced the same matches and the broken build went undetected; putting the ruled line first exposes it). Record each rule, its mutant and the failing case in `answers/<key>/evidence/mutation-check.md`.

4. **Reading values from images (vouchers, screenshots, scans)**:
   - Never trust one look at a digit string. Make zoomed crops of every region that holds an id or a number (e.g. Pillow if installed, or Playwright: open the image in a page at 3x zoom and screenshot a clip) and read each number digit by digit; read it a second time from a different crop. If the two reads differ, read again until two agree.
   - Copy every number exactly as printed into `answers/<key>/evidence/` (one line per value with where it appears) before computing; compute from those lines with a script and re-check each result against the image.

5. **"What the application does" items (datasets, manifests, expected outputs; 39PSWT5C TASK-07)**:
   - When the brief says the grader runs your files through the issued, unmodified source (imports a statement pack, replays a seed, parses an export), never predict the results by hand or with your own parser. Build a harness that calls the SAME entry points the grader names (e.g. the file parser and `reconcileTransactions` with the stated import settings, one fresh empty account per manifest account, files in manifest order) on an unmodified copy of the source (`git stash`/`git worktree add` of the base revision, never your edited tree), and write every expected value (parsed fields, added counts, final count and balance) from what the harness returns.
   - Generate the input files with a script, then run a second script that checks every listed requirement (volume, mix, overlap, recurrence, special cases, payee rules, size limit) against the files and the harness output, and save both scripts' output in `answers/<key>/evidence/`. Fix the generator until every requirement passes; commit the dataset only then.
   - Keep the item's source unchanged when the brief says so; commit only the dataset, `submit.json` and evidence.
