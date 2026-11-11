---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident inside the ProjectRuntime sandbox.
---

# PI (Production Incident — Urgent Fast Path)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS (ranked by completion order). Root submits immediately.

1. **Zero Browser / Zero Server Overhead**:
   - Production incidents are urgent backend/service fixes. **Do NOT start a web server or browser** unless a required evidence file is a screenshot.
2. **Incident Figures (logs + KB)**:
   - Check `metadata.company_context` in TaskEnvelope and `answers/<key>/evidence/` first. Read the KB counting rules before counting (what counts as affected, distinct by which id, which time window, which statuses); extra KB rules: one `call_company_tools` call.
   - Get impact figures (affected count, total amount, first/last bad timestamp, ids) with ONE `collect_logs(query, service, outPath=<match_root>/answers/<key>/evidence/logs-<service>.json)` per query and compute them with a script from that file. Narrow the query with words every wanted entry has (service, rule id, release, message words). Log search matches whole words, so a date (`2025-10-17`) is NOT a day filter (it also matches minute/second 17): filter dates in your script from each entry's `ts`.
   - Never loop `search_logs` per transaction, per id or per day: it returns only the newest ~20 entries per call and burns the time budget. If you need detail per id, take it from the collected file.
   - If `complete` is false: collect the missing parts with narrower queries (other release/message words). Never wait for perfection: about 4 minutes after starting on the figures, compute a best estimate from what you have, add the missing count (`total - unique`) as a note, and move on.
   - Write each answer field into `answers/<key>/submit.json` as soon as you know it and commit right away (a partial answer scores, an empty template scores 0). Never leave the template empty or with placeholder values.
3. **Surgical Fix & Sandbox Verification**:
   - Diagnose the root cause from `challenge/<key>.md`, the incident logs and the KB runbook; locate the code with `rg` on the service/handler names the logs show.
   - Apply the minimal fix, keep public names/arguments/schema, and add a test that reproduces the incident.
   - Run the targeted test with the project's own test command (as the README or other MVS files show) via `python3 "<metadata.project_runtime_path>" ... run -- <command>`, and save the log to `answers/<key>/evidence/`.
4. **Immediate Commit & Completion**:
   - Make sure every field of `answers/<key>/submit.json` holds your computed (or best-estimate) values, run `metadata.submit_schema_check`, `git add` & `git commit` all modified files and `answers/<key>/`, and write `worker_result.json` (`status: "READY_FOR_REVIEW"`) immediately so Root can submit first.
