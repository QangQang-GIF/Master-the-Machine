---
name: mtm-bug-worker
description: Reproduce, minimally fix, and verify an announced MTM BUG item in its assigned worktree.
---

# BUG

Follow `mtm-init` and the TaskEnvelope. The root has already called Start Work.

1. Read the bug expectation, returned submit template, all MVS, and required artifacts. Capture requested pre-fix evidence before changing code.
2. Reproduce the defect with a focused command or test; trace its cause and patch the affected behavior without unrelated edits.
3. Re-run the reproduction, every announced MVS, and relevant regression/negative checks. Save actual logs and requested captures under `answers/<key>/evidence/`. Compare unrelated baseline failures with recorded preparation evidence; do not hide new regressions.
4. Fill `answers/<key>/submit.json` using the exact template and complete scenario IDs. Only claim PASS for executed passing checks. Every evidence reference must name a real artifact within this item's evidence directory.
5. Return `worker_result.json` promptly to the root for integration and submission. Do not push or call Submit yourself. Report real blockers with diagnostics rather than fabricate fixtures, evidence, or success.
