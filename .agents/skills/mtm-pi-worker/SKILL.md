---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident inside the ProjectRuntime sandbox.
---

# PI (Production Incident — Urgent Fast Path)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS. Root submits immediately.

1. **Zero Browser / Zero Server Overhead**:
   - Production incidents (`PI-01`, etc.) are urgent backend/service fixes. **Do NOT start a web server or Playwright browser** unless `.png` evidence is explicitly listed in `submit.json`.
2. **Pre-Fetched Incident Context**:
   - Check `metadata.company_context` in the full TaskEnvelope file (`full_envelope_path`) and `answers/<key>/evidence/` first. If additional production logs or KB rules are needed, query via `runner.control company`.
3. **Surgical Fix & Sandbox Verification (< 2 Minutes Total)**:
   - Diagnose the root cause from `challenge/<key>.md` and incident logs (e.g., `refreshInvoiceStatus` after payment creation or voiding in `src/src/server/services/accounting/posting.ts`).
   - Apply the minimal surgical fix in `src/`.
   - Run targeted vitest verification via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- pnpm vitest run <test_file>`
   - Save verification logs to `answers/<key>/evidence/`.
4. **Immediate Commit & Completion**:
   - Populate `answers/<key>/submit.json` (`status: "DONE"`), `git add` & `git commit` all modified files and `answers/<key>/`, and write `worker_result.json` (`status: "READY_FOR_REVIEW"`) immediately so Root can submit for maximum speed bonus!
