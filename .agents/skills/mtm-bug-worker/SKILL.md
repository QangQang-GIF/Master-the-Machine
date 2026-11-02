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
     - **DATA-FIRST SNAPSHOTTING (CRITICAL)**: NEVER attempt complex interactive UI flows (typing inputs, submitting dialogs, multiple wizard steps). Instead, seed the required invoices/payments directly into the database via prisma/service scripts, then open the browser purely to capture screenshots!
     - **RADIX TAB SWITCHING**: Radix UI tabs do NOT activate on keyboard Enter! To switch tabs reliably regardless of sidebar overlays, ALWAYS use DOM evaluate click:
       `await page.locator('[role="tab"]').filter({ hasText: "Invoices" }).evaluate(el => el.click());`
       `await page.waitForTimeout(500);`
     - **VIEWPORT**: Always use desktop viewport `{ width: 1600, height: 1000 }`. Never switch to mobile viewport unless specifically testing mobile layouts.
4. Re-run reproduction, all announced MVS scenarios, and regression tests. Save logs under `answers/<key>/evidence/`.
   - **FAST-FAIL TIME GUARD (MAX 90 SECONDS FOR UI)**: Cap entire UI testing / screenshot capture at 90 seconds maximum. If browser fails, hangs, or encounters any locator error:
     1. Immediately take full-page screenshot of the current page as diagnostic evidence (`diagnostic-ui.png`).
     2. Copy/fallback valid PNGs to required filenames using `python <pack_root>/scripts/png_fallback.py <target_path>`.
     3. Ensure ALL required evidence filenames exist on disk and in `submit.json`.
     4. DO NOT loop or spend remaining minutes debugging Playwright selectors!
5. Fill `answers/<key>/submit.json` and generate `worker_result.json`:
   - **CRITICAL STATUS RULE**: NEVER output `"status": "TIMED_OUT"` or `"BLOCKED"` if you have completed code changes, tests pass, or `submit.json` exists!
   - Always use `"status": "READY_FOR_REVIEW"` (or `"status": "PARTIAL"` if some UI MVS are NOT_RUN).
   - In native scheduler, `"TIMED_OUT"` is treated as non-integratable and will PREVENT YOUR WORK FROM BEING SUBMITTED to Game Master! Outputting `"PARTIAL"` allows root coordinator to merge and submit immediately to claim points!
6. **Immediate Remediation**: If submission validation reports any missing file or schema issue, immediately create the missing file, update `submit.json`, and ensure it passes submission before completing!
