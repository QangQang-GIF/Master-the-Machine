---
name: mtm-pi-worker
description: Rapidly diagnose, stabilize, and verify an announced MTM production incident or urgent item.
---

# PI and urgent work

Follow `mtm-init` and the TaskEnvelope. PI scoring depends on time from reveal to graded PASS; a receipt alone is not a pass. Use the server-derived budget supplied by the coordinator, reserving time for root integration, push, and Submit.

Read symptoms and received template; PI may have no MVS. Capture any required pre-change artifacts, reproduce or diagnose quickly, and implement the smallest complete fix. Run focused incident and regression checks, with explicit timeouts; expand only to resolve a concrete risk. Save real diagnostic/test evidence under `answers/<key>/evidence/`.

Fill the exact submit template and return `worker_result.json` immediately when verified. The root submits each ready item without waiting to batch other work. Do not skip required evidence, fabricate PASS, run unrelated expensive suites, push the assigned branch, or call Submit yourself. At budget exhaustion, preserve artifacts and report honestly.
