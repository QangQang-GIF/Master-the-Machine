---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an urgent MTM production incident.
---

# PI (Urgent)

Follow `mtm-init` and `mtm-worker`. Score depends on time from reveal to PASS. Root submits immediately.

1. Capture pre-change diagnostics; diagnose root cause quickly.
2. Apply the smallest complete fix with tight command timeouts.
3. Verify incident resolution and targeted regression checks. Save outputs under `answers/<key>/evidence/`.
4. Fill `answers/<key>/submit.json` and generate `worker_result.json` with status `READY_FOR_REVIEW` immediately.
