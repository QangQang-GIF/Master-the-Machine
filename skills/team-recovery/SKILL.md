---
name: team-recovery
description: Resume an authorized MTM preparation/game session while preserving work, receipts, and submission identity.
---

# Recovery

Run only on Player's `RESUME_PREPARATION_V1` or `RESUME_GAME_V1`. Read the exact progress log, coordinator/journal state, current checkout, and server projection first. Preserve `playerSessionId`, existing worktrees, verified checkpoints, client submission IDs, request bytes, and receipts. Never wipe/reclone or spawn another CLI. Continue append-only telemetry.

Call `configure_session(descriptor)` with the fresh recovery frame. For preparation, rerun only incomplete or invalid gates from `game-preparation`; retain valid readiness and avoid duplicate events. For active play, reauthenticate at the approved origin and reconcile worker ownership, server-clock deadlines, and authoritative item/submission state before dispatching.

- Accepted submission with a real receipt: do not submit again; continue tracking grading separately.
- Ambiguous in-flight submission: reconcile with server using the existing journal identity; never generate a new submission to bypass uncertainty.
- Server `BACKLOG_ITEM_CLOSED`: record closure using the supported progress schema, stop that item, and advance. Do not reauthenticate/retry indefinitely.
- Verified ready work: prioritize integration/submission while still open; do not rerun implementation unnecessarily.
- Orphan worker: retain its slot until confirmed stopped; preserve its work before reassignment.

Continue the `team-orchestrator` loop. Respect PAUSED/FINISHED immediately. Recovery is not permission for intervention, skill edits, or self-restart during production.
