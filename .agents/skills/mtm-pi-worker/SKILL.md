---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident inside the ProjectRuntime sandbox.
---

# PI (Production Incident — Urgent Fast Path)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS. Root submits immediately.

1. **Zero Browser / Zero Server Overhead**:
   - Production incidents (`PI-01`, etc.) are urgent backend/service fixes. **Do NOT start a web server or Playwright browser** unless `.png` evidence is explicitly listed in `submit.json`.
2. **Pre-Fetched Incident Context**:
   - Check `metadata.company_context` in TaskEnvelope and `answers/<key>/evidence/` first. For impact figures (affected count, total amount, first bad timestamp) run `collect_logs(query, service, outPath=<match_root>/answers/<key>/evidence/logs-<service>.json)` and compute them with a script from that file. Read the KB counting rules first (what counts as affected, distinct by which id, which time window). Narrow the query to the incident (service plus the specific message/rule/release tokens) instead of collecting a whole service. If `complete` is false, never compute an answer from that file: split by service, release or message token and collect each part until every part is complete (`unique == total`); report the exact gap only if time runs out. Extra KB rules: one `call_company_tools` call.
3. **Surgical Fix & Sandbox Verification (< 2 Minutes Total)**:
   - Diagnose the root cause from `challenge/<key>.md` and incident logs (e.g., `refreshInvoiceStatus` after payment creation or voiding in `src/src/server/services/accounting/posting.ts`).
   - Apply the minimal surgical fix in `src/`.
   - Run targeted vitest verification via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- pnpm vitest run <test_file>`
   - Save verification logs to `answers/<key>/evidence/`.
4. **Immediate Commit & Completion**:
   - Populate `answers/<key>/submit.json` (`status: "DONE"`), `git add` & `git commit` all modified files and `answers/<key>/`, and write `worker_result.json` (`status: "READY_FOR_REVIEW"`) immediately so Root can submit for maximum speed bonus!
