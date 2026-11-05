# MTM Orchestrator V2 — Implementation Report (Phases 0-7)

Branch `feat/adaptive-hierarchical-orchestrator-v2`, baseline `f4d8058`. Everything below was run in a Linux
cloud container (Python 3.13, root) against the repo's own test suite. No live Game Master, no real worker CLI,
no Practice Match was available, so nothing here is an end-to-end measurement.

## What changed, by phase
| Phase | Delivered | Status |
|---|---|---|
| 0 | Branch from baseline, checksum + test baseline (`docs/V2_BASELINE_REPORT.md`) | Done; V1 benchmark not measurable here |
| 1 | `submit_work` fail-closed (no auto-heal / forced PARTIAL / `git add -A` / ignored 2nd validation); failed push no longer ignored; emergency flush no longer fabricates a passing test; claim fallbacks enforce dependency/overlap/deadline/status/clock; integration lock has a lease and is not reclaimed while push/submit is in flight; lock released when the gate rejects | Done, with one deferral (below) |
| 2 | Reserve is PI-only and never borrowable; Heavy uses normal capacity; PI may expand into idle normal slots; `production.yaml` aligned with the installer (5/3/2) and a drift test; durable events table with `ack_event` / `unacked_events` | Done |
| 3 | `tasks/triage.py`, `expected_score_priority` (with reasons), `scheduling/model_policy.py` (allowlist, fallback, effort cap, requested/actual telemetry); adaptive ordering behind `MTM_ORCH_V2_ENABLED` + `MTM_V2_ADAPTIVE_SCHEDULER` | Done as pure/flagged logic |
| 4 | `scheduling/deadline_policy.py` (marks 300/180/90, dynamic code freeze, control instruction, `submit_decision`: CR/BUG never partial); durable `deadline_control` events behind `MTM_V2_DEADLINE_CONTROL` | Logic done; worker-side delivery/ack unverified |
| 5 | `tasks/heavy_plan.py` (versioned DAG, validation, critical path, split-worth check, `SpawnGate`), `mtm-heavy-orchestrator` skill | Logic done; native child spawn NOT implemented/verified |
| 6 | `record_heartbeat`, `stale_heartbeats`, `reconcile_report` (recommend-only; releases only a provably orphaned lock) | Done at the coordinator level |
| 7 | `scripts/bench_v2_replay.py` deterministic model + `docs/V2_BENCHMARK_MODEL.json` | Model only |

## Tests
Full suite after the work: 783 passed, 4 failed, 8 skipped (15 deselected: live CLI / staging Game Master).
All 4 failures also failed at baseline (Windows read-only/permission semantics, running as root, a timing test).
A fifth baseline failure (`test_web_session` process-tree teardown) is intermittent: it failed in the baseline
run and passed in later runs. Test count rose from 740 to 787 with the new V2 tests. Frozen checksums were regenerated
with `scripts/update_checksums.py` and verify clean.

## Acceptance gates (Plan section 14) — honest status
- Safety gates: the ones covered by unit tests pass (no submit past a rejected gate, no fabricated test, no PI-reserve
  borrowing, no slot release without confirmed stop in `SpawnGate`). "No invalid submission bypasses the guard" and
  "official Player compatibility" are NOT proven end to end.
- Heavy median >= 20% faster: NOT demonstrated. In the model, the 5-slot 3/2 split makes Heavy ~6% slower (69% of runs
  degrade to sequential because the parent and other workers take the normal slots). With 4/1 or 5/1 the sample DAG
  tops out near -17.6% at 30 s per-child overhead; 20% needs overhead below roughly 26 s. This is a design risk to
  settle with real data, not a result.
- PI p95 not slower than baseline: holds in the model (PI never waits for Heavy), not measured for real.
- Score non-regression: not measurable here.

## Deferred / open (explicit)
1. Safe stop before snapshot (Plan P1): `finalize_item` still snapshots a worker that may be running. Fixing it needs a
   stop-then-finalize protocol with worker acknowledgement, which depends on native runtime behaviour.
2. Native runtime questions in Plan section 13 (per-agent model override, signalling an active worker, whether Root
   counts toward the concurrency cap, nested-agent accounting) are still unverified. Child spawn is gated, not wired.
3. `releases/prod_manifest.json` still records `native_policy` 2/1/1 and does not list the new Heavy skill; it is a
   frozen release artifact, so it needs a deliberate re-freeze.
4. Auto-finalize at the deadline now reports FAILED instead of submitting a forced PARTIAL (gate rejects it). Whether a
   TASK/PI contract permits a partial submission must be wired from the announced contract before that can return.
5. Rollout stages (shadow scheduler, canary) are not built: only the feature flags exist.
6. The 5 baseline failures need a run on the project's Windows machine.

## Update: gap-fix round
- Emergency flush now requests a stop first and promotes only after a confirmed stop; begin_integration requires quiescence under V2.
- Model routing wired into claim envelope (requested model only; actual model reported as unreported).
- Config/manifest aligned to 5 = 4 normal + 1 PI reserve; new schemas; fault-injection tests.
- Benchmark model recalibrated with real match G97MQB4T durations (CR-01 864s, BUG-01 1614s, PI-01 210s, CR-02 371s, CR-03 374s, TASK-03 151s, BUG-03 235s). Result is still a model: Heavy -24% at 30s/child overhead, -14% at 60s; gate NOT demonstrated.
- Real-match observation: losses came from unfinished items (BUG-02, BUG-04, TASK-04 never submitted/started) and PARTIAL scores (CR-01 20/140, CR-03 35/100), more than from raw speed.
- Still unbuilt/unverified: see V2_PRODUCTION_READINESS_CHECKLIST.md.

## Update: lessons from practice match RLL3DYW5 (fixes)
Findings: a stale workspace `AGENTS.md` limited Root to 1 normal worker (Root even exported MTM_NATIVE_MAX_WORKERS=2); the fail-closed gate refused honest PARTIAL (scored by Game Master); finalize depended on Root's turn (BUG-02 finalized after its deadline); never-spawned requeues inflated attempt numbers.
Fixes: honest PARTIAL accepted (answer must itself declare PARTIAL; DONE stays strict); server-side deadline watchdog thread (stop request, then snapshot after MTM_STOP_GRACE_SECONDS, default 12s, source `deadline_grace`); `stop_worker` events; `neverSpawned` on reconcile_worker; AGENTS.md/orchestrator TOML/skill text corrected to 4 normal + 1 PI reserve and installer syncs workspace AGENTS.md preserving the Player block.
Not verified: behaviour against a live Game Master and real Codex runtime (needs another practice match).

## CWQNKAPC (2026-11-05) — submission fixes and DONE-first

Result: 2/10 submitted (BUG-03 80/80, PI-01 0/160), 80 points. Every other item was blocked at submission.

| Match blocker | Cause | Fix |
|---|---|---|
| BUG-01, BUG-02, CR-01 "missing required TC-01/TC-04" | `announced_test_ids` read prose cross-references ("same conditions as TC-01") as required tests | qualified ids anywhere; bare ids only when a line declares them; bare aliases dropped |
| BUG-02 cherry-pick conflict on retry | integration re-picked already-applied commits | `git cherry` skips applied patches |
| BUG-04 "missing required evidence file" | MVS evidence check ignored honest PARTIAL | missing file only leaves its test NOT_RUN; DONE stays strict |
| STALE-INHERITED-WORKER-RESULT, CR-02 conflict markers | a worker committed `worker_result.json`; cherry-picked into the base, every later worktree inherited it | result counts only for its own `task_id`; control files removed from new worktrees, excluded from snapshots, dropped from the shared checkout |
| workers stopped at ~7 min | fixed 420 s budget | budget = time to item deadline / match end minus 240 s |

DONE-first: while a fix attempt still fits (`MTM_FIX_MIN_SECONDS`, default 300 s after the 240 s margin), a non-DONE
answer or a local gate rejection is not submitted. `finalize_item` returns `SENT_BACK_FOR_DONE`, the item is requeued,
and the next worker resumes from the previous attempt's commit with `taskEnvelope.fix_request`
(`MTM_DONE_FIRST_RETRIES`, default 3). PI is held to DONE too: a fast wrong PI scores 0 (PI-01 here), the slowest correct one still earns 60%. The deadline / match-end
auto-finalize still submits an honest PARTIAL as the last resort. `MTM_DONE_FIRST=0` restores immediate PARTIAL.

Replay (`scripts/replay_submission_gate.py`, real answers + Start Work data, placeholder evidence bytes):
deployed code rejects BUG-01/BUG-04/CR-01 with the exact match errors; current code accepts them.
Not yet measured in a real match.

## Resubmission after a failed submission

- A local gate error or a Game Master 400/422 rejection no longer ends the item. The item goes back to a worker (`SENT_BACK_FOR_DONE`) with the error text in `fix_request.gateError`, and is resubmitted once the worker finishes.
- Window needed: at least `MTM_FIX_MIN_SECONDS_ON_ERROR` (60 s) after an error. A PARTIAL answer with fewer than `MTM_FIX_MIN_SECONDS` (300 s) left is submitted as honest PARTIAL.
- 409 closed/deadline, 404/410 and `MTM_DONE_FIRST=0` keep the old terminal behaviour.
