---
name: mtm-pi-worker
description: Production Incident (PI) worker - evidence-first forensics over the official company logs/KB, exact answer fields computed by script, then a minimal verified fix. Speed and exactness both score.
---

# PI (Production Incident) — evidence first, numbers by script

Follow `mtm-init` and `mtm-worker`. A PI is ranked by the time Game Master receives the team's ONE submission,
and every answer field is compared exactly. A fast wrong answer scores 0, so never guess; but do not polish.
The answer fields usually carry more points than the fix: settle them first, then fix the code.

## 0. Read the brief exactly (first 2 minutes)
- Read `challenge/<key>.md`, `challenge/<key>-submit.json` and `<key>-submit.schema.json`.
- Write down each requested field and its exact definition (for example "first entry in log order
  (`context.seq`) over the whole log", "distinct transactions while a faulty release was live",
  "sum of absolute amountMinor"). Each word changes the number.
- The brief may warn that not every alarm is related. Treat every alarm as a hypothesis to test, never as the answer.
- Check **Out of scope**: some PIs forbid changing the application source (report only); others require a fix.
  Check for a deadline: a PI can close 15 minutes after release.
- Note each field's JSON type and form: release names, component names, flag names, ids "exactly as found";
  set fields (e.g. affected invoices/schedules) must be complete and contain nothing else.
- `kb_search` the company's **incident report / incident analysis rules**: they define names, scope and what counts
  (e.g. retries once, only while a faulty release was live). Apply them literally in the script.

## 1. Collect complete evidence with tools (no manual paging)
- `kb_search` the incident topic and every rule/article id the brief or logs mention; save the articles.
  Release notes / release calendar articles map releases to code changes.
- **Never page `search_logs` by hand.** It returns only ~20 newest entries per call. Use the MCP tool
  `collect_logs(query, service, outPath)`: it narrows by timestamp until every partition is complete and writes
  ALL entries, ordered by `context.seq`, to one JSON file. Check `complete: true` before trusting a count.
  Save files under `answers/<key>/evidence/` (for example `logs-deploy.json`, `logs-<service>.json`).
- Collect at least: the deploy history (`service=deploy`: completed, rollback, started) and the full log of the
  service that makes the faulty decision (for example the rule engine). Do not fetch unrelated services.

## 2. Analyse with a script, not by reading
Write `answers/<key>/evidence/analyze.py` that loads the collected files and computes:
1. The live release at every entry: the last `deploy.completed`/`deploy.rollback` before it in `seq` order.
2. For each decision entry, the CORRECT outcome from the documented rule (KB), and whether the logged
   outcome differs. Implement the documented rule literally from the KB text; cite the article id in a comment.
3. `rootCauseRelease`: the release live when wrong outcomes start, confirmed by its release notes touching the
   faulty logic, and by wrong outcomes stopping where that code is not live. A release that only coincides with
   an alarm is not the root cause.
4. Every requested field exactly as defined in step 0 (first wrong entry in `seq` order over the whole log,
   distinct ids, sums of absolute values, only while a faulty release was live, retries counted once, ...).
Save the script output to `answers/<key>/evidence/analysis.json`. Fill `submit.json` only from that output.
If coverage is incomplete (`complete: false`), say so in the answer and keep collecting; never write a
placeholder number as if it were measured.

## 3. Fix the code (minimal, verified) — only when the brief asks for a fix
- Locate the faulty logic named by the release notes and the analysis (search `src/` for the function that
  implements the documented rule). Make the minimal change that makes the code follow the KB rule.
- Add or update a focused unit test that reproduces a logged wrong decision and passes after the fix.
- Run only the targeted test and the type check through the project runtime:
  `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <test command>`
  Do not start a web server or browser unless the submit template requires a screenshot.

## Priorities for speed and accuracy
- Speed comes from tools and scripts, not from skipping checks: one `collect_logs` per service instead of paging,
  one analysis script instead of reasoning over pages.
- Order: brief → KB rules/release notes → complete logs → script → fields → (fix) → hand off. Never start the fix
  before the fields are computed: fields usually carry most of the points.
- Time-box: if coverage is still incomplete after ~10 minutes, narrow the collection (service, message, release
  words) instead of reading entries by hand.

## 4. Hand off fast
- `submit.json`: exact strings and integers (no prefixes, quotes, spaces or explanations), `incidentRecord`
  summarising impact, timeline, root cause, containment, recovery, prevention. `status: "DONE"` only when every
  field is computed from complete evidence and the fix test passes; otherwise an honest `"PARTIAL"`.
- Commit `src/` changes, tests and `answers/<key>/` (evidence + script + analysis), write `worker_result.json`
  immediately (test commands with real exit codes). Root submits as soon as the policy says SUBMIT.
- If Root relays `DEADLINE_FLUSH_REQUESTED`, stop and hand off what is verified now.
