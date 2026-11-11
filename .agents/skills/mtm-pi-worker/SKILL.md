---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident inside the ProjectRuntime sandbox.
---

# PI (Production Incident — Urgent Fast Path)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS (ranked by completion order). Root submits immediately.

1. **Zero Browser / Zero Server Overhead**:
   - Production incidents are urgent backend/service fixes. **Do NOT start a web server or browser** unless a required evidence file is a screenshot.
2. **Pre-Fetched Incident Context**:
   - Check `metadata.company_context` in TaskEnvelope and `answers/<key>/evidence/` first. For impact figures (affected count, total amount, first bad timestamp) run `collect_logs(query, service, outPath=<match_root>/answers/<key>/evidence/logs-<service>.json)` and compute them with a script from that file. Read the KB counting rules first (what counts as affected, distinct by which id, which time window). Narrow the query to the incident (service plus the specific message/rule/release tokens) instead of collecting a whole service. If `complete` is false, never compute an answer from that file: split by service, release or message token and collect each part until every part is complete (`unique == total`); report the exact gap only if time runs out. Extra KB rules: one `call_company_tools` call.
3. **Surgical Fix & Sandbox Verification**:
   - Diagnose the root cause from `challenge/<key>.md`, the incident logs and the KB runbook; locate the code with `rg` on the service/handler names the logs show.
   - Apply the minimal fix, keep public names/arguments/schema, and add a test that reproduces the incident.
   - Run the targeted test with the project's own test command (as the README or other MVS files show) via `python3 "<metadata.project_runtime_path>" ... run -- <command>`, and save the log to `answers/<key>/evidence/`.
4. **Immediate Commit & Completion**:
   - Populate `answers/<key>/submit.json` with the computed figures, run `metadata.submit_schema_check`, `git add` & `git commit` all modified files and `answers/<key>/`, and write `worker_result.json` (`status: "READY_FOR_REVIEW"`) immediately so Root can submit first.
