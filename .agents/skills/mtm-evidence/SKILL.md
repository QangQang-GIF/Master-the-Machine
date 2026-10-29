---
name: mtm-evidence
description: Capture and validate the actual screenshots, logs, reports, and exports required by an MTM item.
---

# Evidence

Read the task, received template, and announced scenarios to inventory exact artifact names, formats, and capture conditions. Capture requested pre-change state before editing; never reconstruct it from a modified application.

Save real artifacts under `matchRoot/answers/<key>/evidence/` (the worktree's matchRoot for workers). Record commands, exit codes, scenario IDs, and relevant context in logs. Screenshots must show the requested UI at the required viewport; exported data must come from the behavior under test. Use bounded waits and verify file completion. Never fabricate logs, images, or PASS results.

For web evidence, use the project runtime's `session -- <browser/check command>` mode with the envelope's runtime path, match and worker IDs. It starts the declared local dev server, checks readiness, runs the capture command with the same ephemeral configuration/port, and cleans up the server process group. Keep sign-in and all related captures in that session. Obtain local demo/test accounts from the project's documentation and seed configuration; Game Master credentials are not application credentials. Use the real sign-in flow and a headless browser, never bypass auth or substitute signed-out screenshots.

Before reporting a missing fixture, inspect all resolved public input files and the supplied seed/check loader's input contract. Use local fixtures where provided. When requirements permit generating test data, create a bounded local fixture from the stated schema/cases, run the supplied loader, and label its provenance accurately. Narrative references to a QA store do not authorize external searches or prove that a local fixture is missing. Never invent an official file, signature or hash when a specific externally supplied artifact is truly required.

Every reference in `evidenceFiles`, test results, and WorkerResult must resolve to a nonempty file within this item's directory; reject absolute paths, traversal, and escaping symlinks. Keep secrets out of evidence. Root verifies required coverage and committed contents at the exact revision being pushed/submitted, rather than merely checking working-tree existence.
