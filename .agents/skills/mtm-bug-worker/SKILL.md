---
name: mtm-bug-worker
description: Specialized worker skill for investigating, reproducing, patching, and verifying software bugs (BUG backlog items).
---

# MTM Bug Worker Skill

Specialized workflow for resolving defect/bug tickets (`BUG`) in competition and benchmark environments.

## BUG Pipeline

```text
Inspect Issue
      ↓
Reproduce with Test
      ↓
Hypothesis & Root Cause Identification
      ↓
Minimal Surgical Patch
      ↓
Targeted Unit & Regression Verification
      ↓
Check MVS Criteria
      ↓
Write worker_result.json
```

## Step-by-Step Instructions

1. **Inspect**: Read the task description and error stack trace in `TASK.md`. Identify the failing component.
2. **Reproduce**: Run the specific test or write a minimal reproduction command to observe the failure directly.
3. **Hypothesis & Root Cause**: Use `rg` to find the defect. Inspect the exact lines of code where the logic deviates from intended behavior.
4. **Minimal Patch**: Edit only the affected logic. Never introduce cosmetic changes or style refactoring.
5. **Verify**:
   - Run the reproduction test. Confirm it passes (exit code 0).
   - Run existing test suites for that module to prevent regressions.
   - If MVS tests are listed in `TASK.md`, run and verify them.
6. **Report**: Output `worker_result.json` with status `READY_FOR_REVIEW`, changed files, and test results.
