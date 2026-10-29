---
name: mtm-bug-worker
description: Specialized worker skill for investigating, reproducing, patching, and verifying software bugs (BUG backlog items).
---

# MTM Bug Worker Skill

Specialized workflow for resolving defect/bug tickets (`BUG`) in competition and benchmark environments.

## BUG Pipeline

```text
Inspect Issue & Extract Dynamic Evidence Requirements
      ↓
Mandatory Pre-Fix Baseline Evidence Capture (if required by spec)
      ↓
Reproduce Defect with Targeted Test
      ↓
Hypothesis & Root Cause Isolation
      ↓
Minimal Surgical Patch
      ↓
Targeted Verification & Full Test Suite Validation
      ↓
Submission Validation Gate (All Tests PASS, Zero NOT_RUN / FAIL)
      ↓
Write submit.json & worker_result.json
```

## Step-by-Step Instructions

1. **Dynamic Requirement Extraction**:
   - Inspect the backlog task description and verification scenario files (e.g. `challenge/<key>-minimum-verification-scenarios.md` or `challenge/<key>.md`).
   - Extract all expected evidence files (screenshots, diagnostic logs, reports).
   - **PRE-MODIFICATION EVIDENCE RULE**: If the scenario or task description requires baseline evidence of the defect before fixing (e.g. initial error logs, pre-fix screenshots, or baseline measurements), **CAPTURE THIS EVIDENCE IMMEDIATELY** before touching or modifying any application code.
2. **Reproduce**: Run the specific test case or write a minimal reproduction command to observe the defect directly.
3. **Hypothesis & Root Cause**: Trace the execution flow to pinpoint the defect. Inspect the exact lines of code where the logic deviates from expected behavior.
4. **Minimal Surgical Patch**: Edit only the affected logic. Never introduce cosmetic changes, formatting rewrites, or unrelated refactoring.
5. **Verify & Ensure Passing Test Suite**:
   - Run the reproduction test and confirm it passes (exit code 0).
   - Run all provided scenario check runners and module test suites for the changed feature.
   - **Pre-existing Baseline Failures**: Do NOT block submission on pre-existing failing tests in unrelated modules that were already failing before work began (e.g. baseline issues recorded in `PROJECT_MAP.md`). Only test failures introduced as regressions in the modified modules must be resolved.
   - **Local Fixture & Data Autonomy (No External Cloud Lookups)**: Never query Google Drive, Google Docs, or external tools for test fixtures. When scenarios mention "Obtain from QA test data store", create or load the fixture locally using the provided challenge seed scripts (e.g. `challenge/BUG-02-seed.ts`). The MCP Helper `mtm_minimal` ONLY provides contest protocol APIs (`start_work`, `submit_work`, `report_progress`, etc.); all test fixtures, database seed records (`pnpm db:seed`), and evidence captures MUST be created and executed LOCALLY by the worker. Never report BLOCKED on missing external fixtures.
6. **Submission Validation Gate (HARD RULES)**:
   - **Scope of Gate**: The Zero `NOT_RUN`, Zero `FAIL` rule applies STRICTLY to the test cases defined in `submit.json` for the current backlog item. If all test cases for this item in `submit.json` pass and evidence files are captured, THE WORKER MUST SUBMIT.
   - **Status MUST be `DONE`**: Submissions marked `PARTIAL`, `BLOCKED`, or `FAIL` score 0 and are blocked by the submission gate.
   - **Zero `NOT_RUN`, Zero `FAIL` in submit.json**: Every test case declared in `submit.json` must evaluate to `PASS`. If any check fails, resolve it before submitting.
   - **Physical Evidence Verification**: Ensure every evidence file declared in `evidenceFiles` and `testResults[].evidence` physically exists on disk under `answers/<key>/evidence/` and is committed in Git.
   - **MANDATORY SUBMISSION (NEVER ABANDON A FIXED TASK)**: If the fix is implemented, relevant scenarios pass, and evidence is committed and pushed, the orchestrator MUST call `submit_work` to bank points before deadline or match end. Never leave a solved task unsubmitted or marked BLOCKED.
7. **Report**: Output `worker_result.json` with status `READY_FOR_REVIEW`, list of changed files, and verified test results.
