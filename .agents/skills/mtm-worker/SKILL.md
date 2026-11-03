---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

## Execution Flow

### 1. Ingestion & Official Data Fetch
- Read specification, all announced MVS scenarios, and returned submit template.
- Identify required deliverables under `answers/<key>/` and evidence artifacts under `answers/<key>/evidence/`.
- **Authoritative Company Tools Bridge**:
  - Check `metadata.company_tools` in TaskEnvelope for available Match MCP tools (`kb`, `qa_data`, `logs`).
  - If the item requires test data / fixtures: Call `fetch_fixture` or query the `qa_data` tool. The raw fixture is saved to `answers/<key>/evidence/` alongside a `.source.json` sidecar.
  - If the brief mentions company standards/policies (e.g. KB-*, CRM rules, accounting policies): Query the `kb` tool via `call_company_tool` before writing code.
  - If the item is a production incident (PI): Query the `logs` tool via `call_company_tool` to obtain production observability traces.
  - **STRICT PROHIBITION**: NEVER generate synthetic test fixtures under official fixture names (e.g. `*-test-data.json`) - doing so results in 0 points from the automated grader.
- Capture any requested pre-change or baseline evidence **before** editing code.

### 2. Surgical Implementation & Edge Cases
- **BUG**: Reproduce defect with a focused command/test. Trace root cause. Apply minimal surgical patch. No collateral refactoring.
- **CR**: Implement acceptance criteria matching input/output contracts and schemas. Extend existing patterns cleanly. Check edge cases (international formatting, whitespace, boundary values, timezones).
- **TASK**: Satisfy definition of done. Validate configuration or documentation changes with an executable validation command.
- **PI (Urgent)**: Fastest path to verified fix. Score depends directly on elapsed time from reveal to PASS. Diagnose symptoms rapidly, patch minimally, avoid broad suites.

### 3. Comprehensive Verification & Score Protection (Logic First, UI Second)
1. **Priority 1 (Logic First)**: Fix source logic in `src/` and run `pnpm test` / vitest until `passed: true`. This guarantees core code points.
2. **Priority 2 (Strict UI Time-Cap - 60s max & UI Click Resilience)**:
   - Only run Playwright / browser capture after unit tests pass.
   - NEVER wait for `networkidle` (Next.js background polling causes infinite hangs). Use `domcontentloaded` and max 5s selector timeout.
   - **Click Interception Resilience**: NEVER rely on `click({ force: true })` when navigation bars or modal overlays are present. ALWAYS trigger clicks directly via DOM evaluate:
     `await page.locator(...).evaluate(el => el.click());` or `await page.locator('[role="tab"]').filter({ hasText: "TabName" }).evaluate(el => el.click());`
   - **Hydration Buffer**: Always add `await page.waitForTimeout(1000)` after `page.goto()` before clicking anything, allowing Next.js React hydration to finish.
   - **Viewport**: Always use desktop viewport `{ width: 1600, height: 1000 }`. Never use mobile view unless specifically asked.
   - **Data-First Snapshotting**: Seed required test data directly into the database via Prisma/SQL script before opening the browser. The page will render existing records, allowing instant and reliable screenshot capture.
   - **Port Cleanup**: Before starting a local dev server, ensure stale node processes on the port are killed to prevent `EADDRINUSE`.
   - Cap browser operations to at most **60 seconds total** per task.
3. **Priority 3 (Deadline Protection - Submit at T-minus 2m)**:
   - If task deadline has <= 2 minutes remaining, or if browser hangs: IMMEDIATELY STOP browser operations.
   - If a test cannot be executed or UI cannot be captured, mark the test `NOT_RUN` with an honest diagnostic reason in `testResults`. NEVER create fake placeholder PNGs.
   - Set status in `submit.json` to `PARTIAL` (or `READY_FOR_REVIEW` if all MVS passed) and SUBMIT IMMEDIATELY.
   - NEVER sacrifice code points to chase UI screenshots!

### 4. Deliverables & Output Contract
- Fill `answers/<key>/submit.json` exactly matching the received template and complete scenario IDs.
- Every testResult must include: `scenarioId`, `passed`, `evidencePath`, `command`, and diagnostic output.
- Write schema-compliant `worker_result.json` at worktree root with status `READY_FOR_REVIEW` (when all MVS PASS with real evidence) or `PARTIAL` (when partial progress/code fixes are made). NEVER mark `BLOCKED` or `TIMED_OUT` if source changes or logs were created.
- Submit immediately upon completion to secure score.
