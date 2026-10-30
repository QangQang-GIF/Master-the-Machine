---
name: mtm-cr-worker
description: Implement and verify an announced MTM change request against specification and MVS.
---

# CR

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. Always run `pnpm exec prisma generate` in `src/` before running any test or seed script.
2. Capture requested pre-change evidence before editing code.
3. For CR-01:
   - Run `pnpm tsx prisma/seed-cr01.ts ../answers/CR-01/evidence/cr-01-test-data.json`.
   - Add CSV export functionality to the invoices page / API with standard columns (invoiceNumber, company, issueDate, dueDate, totalMinor, status).
   - Run `node ../challenge/CR-01-run-checks.mjs ../answers/CR-01/evidence` and `node ../challenge/CR-01-signed-out-check.mjs`.
4. Verify positive, boundary, and regression cases. Save outputs under `answers/<key>/evidence/`.
5. Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if partially completed). NEVER mark `BLOCKED` if code changes or logs were created.
