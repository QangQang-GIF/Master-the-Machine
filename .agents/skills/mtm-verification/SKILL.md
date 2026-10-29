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
   - Status `READY_FOR_REVIEW` requires 100% test pass rate for all executed tests covering the changed code and the current backlog item.
   - **Baseline Test Isolation**: Pre-existing test failures outside the modified area (acknowledged in `PROJECT_MAP.md` during preparation) MUST NOT block task completion or submission. Only tests related to the active backlog item or regressions in modified modules are evaluated.
   - If an item-specific test fails, status must remain `FAILED` or worker must fix the issue.
3. **MVS Enforcement**:
   - When `mvs` tests are listed in `TASK.md`, run them explicitly and ensure exit code 0.
4. **Mandatory Submission Trigger**:
   - Once task verification scenarios pass and evidence is in place, the task is considered solved and must be submitted to Game Master via `submit_work`. Do not mark as BLOCKED.
