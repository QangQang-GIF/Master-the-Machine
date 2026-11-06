---
name: team-orchestrator
description: Run the MTM root lifecycle, native parallel workers, verified integration, and once-only submissions.
---

# MTM root

Player owns this single Codex session. Use only native subagents for match workers; never launch nested Codex/other agent CLIs. In production, skills/profiles are frozen. Use current descriptor values, approved origins and assigned workspace only; never log/persist credentials, inspect hidden tests/tasks, or access another team's data. On host approval blocks report `HOST_APPROVAL_REQUIRED` with the exact reason and preserve progress.

For PREPARE/RESUME, follow `game-preparation`/`team-recovery`. For `START_GAME`, call `configure_session(descriptor)`, `login_match()` as needed, then `refresh_match()`. Use native MCP or the same persistent `runner.control` stdio bridge described in preparation; keep its session alive across phases. Missing MCP discovery does not mean contest APIs are unavailable. Both transports use identical deterministic guards, journal and progress logger. Never handwrite unvalidated status or fabricate readiness. Append validated events to the exact progress log with business keys.

## Scheduling loop

1. Refresh the public match projection throughout play; new items can appear at any time. Trust server status/time, including pause-adjusted deadlines. Discover all visible items and report status. Continue until FINISHED, even when the current queue is empty.
2. **5-Worker Architecture & Deadline-First Scheduling (`plan_tasks()`)**:
   - Total capacity is 5 concurrent workers (`max_workers=5`, `normal_workers=4`); `plan_tasks()` reports the live value, always trust it.
   - **4 normal workers + 1 strong worker.** `claim_task` decides the lane and returns it in `taskEnvelope.lane`, `model`, `reasoning_effort` and `agent_profile`. The strong worker (`team_strong_worker`, gpt-6.1-sol / high) takes a Heavy item first; when no Heavy item is waiting it runs a normal item, but only once all 4 normal workers are busy. Always spawn with the returned `agent_profile` (and pass model / reasoning effort to the native spawn when it accepts them).
   - **PI and deadline items first**: no slot is kept empty, but `plan_tasks()` always lists PI, then deadline items, before anything else. Dispatch them the moment any slot frees.
   - All 5 workers run at 100% capacity on the general backlog. Eliminate all score/ROI-based prioritization: all general tasks are of equal value since the objective is 1000/1000 points within the match limit.
   - **Preemptive Deadline Priority on Worker Completion**: When any general worker finishes and frees up a slot, if the backlog contains any task with an active countdown deadline (e.g. `BUG-02`, `TASK-03`), the orchestrator MUST dispatch that deadline task FIRST before any timeless task.
   - Do not wait for long workers or a batch to finish before checking releases or submitting ready items. Wait only with bounded `wait_for_update(timeoutMs)`/native waits that return before the next dispatch/deadline check.
3. For each selected OPEN item, call `start_work(backlogItemKey)` and retain its exact template/MVS. The helper resolves only that item's publicly announced paths, including post-clone documents; never scan or checkout other tasks. `unavailableAnnouncedFiles` reports a declared path absent from the assigned branch without inventing its content. If official MVS/checkers and the task brief provide sufficient requirements, proceed from those; otherwise report the precise missing input. Then `claim_task(backlogItemKey, workerId, estimatedSeconds)` returns ownership/worktree and TaskEnvelope. Spawn the selected native worker with `fork_turns="none"`, sanitized envelope, owned paths/result location and needed skills. Never forward credentials. Source/answers use `worktreeRoot/repository.folder`; preserve the Git prefix and isolate test resources.
4. Workers investigate, implement, verify, and return WorkerResult; they do not push or call Submit. Root calls `record_worker_result(backlogItemKey, workerResult)`.
   - **Pipelined Dispatch (Mandatory)**: BEFORE integrating or submitting the completed item, immediately call `plan_tasks()`. If slots are available, immediately call `start_work` + `claim_task` and spawn the next worker. Always keep all worker slots 100% occupied. Workers must never wait for submissions to take on new tasks.
   - For `overdueWorkers`, checkpoint/interrupt with native tools, confirm the worker stopped, then call `reconcile_worker` before retry/reassignment. Failed attempts report real diagnostics; retry only within budget. Never release a live worker's capacity or discard artifacts.

## Heavy items

- `claim_task` marks a Heavy item with `taskEnvelope.heavy=true`, `model="gpt-6.1-sol"`, `reasoning_effort="high"` and `agent_profile="team_heavy_worker"`; its budget is the whole remaining window minus a 300 s hand-off margin. Heavy items are dispatched before other timeless items.
- Spawn a Heavy worker with the `agent_profile` from its envelope (`team_heavy_worker`), and pass the envelope's model and reasoning effort to the native spawn when the spawn tool accepts them. If that profile cannot be spawned, spawn the usual worker profile with the same envelope instead of waiting.
- One worker owns one Heavy item. Do not split it across workers.
- A Heavy item does not have to reach DONE. When its worker is listed in `overdueWorkers`, stop it, record its result and integrate and submit the committed work as an honest PARTIAL in the same loop. Never let a Heavy item end the match unsubmitted.
- Keep calling `wait_for_update`/`plan_tasks` until the match is FINISHED. Never end your turn while the match is running.

## Integration and submission

**STRICT ROLE BOUNDARY & IMMEDIATE SUBMISSION (Root Orchestrator)**:
- **No Idle Queues**: Con nào làm xong là phải nộp liền! As soon as a worker returns a result, immediately integrate and submit. Never let a completed worker wait in an integration queue while deadlines expire.
- **Immediate Remediation on Submission Feedback**: If `submit_work` or Game Master returns ANY error (e.g. missing evidence file, schema mismatch, or parity error), DO NOT mark `BLOCKED` or abandon the task! Immediately remedy the issue: synthesize/capture the missing evidence file, update `submit.json`, commit, and re-submit immediately.
- **Mandatory Submission Before Slot Reassignment**: An item MUST be successfully submitted (server receipt received) before reassigning that worker or releasing the item.
- The Orchestrator integrates git: merge/cherry-pick -> verify/auto-heal submit.json & evidence -> git push -> `submit_work` RPC -> receive receipt.
- The integration lock has an 8-second ceiling to ensure fast, pipelined submission without deadlocks.

Serialize integration/push/Submit; workers continue independent work. Prioritize urgent ready items. Acquire `integration_gate(backlogItemKey, action="begin")` before integration. The root alone holds this lock through verification, push, and Submit. On failure, do not abandon: resolve missing files, auto-heal evidence, reconcile Git/push state, and re-submit; successful `submit_work` releases it on the real receipt. Verify the patch is confined to permitted paths, integrate into the assigned branch, resolve conflicts. Source, answers, and evidence stay under `matchRoot = checkoutRoot/repository.folder`.

For every item, cover all required acceptance/MVS scenarios and exact evidence requirements, including requested before-change captures. All declared evidence files MUST exist as nonempty files in `answers/<key>/evidence/`. If browser capture times out or cannot settle, capture a diagnostic screenshot of the current app or write a diagnostic report; NEVER omit required evidence files from the commit.

Fill `answers/<key>/submit.json` exactly from the received template. Before dispatch, deterministic guards verify answer/payload parity, complete passing required results, nonempty contained evidence in the exact commit tree, permitted diff, full SHA, assigned branch, ordinary push, and remote ref parity. Never force-push or alter immutable/secret/agent files.

As soon as the item is verified, committed, and pushed, call `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)` immediately without waiting for other items. Preserve the journal's canonical request bytes and idempotency identity across uncertainty; reconcile rather than submit a second solution. Record a real server receipt; ACCEPTED is not graded PASSED. Track grading and all item outcomes. A solved item left unsubmitted is unfinished work.

On PAUSED, halt new work/submission dispatches and honor Player controls; on FINISHED stop descendants and finalize logs. CLOSED items are terminal; record and advance without retry loops. Keep unresolved blockers visible while other eligible tasks continue.
