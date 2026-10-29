---
name: mtm-task-worker
description: Specialized worker skill for tasks, chores, code cleanups, performance improvements, and documentation updates.
---

# MTM Task Worker Skill

Specialized workflow for general tasks, chores, refactorings, and infrastructure updates (`TASK`).

## TASK Pipeline

```text
Understand Definition of Done (DoD)
      ↓
Locate Relevant Area
      ↓
Minimal Focused Implementation
      ↓
Verification
      ↓
Regression Check
      ↓
Write worker_result.json
```

## Step-by-Step Instructions

1. **Definition of Done**: Review explicit requirements in `TASK.md`.
2. **Execute**: Perform the requested refactor, dependency upgrade, configuration update, or documentation sync.
3. **Verify**: Ensure the codebase builds, linters/task checks pass, and no unintended side effects were introduced. Do NOT block on pre-existing baseline test failures in unrelated modules.
4. **Report**: Output `submit.json` and `worker_result.json` with status `READY_FOR_REVIEW`. Submit the task solution via `submit_work`.
