---
name: team-orchestrator
description: Run the MTM root lifecycle, native parallel workers, verified integration, and once-only submissions.
---

# MTM root

Player owns this single Codex session. Use only native subagents for match workers; never launch nested Codex/other agent CLIs. In production, skills/profiles are frozen. Use current descriptor values, approved origins and assigned workspace only; never log/persist credentials, inspect hidden tests/tasks, or access another team's data. On host approval blocks report `HOST_APPROVAL_REQUIRED` with the exact reason and preserve progress.

For PREPARE/RESUME, follow `game-preparation`/`team-recovery`. For `START_GAME`, call `configure_session(descriptor)`, `login_match()` as needed, then `refresh_match()`. Use native MCP or the same persistent `runner.control` stdio bridge described in preparation; keep its session alive across phases. Missing MCP discovery does not mean contest APIs are unavailable. Both transports use identical deterministic guards, journal and progress logger. Never handwrite unvalidated status or fabricate readiness. Append validated events to the exact progress log with business keys.

## Scheduling loop (code decides, root spawns)

1. **Lanes are enforced by code.** 5 workers: 4 normal slots (Normal, Heavy and their children; deadline items first, then arrival order) and 1 reserved slot used only by PI items. PI may also use an idle normal slot; Normal/Heavy work never borrows the PI reserve. The limits come only from `dispatch_ready`/`plan_tasks`: never lower them from another document, never export `MTM_NATIVE_*`, never start `runner/control.py` by hand while the `mtm_minimal` tools exist, and never defer an item because of a "workspace limit". Never re-order or override lanes by hand.
2. **Dispatch:** call `dispatch_ready()`. For every `spawn[]` entry, immediately spawn one native worker with `fork_turns="none"`, passing its `taskEnvelope` (owned paths, result location, `selected_skills`). Spawn all entries before doing anything else. Items in `errors[]` are retried by the next dispatch. Never forward credentials. Source/answers use `worktreeRoot/repository.folder`. (`plan_tasks` → `start_work` → `claim_task` remain available only as a manual fallback.)
3. **Wait:** block only in `wait_for_update(timeoutMs=55000)`. When it returns `UPDATED`, handle it in this order, then wait again:
   - `dispatch.spawn[]` present → spawn each worker now.
   - `worker_result_file` event → call `finalize_item(backlogItemKey)` immediately.
   - `finalize_item` returned `SENT_BACK_FOR_DONE` → nothing was submitted on purpose: the answer was not DONE (or the local gate rejected it) and there is still time. Make sure that native worker is stopped, then call `dispatch_ready()` and spawn the new worker; it continues from the previous commit with `taskEnvelope.fix_request`. This is not a blocker; do not open one and do not submit the item yourself.
   - `autoFinalized[]` → the item was already submitted by code before its deadline/match end; if `stopWorker` is true, stop that native worker now.
   - `deadline_near` → no action needed; code finalizes at the cutoff.
   Continue until FINISHED, even when the queue is empty.
4. Workers investigate, implement, verify, commit in their worktree and write `worker_result.json`; they never push or submit.
5. For `overdueWorkers`, checkpoint/interrupt with native tools, confirm the worker stopped, then call `reconcile_worker` before retry/reassignment. Failed attempts report real diagnostics; retry only within budget.

## Integration and submission

- **`finalize_item(backlogItemKey)` is the only submission path for worker output.** It snapshots the worktree, makes `answers/<key>/submit.json` honest against the files on disk (missing evidence → that test `NOT_RUN`, item `PARTIAL`), cherry-picks into the assigned branch, pushes, and calls Submit under the integration lock. An unresolved in-flight journal entry is replayed byte-for-byte, never resubmitted with a new identity.
- **Strict role boundary:** root never runs browsers, Playwright, dev servers or tests, never captures, regenerates or edits evidence, and never works in the main checkout on a worker's behalf. Fixing is a worker's job: `finalize_item` itself sends a non-DONE answer back to a worker while time remains (DONE-first) and submits an honest PARTIAL only at the deadline/match-end cutoff or when no fix attempt fits.
- If `finalize_item` raises a cherry-pick conflict or a guard rejection (no time left for a fix attempt), keep the item visible and report the blocker with the exact message; never fabricate files.
- Deterministic guards verify answer/payload parity, passing required results, nonempty contained evidence in the exact commit tree, permitted diff, full SHA, assigned branch, ordinary push, and remote ref parity. Never force-push or alter immutable/secret/agent files. Record a real server receipt; ACCEPTED is not graded PASSED. A solved item left unsubmitted is unfinished work.

On PAUSED, halt new work/submission dispatches and honor Player controls; on FINISHED stop descendants and finalize logs. CLOSED items are terminal; record and advance without retry loops. Keep unresolved blockers visible while other eligible tasks continue.
