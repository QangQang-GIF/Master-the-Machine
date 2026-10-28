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
   - Run all provided scenario check runners and module test suites.
   - If there is any baseline test failure or regression, diagnose and resolve the issue so that all required tests pass 100%.
   - If test fixtures or seed data are required, load them according to the challenge instructions. Never leave any test case unexecuted.
6. **Submission Validation Gate (HARD RULES)**:
   - **Status MUST be `DONE`**: Submissions marked `PARTIAL`, `BLOCKED`, or `FAIL` score 0 and are blocked by the submission gate.
   - **Zero `NOT_RUN`, Zero `FAIL`**: Every test case in `submit.json` must evaluate to `PASS`. If any test does not pass, continue debugging until all criteria are met.
   - **Physical Evidence Verification**: Ensure every evidence file declared in `evidenceFiles` and `testResults[].evidence` physically exists on disk under `answers/<key>/evidence/` and is committed in Git.
7. **Report**: Output `worker_result.json` with status `READY_FOR_REVIEW`, list of changed files, and verified test results.
