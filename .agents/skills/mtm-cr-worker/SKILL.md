---
name: mtm-cr-worker
description: Implement and verify an announced MTM change request against specification and MVS.
---

# CR (Change Request)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. Always run `pnpm exec prisma generate` in `src/` before running any test or seed script.
2. Ingestion & Official Data:
   - Identify if the CR requires test fixtures, schemas, or seed data. Call `fetch_fixture` or query the official Match MCP QA tool.
   - If the CR mentions company policies or standards (e.g. KB-*), query the `kb` tool via `call_company_tool` before implementing logic.
   - NEVER generate synthetic test fixtures under official fixture filenames (`answers/<key>/evidence/*-test-data.json`).
3. Implementation:
   - Implement acceptance criteria cleanly extending existing controllers, routers, services, and UI components.
   - Guard against common edge cases: international currency formatting, special characters, whitespace trimming, timezones, and null/empty handling.
4. Verification & Testing:
   - Verify positive, boundary, and regression cases. Run existing test suite to ensure zero regressions across other items.
   - For UI MVS scenarios: Cap browser operations at 60s max. Use DOM evaluate click (`await page.locator(...).evaluate(el => el.click())`) to avoid navigation interception.
   - If browser UI capture is unavailable or times out: Mark the UI scenario `NOT_RUN` with clear diagnostic notes in `testResults`. Never fabricate fake placeholder PNGs.
5. Submission:
   - Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if partially completed).
   - NEVER mark `BLOCKED` or `TIMED_OUT` when working code changes or logs exist. Submit before deadline to claim maximum points!
