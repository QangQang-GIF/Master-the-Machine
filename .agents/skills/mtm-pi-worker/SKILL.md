---
name: mtm-pi-worker
description: Specialized emergency skill for Production Incidents (PI) prioritizing triage speed, root cause isolation, and rapid stabilization.
---

# MTM Production Incident (PI) Worker Skill

Specialized emergency workflow for high-severity customer/production incidents (`PI`).

> **CRITICAL NOTE**: Production Incidents incur latency penalties in MTM scoring. Speed of correct resolution is paramount.

## PI Pipeline

```text
Urgent Triage (Read stack trace / symptom)
      ↓
Fast Localization (rg for error message or exception class)
      ↓
Quickest Reliable Stabilization / Fix
      ↓
Reproduction & Health Verification
      ↓
Immediate READY_FOR_REVIEW
```

## Step-by-Step Instructions

1. **Urgent Triage**: Focus strictly on the exception, panic, crash log, or 500 error reported in `TASK.md`.
2. **Fast Localization**: Jump directly to the failing code line or component using `rg`. Do not spend time reading unrelated architecture.
3. **Stabilization Fix**: Implement the safest and quickest fix that prevents the crash, handles the nil/null pointer, restores data flow, or catches the unhandled edge condition.
4. **Health Check**: Run the reproduction command or affected unit test.
5. **Fast Submission**: Immediately write `worker_result.json` with status `READY_FOR_REVIEW` and elapsed time. Do not run long irrelevant full-repo benchmark suites unless required.
