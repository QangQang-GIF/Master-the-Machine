---
name: mtm-evidence
description: Procedural skill for capturing, formatting, and verifying audit evidence (logs, diffs, execution traces, test outputs).
---

# MTM Evidence Skill

Specialized skill for producing first-class audit evidence required by backlog tickets or tournament rules.

## Evidence Artifacts

1. **Test Logs**:
   - Save full output of passing verification commands to workspace artifact files (e.g. `test_run.log`).
2. **Git Diff Evidence**:
   - Capture clean `git diff HEAD` to verify only intended files were modified.
3. **Trace Log**:
   - Ensure `trace.jsonl` is written and listed under `evidence_paths` in `worker_result.json`.
4. **Physical Existence Guarantee**:
   - Every path declared in `evidence_paths` MUST physically exist on disk before the worker finishes.
