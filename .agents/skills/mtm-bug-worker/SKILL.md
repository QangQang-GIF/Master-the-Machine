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
Mandatory Before-Fix Evidence Capture (if required)
      ↓
Reproduce with Test
      ↓
Hypothesis & Root Cause Identification
      ↓
Minimal Surgical Patch
      ↓
Targeted Verification & Evidence Capture
      ↓
Submission Validation Gate (Zero NOT_RUN / FAIL, 100% PASS)
      ↓
Write submit.json & worker_result.json
```

## Step-by-Step Instructions

1. **Dynamic Requirement Extraction**:
   - Inspect the task description and `challenge/<key>-minimum-verification-scenarios.md`.
   - Parse all required evidence files (screenshots, reports, test logs).
   - **MANDATORY BEFORE-FIX CHECK**: If the requirements specify any baseline evidence before fixing (e.g. `dashboard_before_fixing.png`, `before-*.png`, or pre-fix measurements), **CAPTURE THIS EVIDENCE IMMEDIATELY** before editing any source or styling files.
2. **Reproduce**: Run the specific test or write a minimal reproduction command to observe the failure directly.
3. **Hypothesis & Root Cause**: Use `rg` to find the defect. Inspect the exact lines of code where the logic deviates from intended behavior.
4. **Minimal Patch**: Edit only the affected logic. Never introduce cosmetic changes or style refactoring.
5. **Verify & Complete Test Suite**:
   - Run the reproduction test. Confirm it passes (exit code 0).
   - Run the task's check runner script (e.g. `BUG-02-run-checks.mjs`).
   - If there is a baseline regression failure (e.g., in `tests/cascade.test.ts`), fix the underlying schema/relation or test assertion so the entire suite passes 100%.
   - If official fixtures are needed (e.g., `bug-02-test-data.json`), retrieve or load them from local team templates immediately. Never leave tests as `NOT_RUN`.
6. **Submission Validation Gate (HARD RULE)**:
   - **Status MUST be `DONE`**. Submissions with `PARTIAL`, `BLOCKED`, or `FAIL` will score 0 and are blocked by the submission gate.
   - **All test cases in `submit.json` MUST evaluate to `PASS`**. Never submit any test case with `NOT_RUN` or `FAIL`. If a test is failing, debug and fix it.
   - **Physical Evidence Check**: Verify that every evidence file declared in `evidenceFiles` and `testResults[].evidence` physically exists in `answers/<key>/evidence/` on disk and is staged/committed in Git.
7. **Report**: Output `worker_result.json` with status `READY_FOR_REVIEW`, changed files, and verified test results.
