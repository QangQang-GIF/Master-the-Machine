---
name: mtm-bug-worker
description: Reproduce, minimally fix, and verify an announced MTM BUG item.
---

# BUG (Defect Fix)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. Always run `pnpm exec prisma generate` in `src/` before running any test or seed script.
2. Ingestion & Official Data:
   - Identify if the bug requires test fixtures, schemas, or seed data. Call `fetch_fixture` or query the official Match MCP QA tool.
   - If the bug specification mentions company documentation or KB rules, query the `kb` tool via `call_company_tool`.
   - NEVER generate synthetic test fixtures under official fixture filenames (`answers/<key>/evidence/*-test-data.json`).
3. Pre-fix Evidence & Reproduction:
   - If the scenario requires pre-fix evidence (e.g. before-fix screenshot or reproduction logs), capture it **before** modifying code!
   - Reproduce the bug with a focused command/test. Trace root cause. Apply minimal surgical patch. No collateral refactoring.
4. UI Resilience & Verification:
   - Run announced MVS scenarios and regression tests. Save logs under `answers/<key>/evidence/`.
   - **DATA-FIRST SNAPSHOTTING**: If UI flows are complex or flaky, seed required records directly into the database via prisma/SQL, then open the browser purely to capture screenshots!
   - **RADIX TAB SWITCHING**: Radix UI tabs do not activate on keyboard Enter. Use DOM evaluate click:
     `await page.locator('[role="tab"]').filter({ hasText: "TabName" }).evaluate(el => el.click());`
   - **VIEWPORT**: Always use desktop viewport `{ width: 1600, height: 1000 }`.
   - **FAST-FAIL TIME GUARD (MAX 60 SECONDS FOR UI)**: Cap entire UI testing / screenshot capture at 60 seconds maximum. If browser fails or hangs:
     1. Record the scenario as NOT_RUN with diagnostic details in `testResults`.
     2. NEVER use fake or placeholder PNGs. Real evidence or genuine NOT_RUN is strictly enforced by the submission guard.
5. Submission:
   - Fill `answers/<key>/submit.json` and generate `worker_result.json`:
   - Use `"status": "READY_FOR_REVIEW"` when all announced MVS scenarios pass.
   - Use `"status": "PARTIAL"` when partial code fixes or reproduction logs exist before deadline.
   - NEVER output `"status": "TIMED_OUT"` or `"BLOCKED"` if you have completed code changes.
