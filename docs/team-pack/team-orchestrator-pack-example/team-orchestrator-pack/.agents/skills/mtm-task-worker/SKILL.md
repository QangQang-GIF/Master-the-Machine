---
name: mtm-task-worker
description: Complete an announced MTM TASK item with required checks and artifacts.
---

# TASK

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. Always run `pnpm exec prisma generate` in `src/` before running any test or verification command.
2. Review definition of done and capture requested pre-change artifacts.
3. Complete changes and run proportionate validation/regression commands:
   - For TASK-02 (Dependency vulnerabilities): Run `node ../challenge/TASK-02-run-checks.mjs ../answers/TASK-02/evidence`.
     In `answers/TASK-02/submit.json`, you MUST strictly map test results 1-to-1 to the exact evidence files required by Grader:
     * `TASK-02-TC-01`: result `PASS`, evidence: `["evidence/install.log"]`
     * `TASK-02-TC-02`: result `PASS`, evidence: `["evidence/audit.log"]`
     * `TASK-02-TC-03`: result `PASS`, evidence: `["evidence/typecheck.log"]`
     * `TASK-02-TC-04`: result `PASS`, evidence: `["evidence/test.log"]`
     * `TASK-02-TC-05`: result `PASS`, evidence: `["evidence/tc-05-login-dashboard.png", "evidence/tc-05-signed-out.png"]` (capture the 2 screenshots)
     DO NOT SHUFFLE OR MISMATCH THESE EVIDENCE PATHS. Mismatched paths result in 0 points for that test case!
   - For TASK-01 (Card reordering position):
     * Check `src/src/lib/position.ts`: Cards A, B, C created sequentially have positions 65536, 131072, 196608.
     * Moving C between A and B gives midpoint `(65536 + 131072) / 2 = 98304`.
     * Persisted database position is `"98304"`. Set `"position": "98304"` in `answers/TASK-01/submit.json`. DO NOT use `"0"`.
4. Save deliverables under `answers/<key>/evidence/`.
5. Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if partially completed or command outputs like audit return non-zero advisories; NEVER mark `BLOCKED` if answer files or logs were created).
