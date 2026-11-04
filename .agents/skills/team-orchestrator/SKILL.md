---
name: team-orchestrator
description: Run the MTM root lifecycle, native parallel workers, verified integration, and once-only submissions.
---

# MTM root

Player owns this single Codex session. Use only native subagents for match workers; never launch nested Codex/other agent CLIs. In production, skills/profiles are frozen. Use current descriptor values, approved origins and assigned workspace only; never log/persist credentials, inspect hidden tests/tasks, or access another team's data. On host approval blocks report `HOST_APPROVAL_REQUIRED` with the exact reason and preserve progress.

For PREPARE/RESUME, follow `game-preparation`/`team-recovery`. For `START_GAME`, call `configure_session(descriptor)`, `login_match()` as needed, then `refresh_match()`. Use native MCP or the same persistent `runner.control` stdio bridge described in preparation; keep its session alive across phases. Missing MCP discovery does not mean contest APIs are unavailable. Both transports use identical deterministic guards, journal and progress logger. Never handwrite unvalidated status or fabricate readiness. Append validated events to the exact progress log with business keys.

## Scheduling loop (code decides, root spawns)

1. **Lanes are enforced by code.** 5 workers: 3 normal slots for every ordinary item (deadline items first, then arrival order; no score/ROI ordering, all items get done) and 2 reserved slots used only by PI and Heavy items (`[Heavy Task]` title prefix or flagged by the planner). An idle reserved slot may be lent to an ordinary item whose deadline is within 180 seconds. Never re-order or override lanes by hand.
2. **Dispatch:** call `dispatch_ready()`. For every `spawn[]` entry, immediately spawn one native worker with `fork_turns="none"`, passing its `taskEnvelope` (owned paths, result location, `selected_skills`). Spawn all entries before doing anything else. Items in `errors[]` are retried by the next dispatch. Never forward credentials. Source/answers use `worktreeRoot/repository.folder`. (`plan_tasks` → `start_work` → `claim_task` remain available only as a manual fallback.)
3. **Wait:** block only in `wait_for_update(timeoutMs=55000)`. When it returns `UPDATED`, handle it in this order, then wait again:
   - `dispatch.spawn[]` present → spawn each worker now.
   - `worker_result_file` event → call `finalize_item(backlogItemKey)` immediately.
   - `autoFinalized[]` → the item was already submitted by code before its deadline/match end; if `stopWorker` is true, stop that native worker now.
   - `deadline_near` → no action needed; code finalizes at the cutoff.
   Continue until FINISHED, even when the queue is empty.
4. Workers investigate, implement, verify, commit in their worktree and write `worker_result.json`; they never push or submit.
5. For `overdueWorkers`, checkpoint/interrupt with native tools, confirm the worker stopped, then call `reconcile_worker` before retry/reassignment. Failed attempts report real diagnostics; retry only within budget.

## Integration and submission

- **`finalize_item(backlogItemKey)` is the only submission path for worker output.** It snapshots the worktree, makes `answers/<key>/submit.json` honest against the files on disk (missing evidence → that test `NOT_RUN`, item `PARTIAL`), cherry-picks into the assigned branch, pushes, and calls Submit under the integration lock. An unresolved in-flight journal entry is replayed byte-for-byte, never resubmitted with a new identity.
- **Strict role boundary:** root never runs browsers, Playwright, dev servers or tests, never captures, regenerates or edits evidence, and never works in the main checkout on a worker's behalf. A worker result with missing evidence is submitted as PARTIAL by `finalize_item`; do not try to "fix" it.
- If `finalize_item` reports a cherry-pick conflict or a guard rejection, keep the item visible: resolve the Git conflict minimally or re-dispatch the item to a worker; never fabricate files.
- Deterministic guards verify answer/payload parity, passing required results, nonempty contained evidence in the exact commit tree, permitted diff, full SHA, assigned branch, ordinary push, and remote ref parity. Never force-push or alter immutable/secret/agent files. Record a real server receipt; ACCEPTED is not graded PASSED. A solved item left unsubmitted is unfinished work.

On PAUSED, halt new work/submission dispatches and honor Player controls; on FINISHED stop descendants and finalize logs. CLOSED items are terminal; record and advance without retry loops. Keep unresolved blockers visible while other eligible tasks continue.
