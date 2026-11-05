---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker (Sandbox, Evidence Lane & Zero Over-Engineering)

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

## Core Sandbox & Anti-Over-Engineering Rules

1. **Mandatory ProjectRuntime Sandbox Execution (`run`, `session-start`, `session-stop`)**:
   - Your worktree `src/node_modules` is already pre-linked by `claim_task`. **NEVER run `pnpm install`, `pnpm build`, `pnpm dev`, or `pnpm start` manually!**
   - Run ALL test, typecheck, seed, and check-runner commands inside the isolated `ProjectRuntime` sandbox (which injects in-memory secrets, `DATABASE_URL`, and worker-isolated `TEST_DATABASE_URL`):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <command>
     ```
   - For tasks whose `submit.json` needs `.png` files or live HTTP checks, start this worker's dev server in the background as your FIRST action so it compiles while you code (see `mtm-evidence`):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session-start --routes /route1,/route2
     ```
     Capture all screenshots in ONE `run -- node "<metadata.capture_multi_path>" spec.json --cutoff-epoch <taskEnvelope.evidence_cutoff_local_epoch>` call, then `session-stop`. Live HTTP check scripts use the `baseUrl` printed by `session-start`.

2. **Pre-Fetched Official Fixtures & MVS Tests (Zero Fixture Missing)**:
   - Root automatically pre-fetches official QA fixtures (`answers/<key>/evidence/<key.lower()>-test-data.json` + `.source.json`) and company KB/Logs (`metadata.company_context`) during `start_work` / `claim_task`.
   - Always check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. If it exists, **use it directly and NEVER overwrite it with synthetic data!**
   - If `challenge/<KEY>-mvs.test.ts` exists, it is automatically staged at `src/tests/<key.lower()>-mvs.test.ts`. Always keep and commit `src/tests/<key.lower()>-mvs.test.ts` in your `git add` and `changed_files`!

3. **Task-Tier Triage (Zero Over-Engineering)**:
   - **Tier 1 — Pure Logic / Unit-Test / Trace Tasks (`BUG-03`, `TASK-01`, `TASK-03`, `TASK-04`, `PI-01`)**:
     - **DO NOT launch any web server or Playwright browser** unless `submit.json` explicitly requires `.png` files in `evidenceFiles`.
     - Edit source file(s) -> run official check runner or `pnpm test` + `pnpm typecheck` via `project_runtime.py ... run` -> write required text/log evidence -> fill `answers/<key>/submit.json` (`status: "DONE"`) -> commit and finish in under 2 minutes.
   - **Tier 2 — Logic + Official Check Runner + Single UI Pass (`BUG-04`, `CR-02`, `CR-03`, `TASK-02`)**:
     - Ensure `src/tests/<key.lower()>-mvs.test.ts` is present and committed.
     - Run `node ../challenge/<KEY>-run-checks.mjs` via `project_runtime.py ... run` to produce exact `test.log` and `typecheck.log` files (`command: pnpm ...` and `exit_code: 0`).
     - Run `session-start` first, then ONE `capture_multi.mjs` pass for the required PNG(s), then `session-stop`.
   - **Tier 3 — Official Seed + Check Runner + UI Session (`BUG-01`, `BUG-02`, `CR-01`)**:
     - Copy `challenge/<KEY>-seed.ts` (and `challenge/<KEY>-report.ts` if present) into `src/prisma/` so `~/server/db` resolves cleanly when running `pnpm tsx prisma/seed-*.ts` via `project_runtime.py ... run`.
     - Run `session-start` as your first action. Seed data directly into DB via Prisma/service scripts BEFORE capturing, then run HTTP checks against the printed `baseUrl` and capture all screenshots in ONE `capture_multi.mjs` pass; finish with `session-stop`.

4. **Deadline Protection & Output Contract**:
   - `taskEnvelope.evidence_cutoff_local_epoch` is the hard stop for captures (deadline − 150s, budget end, or match end − 240s). `capture_multi.mjs --cutoff-epoch` skips shots past it; mark those scenarios `NOT_RUN` in `submit.json` (`status: "PARTIAL"`). NEVER fabricate fake/placeholder PNGs.
   - Commit early and often: root's `finalize_item` snapshots and submits your worktree automatically at the cutoff, so committed work is never lost.
   - Always `git add` and `git commit` all modified source files, `src/tests/<key.lower()>-mvs.test.ts`, `answers/<key>/submit.json`, and `answers/<key>/evidence/*` inside your worktree before writing `worker_result.json`. Writing `worker_result.json` is your final step; root submits as soon as it appears.
   - **Heavy items** (`taskEnvelope.heavy == true`): your `time_budget_seconds` is capped. Do the announced MVS scenarios first, avoid exploring outside the task scope, commit after each passing scenario.

## Stop / Handoff Instruction (V2 safe stop)

If Root sends a control instruction with `state: HANDOFF` (or `CODE_FREEZE_REQUESTED`), stop nonessential work immediately:
commit what exists, write an honest `worker_result.json`, STOP writing any file, and then write
`.mtm_stopped.json` in your match root containing `{"instructionId": "<the instructionId from the instruction>"}`.
Write it last. Root snapshots your worktree only after it sees this acknowledgement (or has ended you natively), so any
write after the acknowledgement is ignored. Never write `submit.json` after a handoff instruction.
