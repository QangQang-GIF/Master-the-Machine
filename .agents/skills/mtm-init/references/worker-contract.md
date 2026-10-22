# Worker Contract Specification

Every worker MUST write `worker_result.json` in the root of its workspace before terminating:

```json
{
  "task_id": "<MATCH_TASK_ID>",
  "status": "READY_FOR_REVIEW",
  "skill_ids": ["mtm-bug-worker@0.1.0"],
  "changed_files": ["src/calculator.py"],
  "test_results": [
    {
      "command": "pytest tests/test_calc.py",
      "passed": true,
      "output": "1 passed"
    }
  ],
  "evidence_paths": ["trace.jsonl"],
  "git_revision": null,
  "unresolved_risks": [],
  "elapsed_seconds": 18.5
}
```

Allowed Status Values:
- `READY_FOR_REVIEW`: Task is completely solved, verified by passing tests, ready for submission.
- `BLOCKED`: Unable to proceed due to missing requirements or broken external dependencies.
- `TIMED_OUT`: Hit time budget before completing solution.
- `FAILED`: Tests could not pass after repeated attempts.
- `ESCALATE`: Requires orchestrator reassignment or higher reasoning effort.
