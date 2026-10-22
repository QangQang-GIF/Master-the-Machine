---
name: mtm-verification
description: Multi-level test runner, regression analysis, and Minimum Viable Solution (MVS) validation skill.
---

# MTM Verification Skill

Specialized procedural skill for multi-level testing, test suite isolation, and MVS validation.

## Principles

1. **Focused Verification First**:
   - Always verify the unit test directly testing the changed code before expanding scope.
2. **Deterministic Status Gate**:
   - Status `READY_FOR_REVIEW` requires 100% test pass rate for all executed tests.
   - If even one test fails, status must remain `FAILED` or worker must fix the issue.
3. **MVS Enforcement**:
   - When `mvs` tests are listed in `TASK.md`, run them explicitly and ensure exit code 0.
