---
name: mtm-pi-worker
description: Specialized emergency skill for Production Incidents (PI) prioritizing triage speed, root cause isolation, and rapid stabilization.
---

# MTM Production Incident (PI) Worker Skill

Specialized emergency workflow for high-severity customer/production incidents (`PI`) and items with strict deadlines.

> **CRITICAL RULE**: Production Incidents incur steep latency penalties and often have hard deadlines (e.g. 15-20 minutes from reveal).
> **SPEED OF CORRECT RESOLUTION AND IMMEDIATE SUBMISSION IS PARAMOUNT.**

## PI Emergency Pipeline (< 3 Minutes Target)

```text
1. Immediate Start Work & Scenario Ingestion
          ↓
2. Fast Surgical Fix (apply isolated code fix)
          ↓
3. Targeted Minimal Verification (run only task-specific checks)
          ↓
4. Single-Item Commit & Immediate Push
          ↓
5. Direct API Submission (do NOT wait to batch with other tasks)
```

## Step-by-Step Instructions

1. **Urgent Start Work & Data Ingestion**:
   - Call `mtm_minimal/start_work` or Game Master API immediately.
   - Read the minimum verification scenarios and load required test fixtures directly using the provided seed/loader scripts.
2. **Fast Surgical Fix**:
   - Jump directly to the failing code, service, or router.
   - Apply the targeted fix without modifying unrelated code.
3. **Targeted Verification Only**:
   - Run only the task-specific check runner script or targeted unit test.
   - Do NOT run heavy whole-repo rebuilds or long benchmark suites during the emergency phase.
4. **Immediate Single-Item Submission**:
   - Ensure all scenario tests evaluate to `PASS` (zero `NOT_RUN`, zero `FAIL`).
   - Copy and fill `answers/<key>/submit.json` with status `DONE`.
   - Stage and commit changed files and evidence: `git add answers/<key> src/ ... && git commit -m "fix(pi): resolve <key>"`.
   - Push immediately: `git push origin <branch>`.
   - Submit immediately: call `submit_work`.
   - Verify server receipt is `201 ACCEPTED`.
