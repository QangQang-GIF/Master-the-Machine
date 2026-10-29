---
name: mtm-task-worker
description: Complete an announced MTM TASK item with the checks and artifacts its definition of done requires.
---

# TASK

Follow `mtm-init` and the TaskEnvelope. Read the definition of done and returned submit template; TASK items may have no MVS. Make the requested change, capture all required artifacts, and run proportionate task and regression checks. Even a documentation/artifact task needs a meaningful validation command recorded with its actual exit code. Record unrelated pre-existing failures separately without concealing regressions.

Write `answers/<key>/submit.json` from the exact template and a schema-compliant `worker_result.json`. Return completed verified work promptly to the root, which owns integration, push, and Submit. Do not invent test results or treat absence of MVS as absence of verification.
