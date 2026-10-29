---
name: team-orchestrator
description: Run the MTM root lifecycle, native parallel workers, verified integration, and once-only submissions.
---

# MTM root

Player owns this single Codex session. Use only native subagents for match workers; never launch nested Codex/other agent CLIs. In production, skills/profiles are frozen. Use current descriptor values, approved origins and assigned workspace only; never log/persist credentials, inspect hidden tests/tasks, or access another team's data. On host approval blocks report `HOST_APPROVAL_REQUIRED` with the exact reason and preserve progress.

For PREPARE/RESUME, follow `game-preparation`/`team-recovery`. For `START_GAME`, call `configure_session(descriptor)`, `login_match()` as needed, then `refresh_match()`. All contest API traffic, durable submission identity, and progress use `mtm_minimal`. Append validated events to the exact progress log with business keys, never truncate it.

## Scheduling loop

1. Refresh the public match projection throughout play; new items can appear at any time. Trust server status/time, including pause-adjusted deadlines. Discover all visible items and report status. Continue until FINISHED, even when the current queue is empty.
2. Use `plan_tasks()` for deterministic priorities, capacity, deadlines, and ready work. Use configured normal capacity while reserving urgent capacity for PI/deadline work. Do not wait for long workers or a batch to finish before checking releases or submitting ready items. Wait only with bounded `wait_for_update(timeoutMs)`/native waits that return before the next dispatch/deadline check.
3. For each selected OPEN item, root calls `start_work(backlogItemKey)` first and retains its exact `submitTemplate` and announced MVS. Then `claim_task(backlogItemKey, workerId, estimatedSeconds)` reserves ownership/worktree and returns TaskEnvelope. Spawn the selected native worker with `fork_turns="none"`, that sanitized envelope, explicit owned paths/result location, and only needed skills. Never forward the credential-bearing descriptor. Source/answers use `worktreeRoot/repository.folder`; preserve the folder prefix in Git paths. Keep shared test state isolated.
4. Workers investigate, implement, verify, and return WorkerResult; they do not push or call Submit. Root calls `record_worker_result(backlogItemKey, workerResult)` and promptly handles ready work. For `overdueWorkers`, checkpoint/interrupt with native tools, confirm the worker stopped, then call `reconcile_worker` before retry/reassignment. Failed attempts report real diagnostics; retry only within budget. Never release a live worker's capacity or discard artifacts.

## Integration and submission

Serialize integration/push/Submit; workers continue independent work. Prioritize urgent ready items. Acquire `integration_gate(backlogItemKey, action="begin")` before integration. The root alone holds this lock through verification, push, and Submit. On failure, stop mutations and reconcile Git/push state before releasing with `action="failed"`; successful `submit_work` releases it on the real receipt. Verify the patch is confined to permitted paths, integrate into the assigned branch, resolve conflicts, and rerun affected verification. Source, answers, and evidence stay under `matchRoot = checkoutRoot/repository.folder`.

For every item, cover all required acceptance/MVS scenarios and exact evidence requirements, including requested before-change captures. Keep actual command outcomes, including `command`, `passed`, and `exit_code=0` for passing WorkerResult tests. Require a meaningful validation command even for documentation/artifact TASKs. Demonstrated unrelated baseline failures do not block a solved item; new regressions, omitted scenarios, fabricated PASS, or missing evidence do.

Fill `answers/<key>/submit.json` exactly from the received template. Before dispatch, deterministic guards must verify answer/payload parity, complete passing required results, nonempty contained evidence in the exact commit tree, permitted diff, full SHA, assigned branch, ordinary push, and remote ref parity. Never force-push or alter immutable/secret/agent files. Workers' READY status alone is insufficient.

As soon as the item is verified, committed, and pushed, call `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)` without waiting for other items. Preserve the journal's canonical request bytes and idempotency identity across uncertainty; reconcile rather than submit a second solution. Record a real server receipt; ACCEPTED is not graded PASSED. Track grading and all item outcomes. A solved item left unsubmitted is unfinished work.

On PAUSED, halt new work/submission dispatches and honor Player controls; on FINISHED stop descendants and finalize logs. CLOSED items are terminal; record and advance without retry loops. Keep unresolved blockers visible while other eligible tasks continue.
