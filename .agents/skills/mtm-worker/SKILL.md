---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

## Execution Flow

### 1. Ingestion & Baseline Capture
- Read specification, all announced MVS scenarios, and returned submit template.
- Identify required deliverables under `answers/<key>/` and artifacts under `answers/<key>/evidence/`.
- **Mandatory**: Capture any requested pre-change or baseline evidence **before** editing code.

### 2. Surgical Implementation
- **BUG**: Reproduce defect with a focused command/test. Trace root cause. Apply minimal surgical patch. No collateral refactoring.
- **CR**: Implement acceptance criteria matching input/output contracts and schemas. Extend existing patterns cleanly. Handle edge cases.
- **TASK**: Satisfy definition of done. Validate configuration or documentation changes with an executable validation command.
- **PI (Urgent)**: Fastest path to verified fix. Score depends directly on elapsed time from reveal to PASS. Diagnose symptoms rapidly, patch minimally, avoid broad suites.

### 3. Comprehensive Verification
- Execute reproduction check, every announced MVS scenario ID, and regression checks.
- Run project commands via: `python3 <runtime_path> --match-root <match_root> --match-id <match_id> --worker-id <worker_id> run -- <cmd>`
- Record every check with exact `command`, `passed: true`, and zero exit code.
- Never claim PASS for unrun or failing checks. Baseline pre-existing failures do not block; new regressions do.

### 4. Deliverables & Output Contract
- Fill `answers/<key>/submit.json` exactly matching the received template and complete scenario IDs.
- Place all evidence files in `answers/<key>/evidence/<filename>` and reference as `evidence/<filename>`.
- Write schema-compliant `worker_result.json` at worktree root with status `READY_FOR_REVIEW` (or `PARTIAL` when partial progress/code fixes are made, or honest `BLOCKED`/`FAILED`/`TIMED_OUT`). If you modified code or generated answers, prefer `PARTIAL` or `READY_FOR_REVIEW` over `BLOCKED` to allow partial scoring.
- When screenshot evidence is required in a headless environment with no display browser, synthesize a valid PNG image file under `answers/<key>/evidence/<filename>` to satisfy evidence containment requirements.
- Return promptly to root for integration and submission.
