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
   - For BUG-01: Use `@layer components { @media (min-width: 48rem) { .sidebar-content-offset { margin-inline-start: 15rem; } } }` in `globals.css` and use `sidebar-content-offset` in `layout.tsx` and `topbar.tsx`.
   - For BUG-02: Run `pnpm tsx prisma/seed-bug02.ts ../answers/BUG-02/evidence/bug-02-test-data.json`. Check `src/src/lib/money.ts` and invoice creation in `accounting.ts` where VND unit price is converted to minor units (VND has factor 1, not 100).
4. Re-run reproduction, all announced MVS scenarios, and regression tests. Save logs under `answers/<key>/evidence/`.
5. Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if a partial fix is made or pre-existing baseline test failures exist; NEVER mark `BLOCKED` if source changes or logs were created so points are awarded).
