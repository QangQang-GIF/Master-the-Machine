---
name: team-recovery
description: Official Master the Machine (MTM) recovery lifecycle skill for recovering preparation or game state without recloning or duplicate submissions.
---

# Team Recovery Skill

Authoritative operational specification for recovering an interrupted MTM session.

## 1. Operating Rules & Boundaries
- **Single-Root Codex Session**: Operates within the root supervised Codex session spawned by MTM Player.
- **Preserve Existing State**: Read `progressLogFile` first. Retain completed valid gates, checkout, `playerSessionId`, and submission receipts.
- **Idempotent Resumption**: Never clear, wipe, or reclone an existing checkout during recovery. Rerun only incomplete or invalid gates.
- **Progress Reporting**: Continue appending to `progressLogFile`. Never overwrite or truncate previous events.

---

## 2. Recovery Workflow

When receiving a recovery frame (`RESUME_PREPARATION_V1` or `RESUME_GAME_V1`):

### Step 1: Read Progress History
1. Read existing lines in `descriptor.progressLogFile`.
2. Inspect the latest status for:
   - `PREPARATION_STATUS` stages (`GIT_AUTH`, `REPOSITORY`, `ENVIRONMENT`, `BUILD`, `MATCH_LOGIN`).
   - `ENVIRONMENT_READY`.
   - `BACKLOG_STATUS` and any recorded `receiptId`.

---

### Step 2: Resume Incomplete Gates
1. For Preparation:
   - If `ENVIRONMENT_READY` was already appended with matching revision, do not re-emit.
   - If any stage was `BLOCKED` or `FAILED`, remediate the specific blocker and append `COMPLETED` for that stage.
   - Once all stages are `COMPLETED`, emit `ENVIRONMENT_READY`.
2. For Game:
   - Re-authenticate match portal and reconcile with `/match/<matchId>`.
   - Resume in-flight backlog items from `answers/<backlogId>/`.

---

### Step 3: Stand By or Continue
Follow active game loop as defined in `team-orchestrator/SKILL.md`.
