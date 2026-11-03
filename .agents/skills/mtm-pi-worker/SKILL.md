---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident.
---

# PI (Production Incident - Urgent)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS. Root submits immediately.

1. Query Official Observability:
   - Immediately query the official Match MCP `logs` tool via `call_company_tool` to fetch production traces, error logs, and timestamps.
   - If the incident brief mentions incident guides or system documentation, query the `kb` tool.
   - Save raw query results and correlation logs into `answers/<key>/evidence/`.
2. Rapid Diagnosis & Surgical Fix:
   - Identify the failing subsystem, regression commit, or bad config from the production traces.
   - Apply the smallest complete surgical fix with tight command timeouts.
3. Verification:
   - Run focused incident resolution checks and targeted regression checks. Save outputs under `answers/<key>/evidence/`.
4. Immediate Submission:
   - Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` (or `PARTIAL` if partially mitigated).
   - Submit without delay to maximize completion-order score!
