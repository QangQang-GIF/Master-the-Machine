---
name: mtm-bug-worker
description: Reproduce, minimally fix, and verify an announced MTM BUG item inside the ProjectRuntime sandbox.
---

# BUG (Defect Fix — Sandbox Fast Path)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. **Mandatory Sandbox Commands**:
   - Run all CLI/test/seed commands via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <cmd>`
   - Run all browser/UI checks via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- <cmd>`
   - NEVER manually spawn `pnpm dev`, `pnpm build`, or `pnpm start`.

2. **Fast Paths by BUG Archetype**:
   - **`BUG-01` (Responsive Shell / Layout)**:
     - Fix layout CSS in `src/components/layout/`.
     - Inside a single `project_runtime.py ... session` wrapper script, run `node ../challenge/BUG-01-layout-check.mjs http://127.0.0.1:$PORT development ../answers/BUG-01/evidence/layout-report-dev.json`, `node ../challenge/BUG-01-layout-check.mjs http://127.0.0.1:$PORT production ../answers/BUG-01/evidence/layout-report-prod.json`, and capture `desktop-1440x900.png` & `mobile-390x844.png`! (Do NOT run `pnpm build` — `BUG-01-layout-check.mjs` only tags the mode argument in JSON).
   - **`BUG-02` (Client Currency Propagation)**:
     - Verify `answers/BUG-02/evidence/bug-02-test-data.json` exists (pre-fetched by Root; NEVER overwrite with synthetic data).
     - Copy `challenge/BUG-02-seed.ts` -> `src/prisma/seed-bug02.ts` and `challenge/BUG-02-report.ts` -> `src/prisma/report-bug02.ts`.
     - Fix currency propagation in `src/` and add unit tests.
     - Run `node ../challenge/BUG-02-run-checks.mjs` via `project_runtime.py ... run` to write `answers/BUG-02/evidence/test.log`.
     - Run `pnpm tsx prisma/seed-bug02.ts ../answers/BUG-02/evidence/bug-02-test-data.json` via `project_runtime.py ... run`, then execute a small `pnpm tsx` script using `~/server/db` and `postInvoiceIssued` / `postPaymentReceived` / `refreshInvoiceStatus` to create & issue the VND invoice (lines `25000000`, `18500000`, `6500000` = `50000000`), record the VND payment (`20000000` -> `PARTIAL`), and create & issue the USD invoice (`150000`, `85000` = `235000` -> `SENT`).
     - Run `pnpm tsx prisma/report-bug02.ts ../answers/BUG-02/evidence/bug-02-test-data.json ../answers/BUG-02/evidence/currency-report.txt` via `project_runtime.py ... run`.
     - Capture `tc-01-vnd-invoice.png`, `tc-02-vnd-partial-paid.png`, `tc-03-usd-invoice.png` in ONE `project_runtime.py ... session` pass. Remove temporary `src/prisma/*-bug02.ts` helper files before committing.
   - **`BUG-03` (Invoice Status Precedence — Pure Logic)**:
     - **NO browser, NO web server!**
     - Ensure `challenge/BUG-03-mvs.test.ts` is copied to `src/tests/bug-03-mvs.test.ts` and committed.
     - Fix `src/src/lib/invoice-status.ts`, run `pnpm test` and `pnpm typecheck` via `project_runtime.py ... run`, save required logs, fill `submit.json` (`status: "DONE"`), commit, and finish in < 90s.
   - **`BUG-04` (Kanban Card Reorder Position)**:
     - Ensure `challenge/BUG-04-mvs.test.ts` is copied to `src/tests/bug-04-mvs.test.ts` and committed!
     - Fix `src/src/lib/position.ts`, write `mvs-tests.log` (`command: pnpm vitest run tests/bug-04-mvs.test.ts` ... `exit_code: 0`) and `typecheck.log` (`command: pnpm typecheck` ... `exit_code: 0`) via `project_runtime.py ... run`, and capture `tc-02-top-order.png` via `project_runtime.py ... session`.
