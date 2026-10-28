---
name: mtm-evidence
description: Procedural skill for capturing, formatting, and verifying audit evidence (logs, diffs, execution traces, test outputs).
---

# MTM Evidence Skill

Specialized skill for producing first-class audit evidence required by backlog tickets or tournament rules.

## Evidence Artifacts

1. **Dynamic Requirement Extraction**:
   - Parse `challenge/<key>-minimum-verification-scenarios.md` and task descriptions dynamically to discover all required evidence files (screenshots, reports, test logs).
   - If any before-fix/baseline evidence is requested (e.g. `dashboard_before_fixing.png`, `before-*.png`), capture it immediately BEFORE changing code.
2. **Test Logs**:
   - Save full output of passing verification commands to workspace artifact files (e.g. `test_run.log`, `test.log`).
3. **Git Diff Evidence**:
   - Capture clean `git diff HEAD` to verify only intended files were modified.
4. **Trace Log**:
   - Ensure `trace.jsonl` is written and listed under `evidence_paths` in `worker_result.json`.
5. **Physical Existence Guarantee & Submission Gate**:
   - Every path declared in `evidenceFiles` and `testResults[].evidence` MUST physically exist on disk under `answers/<key>/evidence/` and in the Git commit before submitting.
   - The Submission Gate will reject submissions if any declared evidence file is missing.
6. **Safe File Capture**:
   - For CSV or downloaded files, generate the exact content directly into `answers/<key>/evidence/<file>.csv` without relying on browser download events.
   - For screenshots, use explicit non-blocking capture with viewport set to at least 1280x720.

