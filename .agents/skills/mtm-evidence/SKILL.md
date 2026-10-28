---
name: mtm-evidence
description: Procedural skill for capturing, formatting, and verifying audit evidence (logs, diffs, execution traces, test outputs).
---

# MTM Evidence Skill

Specialized skill for producing first-class audit evidence required by backlog tickets or tournament rules.

## Evidence Artifacts

1. **Dynamic Requirement Extraction**:
   - Parse `challenge/<key>-minimum-verification-scenarios.md` and task descriptions dynamically to discover all required evidence files (screenshots, reports, test logs).
   - If any baseline evidence is requested before changes are made, capture it immediately BEFORE modifying code.
2. **Test Logs**:
   - Save full output of passing verification commands to workspace artifact files under `answers/<key>/evidence/`.
3. **Git Diff Evidence**:
   - Capture clean diffs when required to verify only intended files were modified.
4. **Trace Log**:
   - Ensure execution traces are recorded and listed under `evidence_paths` when required.
5. **Physical Existence Guarantee & Submission Gate**:
   - Every path declared in `evidenceFiles` and `testResults[].evidence` MUST physically exist on disk under `answers/<key>/evidence/` and in the Git commit before submitting.
   - The Submission Gate will reject submissions if any declared evidence file is missing.
6. **Safe File Capture**:
   - For downloaded files or data exports, write the exact content directly into `answers/<key>/evidence/<file>` without relying on browser download events.
   - For screenshots, use explicit non-blocking capture with viewport set to at least 1280x720.
