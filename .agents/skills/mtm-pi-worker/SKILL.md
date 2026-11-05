---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident inside the ProjectRuntime sandbox.
---

# PI (Production Incident — Urgent Fast Path)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS, but only a correct fix scores: the slowest correct PI still earns 60%, a wrong one earns 0 and cannot be resubmitted. Be fast, never at the cost of correctness.

1. **Zero Browser / Zero Server Overhead**:
   - Production incidents (`PI-01`, etc.) are urgent backend/service fixes. **Do NOT start a web server or Playwright browser** unless `.png` evidence is explicitly listed in `submit.json`.
2. **Pre-Fetched Incident Context**:
   - Check `metadata.company_context` in TaskEnvelope and `answers/<key>/evidence/` first. If additional production logs or KB rules are needed, query via `runner.control company`.
3. **Surgical Fix & Sandbox Verification (aim for a few minutes, but verify)**:
   - Diagnose the root cause from `challenge/<key>.md` and incident logs (e.g., `refreshInvoiceStatus` after payment creation or voiding in `src/src/server/services/accounting/posting.ts`).
   - Apply the minimal surgical fix in `src/`.
   - Run targeted vitest verification via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- pnpm vitest run <test_file>`
   - Save verification logs to `answers/<key>/evidence/`.
4. **Immediate Commit & Completion**:
   - Populate `answers/<key>/submit.json`, `git add` & `git commit` all modified files and `answers/<key>/`, run `check_done` (see `mtm-worker`), and write `worker_result.json` as soon as it passes so Root can submit for the speed bonus.
   - If the required incident context (company KB / logs) is unavailable, say so honestly in `submit.json`; never guess a fix you could not verify. Root will hold a non-DONE PI back while there is time and send it to you again.
