---
name: mtm-cr-worker
description: Specialized worker skill for implementing Change Requests (CR), new features, API additions, and schema extensions.
---

# MTM Change Request (CR) Worker Skill

Specialized workflow for implementing feature enhancements and change requests (`CR`) in competition environments.

## CR Pipeline

```text
Parse Requirements & Extract Dynamic Evidence Requirements
      ↓
Mandatory Pre-Change Baseline Evidence Capture (if required by spec)
      ↓
Prepare Local Test Data Fixtures (Never block on external cloud tools)
      ↓
Locate Affected Architecture / Entrypoints
      ↓
Implement Change Adhering to Standards & Existing Conventions
      ↓
Positive, Negative & Regression Test Validation
      ↓
Submission Validation Gate (All Tests PASS, Zero NOT_RUN / FAIL)
      ↓
Write submit.json & worker_result.json
```

## Step-by-Step Instructions

1. **Parse Requirements & Dynamic Evidence Extraction**:
   - Inspect the task description and verification scenario files in `challenge/`.
   - Extract all input/output contracts, API schema changes, CLI flags, and required evidence files.
   - **PRE-MODIFICATION EVIDENCE RULE**: If the scenario requires pre-change evidence (e.g. UI state before adding a new control, baseline data state), **CAPTURE THIS EVIDENCE BEFORE IMPLEMENTING ANY CODE CHANGES**.
2. **Local Test Data & Fixtures**:
   - In competition runtime, no external third-party cloud drives or external QA tools exist.
   - If the task references fixtures, seed data, or test loaders, locate them within the challenge directory or local workspace assets.
   - **NEVER REPORT BLOCKED**: Load, seed, or generate required test fixtures locally as instructed by the challenge scripts.
3. **Locate Architecture**: Identify the correct module, router, or class to extend without creating duplicate or divergent code paths.
4. **Implement**:
   - Implement the required functionality adhering strictly to existing project conventions and relevant open standards.
   - Handle data formatting, character encodings, edge cases, and internationalization as specified in the requirements.
5. **Verify All Scenarios**:
   - Run the task's check runners and test scripts provided with the challenge.
   - Verify positive paths, edge cases (empty states, special characters), and negative paths (unauthorized access, boundary limits).
   - Run full unit tests and typechecks to guarantee zero regression.
6. **Submission Validation Gate (HARD RULES)**:
   - **Status MUST be `DONE`**: Submissions marked `PARTIAL`, `BLOCKED`, or `FAIL` score 0 and are blocked by the submission gate.
   - **Zero `NOT_RUN`, Zero `FAIL`**: Every test case in `submit.json` must evaluate to `PASS`. If any check fails, resolve it before submitting.
   - **Physical Evidence Verification**: Verify that every evidence file declared in `evidenceFiles` and `testResults[].evidence` physically exists on disk under `answers/<key>/evidence/` and is committed in Git.
7. **Report**: Write `submit.json` and `worker_result.json` with status `READY_FOR_REVIEW`.

## CR Execution Rules
- **Non-blocking Operations**: When testing file exports or downloads, avoid indefinite waits for browser download events. Evaluate output data directly or write to the designated evidence path (`answers/<key>/evidence/<file>`).
- **Timeouts**: All browser and network operations must include an explicit timeout (e.g. max 5000ms).
- **Evidence Containment**: All generated files must be stored under `answers/<key>/evidence/` with relative paths.
