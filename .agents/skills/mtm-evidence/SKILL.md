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
5. **Safe File Capture**:
   - For CSV or downloaded files, generate the exact content directly into `answers/<key>/evidence/<file>.csv` without relying on browser download events.
   - For screenshots, use explicit non-blocking capture with viewport set to at least 1280x720.

