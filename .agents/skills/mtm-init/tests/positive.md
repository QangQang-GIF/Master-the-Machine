# Positive Test Cases for mtm-init Evaluation

1. **Autonomous Execution**:
   - Worker reads `TASK.md` without prompting the user.
   - Worker searches with `rg`, identifies bug within 3 file reads.
   - Worker implements surgical 2-line change.
   - Worker runs `pytest`, captures result, writes `worker_result.json` with status `READY_FOR_REVIEW`.
