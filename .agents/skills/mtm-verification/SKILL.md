---
name: mtm-verification
description: Multi-level test runner, regression analysis, and Minimum Viable Solution (MVS) validation skill.
---

# MTM Verification Skill

Specialized procedural skill for multi-level testing, test suite isolation, and MVS validation.

## Principles

1. **Focused Verification First**:
   - Always verify the unit test directly testing the changed code before expanding scope.
2. **Deterministic Status Gate (Strict Backlog Item Scope)**:
   - Status `READY_FOR_REVIEW` requires 100% test pass rate for all executed tests covering the changed code and the current backlog item.
   - **Strict Backlog Item Scope**: The 100% PASS requirement (Zero `NOT_RUN`, Zero `FAIL`) applies EXCLUSIVELY to the test cases declared in `answers/<key>/submit.json` for the current backlog item.
   - **Baseline Test Isolation**: Pre-existing test failures outside the modified area (acknowledged in `PROJECT_MAP.md` during preparation, e.g. intentional baseline failures in `cascade.test.ts`) MUST NOT block task completion or submission. Never withhold submission because of pre-existing, unrelated repository test failures.
   - If an item-specific test fails, status must remain `FAILED` while the worker fixes the issue.
3. **Local Autonomy & Fixtures (No External Cloud Lookups)**:
   - External cloud drives (Google Drive, Docs, external QA portals) DO NOT exist in the tournament runtime.
   - Phrases like "obtain from QA test data store" or "official fixture" in task scenarios are narrative context.
   - When seed loaders are provided in `challenge/` (`CR-01-seed.ts`, `BUG-02-seed.ts`), generate or load the fixture data locally according to the schema expected by the script and run it. Never report `BLOCKED` for missing external stores.
4. **Mandatory Submission Trigger**:
   - Once task verification scenarios pass and evidence files exist in `answers/<key>/evidence/`, the task is considered fully solved.
   - The worker/orchestrator MUST commit the changes, push to the remote branch, and IMMEDIATELY call `submit_work`.
   - NEVER leave a completed task unsubmitted. Never mark a solved task as BLOCKED.
