---
name: mtm-cr-worker
description: Implement and verify an announced MTM change request against specification and MVS inside the ProjectRuntime sandbox.
---

# CR (Change Request — Sandbox Fast Path)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. **Mandatory Sandbox Commands**:
   - Run all CLI/test/seed/check-runner commands via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <cmd>`
   - For UI/HTTP items, start the dev server first and capture in one pass (see `mtm-evidence`):
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session-start --routes <routes>` → work → `run -- node "<metadata.capture_multi_path>" spec.json --cutoff-epoch <taskEnvelope.evidence_cutoff_local_epoch>` → `session-stop`.
   - NEVER manually spawn `pnpm dev`, `pnpm build`, or `pnpm start`.

2. **Fast Paths by CR Archetype**:
   - **`CR-01` (Invoices CSV Export / Client CSV Import)**:
     - Verify `answers/CR-01/evidence/cr-01-test-data.json` exists (pre-fetched by Root; NEVER overwrite with synthetic data).
     - Copy `challenge/CR-01-seed.ts` to `src/prisma/seed-cr01.ts` and run `pnpm tsx prisma/seed-cr01.ts ../answers/CR-01/evidence/cr-01-test-data.json` via `project_runtime.py ... run` to load the official fixture companies and invoices into the database. (Remove `src/prisma/seed-cr01.ts` before `git commit`).
     - Implement the required feature and unit tests in `src/`.
     - Run `node ../challenge/CR-01-run-checks.mjs` via `project_runtime.py ... run` to generate `answers/CR-01/evidence/test.log` and `answers/CR-01/evidence/typecheck.log`.
     - After `session-start` (run it as your first action), using the printed `<baseUrl>`:
       1. Execute `node ../challenge/CR-01-signed-out-check.mjs <baseUrl>/api/trpc/accounting.invoices ../answers/CR-01/evidence/tc-04-signed-out-request.txt`.
       2. Export `tc-01-all-invoices.csv` (all 6 fixture invoices) and `tc-02-filtered-invoices.csv` (filtered to `OVERDUE`).
       3. Capture `tc-01-accounting-page.png` and `tc-02-filtered-export.png` (1440x900) in ONE `capture_multi.mjs` pass, then `session-stop`.
   - **`CR-02` (Accounts Receivable Aging Report) & `CR-03` (Quick-Action Contact Links)**:
     - **CRITICAL**: Always copy `challenge/CR-02-mvs.test.ts` -> `src/tests/cr-02-mvs.test.ts` (or `challenge/CR-03-mvs.test.ts` -> `src/tests/cr-03-mvs.test.ts`) and include it in your `git commit`! The Grader checks that `src/tests/cr-02-mvs.test.ts` / `src/tests/cr-03-mvs.test.ts` is committed.
     - Implement the pure calculation/component logic in `src/`.
     - Run `node ../challenge/CR-02-run-checks.mjs` (or `CR-03-run-checks.mjs`) via `project_runtime.py ... run` so `test.log` and `typecheck.log` are generated with exact headers.
     - Run `session-start` as your first action; capture `tc-01-aging-report.png` (or `tc-01-contact-links.png`) at 1440x900 in ONE `capture_multi.mjs` pass, then `session-stop`.

3. **Submission**:
   - Fill `answers/<key>/submit.json` (`status: "DONE"` when all MVS pass, or `"PARTIAL"` if any scenario is `NOT_RUN`), commit all changes in your worktree, and write `worker_result.json` with `status: "READY_FOR_REVIEW"`.
