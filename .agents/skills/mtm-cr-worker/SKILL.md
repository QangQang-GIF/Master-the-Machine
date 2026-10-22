---
name: mtm-cr-worker
description: Specialized worker skill for implementing Change Requests (CR), new features, API additions, and schema extensions.
---

# MTM Change Request (CR) Worker Skill

Specialized workflow for implementing feature enhancements and change requests (`CR`) in competition environments.

## CR Pipeline

```text
Parse Requirements
      ↓
Build Acceptance Criteria Matrix
      ↓
Locate Affected Architecture / Entrypoints
      ↓
Implement Change
      ↓
Positive & Negative Test Validation
      ↓
Check MVS Criteria
      ↓
Regression Check
      ↓
Write worker_result.json
```

## Step-by-Step Instructions

1. **Parse Requirements**: Extract all input/output contracts, CLI flags, or API schema changes specified in `TASK.md`.
2. **Locate Architecture**: Identify the correct module, router, or class to extend without creating duplicate code paths.
3. **Implement**: Add the required classes/methods adhering to the existing project conventions.
4. **Test**:
   - Write or execute positive tests validating the new capability.
   - Test edge cases (null inputs, boundary values, error responses).
   - If MVS tests are declared, verify all of them pass.
5. **Report**: Write `worker_result.json` with status `READY_FOR_REVIEW`.
