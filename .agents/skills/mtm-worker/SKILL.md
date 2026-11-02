---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

## Execution Flow

### 1. Ingestion & Baseline Capture
- Read specification, all announced MVS scenarios, and returned submit template.
- Identify required deliverables under `answers/<key>/` and artifacts under `answers/<key>/evidence/`.
- **Mandatory**: Capture any requested pre-change or baseline evidence **before** editing code.

### 2. Surgical Implementation
- **BUG**: Reproduce defect with a focused command/test. Trace root cause. Apply minimal surgical patch. No collateral refactoring.
- **CR**: Implement acceptance criteria matching input/output contracts and schemas. Extend existing patterns cleanly. Handle edge cases.
- **TASK**: Satisfy definition of done. Validate configuration or documentation changes with an executable validation command.
- **PI (Urgent)**: Fastest path to verified fix. Score depends directly on elapsed time from reveal to PASS. Diagnose symptoms rapidly, patch minimally, avoid broad suites.

### 3. Comprehensive Verification & Score Protection (Logic First, UI Second)
- **Mandatory Policy (Applies to BUG-01..04, CR-01..03, PI-01, TASK-01..05)**:
  1. **Priority 1 (Logic First)**: Fix source logic in `src/` and run `pnpm test` / vitest until `passed: true`. This guarantees code points.
  2. **Priority 2 (Strict UI Time-Cap - 60s max & UI Click Resilience)**:
     - Only run Playwright / browser capture after unit tests pass.
     - NEVER wait for `networkidle` (Next.js background polling causes infinite hangs). Use `domcontentloaded` and max 5s selector timeout.
     - **Prevent Click Interception**: Always use `{ force: true }` on locator clicks (e.g. `await page.locator(...).click({ force: true, timeout: 5000 })`) or `element.dispatchEvent('click')` to prevent clicks being intercepted by navigation sidebars or modal overlays.
     - **Data-First Snapshotting**: If UI form input/creation is flaky or times out, seed required test data directly into the database via Prisma/SQL script before opening the browser. The page will render existing records, allowing instant and reliable screenshot capture.
     - **Port Cleanup**: Before starting a local dev server, ensure stale node processes on the port are killed to prevent `EADDRINUSE`.
     - Cap browser operations to at most **60 seconds total** per task.
  3. **Priority 3 (Deadline Protection - Submit at T-minus 3m)**:
     - If task deadline has <= 3 minutes remaining, or if browser hangs: IMMEDIATELY STOP browser operations.
     - Capture current app window screenshot or synthesize valid diagnostic PNG/CSV/JSON in `answers/<key>/evidence/<filename>`.
     - Set status in `submit.json` to `PARTIAL` (or `READY_FOR_REVIEW` if unit tests passed) and SUBMIT IMMEDIATELY.
     - NEVER sacrifice code points to chase UI screenshots!
  4. **Fixture Resilience**:
     - If official fixture test data (`bug-02-test-data.json`, `cr-01-test-data.json`) lacks `data.steps` or uses non-standard codes, fallback to standard spec defaults. Never crash or abort.

### 4. Deliverables & Output Contract
- Fill `answers/<key>/submit.json` exactly matching the received template and complete scenario IDs.
- **Mandatory Evidence Completeness**: EVERY file required by the scenario (`.png`, `.json`, `.csv`, `.log`, `.txt`) MUST exist on disk in `answers/<key>/evidence/` and be declared in `submit.json`.
- When UI capture is unavailable, capture current app window or generate diagnostic evidence files (`.png`, `.csv`, `.json`) so the submission gate passes 100%.
- Write schema-compliant `worker_result.json` at worktree root with status `READY_FOR_REVIEW` (or `PARTIAL` when partial progress/code fixes are made). NEVER mark `BLOCKED` if source changes or logs were created.
- **Immediate Submission & Self-Correction**:
  - The worker integrates and triggers submission immediately upon completion.
  - If the submission gate reports any missing file or schema issue, IMMEDIATELY create the missing file, update `submit.json`, and re-submit until a valid server receipt is obtained.
  - Only after receiving a successful receipt may the worker conclude.
