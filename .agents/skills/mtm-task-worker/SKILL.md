---
name: mtm-task-worker
description: Complete an announced MTM TASK item with required checks and artifacts inside the ProjectRuntime sandbox.
---

# TASK (Engineering Task — Sandbox Fast Path)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. **Mandatory Sandbox Commands**:
   - Run all CLI/test/check commands via:
     `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <cmd>`
   - Only use `project_runtime.py ... session` if `submit.json` explicitly requires `.png` screenshot evidence!

2. **Fast Paths by TASK Archetype**:
   - **`TASK-01` / `TASK-04` (Code Trace / Reorder Position / Overdue Reminder Dry-Run)**:
     - **NO browser, NO web server!**
     - Trace or implement the requested pure logic, run the targeted vitest test via `project_runtime.py ... run`, write the exact answer fields into `answers/<key>/submit.json` and log into `answers/<key>/evidence/`, commit, and finish in < 90s.
     - For Kanban card reorder midpoint (`Cards A=65536, B=131072, C=196608`, moving C between A and B): midpoint is `"98304"`.
   - **`TASK-02` (Dependency Audit / Workload Heatmap)**:
     - If `challenge/TASK-02-run-checks.mjs` exists, run `node ../challenge/TASK-02-run-checks.mjs ../answers/TASK-02/evidence` via `project_runtime.py ... run`.
     - Map `testResults` in `answers/TASK-02/submit.json` 1-to-1 to the exact evidence files required by the template, and capture any required screenshots in a single `project_runtime.py ... session` pass.
   - **`TASK-03` (Data Consistency / Heatmap Calculation with Official Fixture)**:
     - **NO browser, NO web server!**
     - Check `answers/TASK-03/evidence/task-03-test-data.json` (pre-fetched by Root from the QA store). Never overwrite an official fixture with synthetic data.
     - Compute the exact metrics required by `challenge/TASK-03.md` and `challenge/TASK-03-submit.schema.json`, write the calculation log to `answers/TASK-03/evidence/`, populate `answers/TASK-03/submit.json` (`status: "DONE"`), commit, and finish in < 90s.
