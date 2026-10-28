---
name: mtm-cr-worker
description: Specialized worker skill for implementing Change Requests (CR), new features, API additions, and schema extensions.
---

# MTM Change Request (CR) Worker Skill

Specialized workflow for implementing feature enhancements and change requests (`CR`) in competition environments.

## CR Pipeline

```text
Parse Requirements & Extract Dynamic Evidence Requirements
      ↓
Mandatory Before-Change Evidence Capture (e.g. before-invoices.png)
      ↓
Prepare Local Test Data Fixtures (Never block on external Drive)
      ↓
Locate Affected Architecture / Entrypoints
      ↓
Implement Change (RFC 4180 standard for CSV exports)
      ↓
Positive & Negative Test Validation
      ↓
Submission Validation Gate (All tests PASS, zero NOT_RUN / FAIL)
      ↓
Write submit.json & worker_result.json
```

## Step-by-Step Instructions

1. **Parse Requirements & Dynamic Evidence Extraction**:
   - Inspect `TASK.md` or `challenge/<key>-minimum-verification-scenarios.md`.
   - Extract all input/output contracts, CLI flags, API schema changes, and expected evidence files.
   - **MANDATORY BEFORE-CHANGE EVIDENCE**: If the requirements specify pre-change evidence (e.g., `before-invoices.png` showing UI before the new button/feature), **CAPTURE THIS SCREENSHOT BEFORE IMPLEMENTING ANY CODE CHANGES**.
2. **Local Test Data Fixtures (ANTI-BLOCKER RULE)**:
   - In MTM tournament runtime, there is NO external Google Drive or external QA tool connection.
   - If the task mentions obtaining fixtures from a "QA test data store" (e.g. `cr-01-test-data.json`), **NEVER REPORT BLOCKED**.
   - Copy or seed the fixture from the team's local templates/state directly into `answers/<key>/evidence/<fixture>.json`.
   - Run the loader script (e.g. `pnpm tsx prisma/seed-cr01.ts ...`).
3. **Locate Architecture**: Identify the correct module, router, or class to extend without creating duplicate code paths.
4. **Implement**:
   - Add the required classes/methods adhering to the existing project conventions.
   - For CSV export features, strictly follow **RFC 4180** standards:
     - Prepend UTF-8 BOM (`\uFEFF`).
     - Line endings must be CRLF (`\r\n`).
     - Escape commas, double quotes (`""`), and newlines in text fields.
     - Zero-decimal currencies (like VND, JPY) format without fractional decimals, while decimal currencies (USD) format with decimal points.
5. **Verify All Scenarios**:
   - Run the task's check runners (e.g. `CR-01-run-checks.mjs`, `CR-01-signed-out-check.mjs`).
   - Run unit tests and typechecks to guarantee zero regression.
6. **Submission Validation Gate (HARD RULE)**:
   - **Status MUST be `DONE`**. Submissions with `PARTIAL`, `BLOCKED`, or `FAIL` will score 0 and are blocked by the submission gate.
   - **All test cases in `submit.json` MUST evaluate to `PASS`**. Never submit any test case with `NOT_RUN` or `FAIL`. If any check fails, debug and fix it immediately.
   - **Physical Evidence Check**: Verify that every evidence file declared in `evidenceFiles` and `testResults[].evidence` physically exists on disk and is committed in Git.
7. **Report**: Write `submit.json` and `worker_result.json` with status `READY_FOR_REVIEW`.

## CR Verification & File Export Rules
- **Non-blocking Downloads**: When testing file export features (CSV/PDF), never hang waiting for browser download events (`waitForEvent('download')`). Instead, evaluate the export generation directly or write the export data straight to the designated evidence path (`answers/<key>/evidence/<file>.csv`).
- **Playwright Timeouts**: All browser operations must include an explicit timeout (max 5000ms).
- **Audit Evidence**: Ensure all required evidence files (screenshots, logs, reports) exist before reporting completion.

