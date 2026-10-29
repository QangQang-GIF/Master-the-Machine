---
name: mtm-evidence
description: Capture and validate the actual screenshots, logs, reports, and exports required by an MTM item.
---

# Evidence

Read the task, received template, and announced scenarios to inventory exact artifact names, formats, and capture conditions. Capture requested pre-change state before editing; never reconstruct it from a modified application.

Save real artifacts under `matchRoot/answers/<key>/evidence/` (the worktree's matchRoot for workers). Record commands, exit codes, scenario IDs, and relevant context in logs. Screenshots must show the requested UI at the required viewport; exported data must come from the behavior under test. Use bounded waits and verify file completion. Never fabricate logs, images, or PASS results.

Every reference in `evidenceFiles`, test results, and WorkerResult must resolve to a nonempty file within this item's directory; reject absolute paths, traversal, and escaping symlinks. Keep secrets out of evidence. Root verifies required coverage and committed contents at the exact revision being pushed/submitted, rather than merely checking working-tree existence.
