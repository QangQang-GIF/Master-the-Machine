---
name: mtm-cr-worker
description: Implement and verify an announced MTM change request against its specification and MVS.
---

# CR

Follow `mtm-init` and the TaskEnvelope. The root has already called Start Work.

1. Extract input/output contracts, acceptance criteria, every announced MVS, and artifact requirements from the specification and returned template. Capture any requested before-change evidence now.
2. Locate the existing implementation and extend its conventions. Handle required boundaries, permissions, encodings, and compatibility without unrelated refactoring.
3. Use provided local fixtures/loaders and schemas. Verify positive, negative, boundary, and relevant regression behavior; save real command outputs and requested UI/export artifacts under `answers/<key>/evidence/`. Preserve specified screenshot conditions and file formats.
4. Fill `answers/<key>/submit.json` exactly from the received template. Cover every required scenario; do not remove failing or unrun cases. Declare PASS only from actual verification. Unrelated demonstrated baseline failures do not block a solved item; new regressions do.
5. Write schema-compliant `worker_result.json` and return immediately for root integration/submission. The root alone pushes and calls Submit. Report genuine missing inputs accurately; never invent official data or evidence.
