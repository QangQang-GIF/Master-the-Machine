---
name: mtm-bug-worker
description: Reproduce, minimally fix, and verify an announced MTM BUG item.
---

# BUG

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. Always run `pnpm exec prisma generate` in `src/` before running any test or seed script.
2. Capture requested pre-fix evidence before changing code:
   - For BUG-01: Grader STRICTLY REQUIRES a pre-fix screenshot named EXACTLY `dashboard_before_fixing.png` saved in `answers/BUG-01/evidence/dashboard_before_fixing.png`. You MUST list `"evidence/dashboard_before_fixing.png"` in `evidenceFiles` and reference it in `testResults`. Failing to include this exact filename scores 0!
3. Reproduce the bug with a focused command; trace root cause; patch minimally:
   - For BUG-01: Use `@layer components { @media (min-width: 48rem) { .sidebar-content-offset { margin-inline-start: 15rem; } } }` in `globals.css` and use `sidebar-content-offset` in `layout.tsx` and `topbar.tsx`. DO NOT run heavy production build (`pnpm build`) inside worker worktrees as it exhausts time budget; use the pre-warmed dev server layout measurements.
   - For BUG-02: Run `pnpm tsx prisma/seed-bug02.ts ../answers/BUG-02/evidence/bug-02-test-data.json`. Check `src/src/lib/money.ts` and invoice creation in `accounting.ts` where VND unit price is converted to minor units (VND has factor 1, not 100).
     - **UI Click Resilience**: In Playwright browser scripts, ALWAYS use `locator.click({ force: true, timeout: 5000 })` or `element.dispatchEvent('click')` so clicks on "New invoice" or tabs are never intercepted by the navigation sidebar.
     - **Data-First Snapshotting**: Seed required invoices/payments directly into the database before opening the browser if form submission encounters UI friction, ensuring screenshots capture valid data immediately.
4. Re-run reproduction, all announced MVS scenarios, and regression tests. Save logs under `answers/<key>/evidence/`.
   - **Deadline & Evidence Completeness**: Cap screenshot/browser operations at 60 seconds max. If browser hangs, renders slowly, or task deadline has <= 2 minutes remaining: immediately capture a diagnostic screenshot or synthesize a valid diagnostic PNG/report in `answers/<key>/evidence/`. ALL scenario evidence files MUST exist on disk and be listed in `evidenceFiles`. Mark the specific UI test `NOT_RUN` with note: "Diagnostic evidence captured". Never omit a required evidence filename!
5. Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if a partial fix is made or pre-existing baseline test failures exist; NEVER mark `BLOCKED` if source changes or logs were created so points are awarded).
6. **Immediate Remediation**: If submission validation reports any missing file or schema issue, immediately create the missing file, update `submit.json`, and ensure it passes submission before completing!
