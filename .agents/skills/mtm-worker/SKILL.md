---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker (Ephemeral Sandbox & Zero Over-Engineering)

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

**First step:** the Root hands you a compact TaskEnvelope. If it has `full_envelope_path`, read that JSON file before anything else: it holds the full description, `metadata.start_details` (submit template, MVS, delivered files; the files are also already written into your worktree under `challenge/`), and `metadata.company_context` (official KB / logs). Everything below that mentions TaskEnvelope fields refers to that full file.

## Core Sandbox & Anti-Over-Engineering Rules

1. **Mandatory ProjectRuntime Sandbox Execution (`run` & `session`)**:
   - Your worktree `src/node_modules` is already pre-linked by `claim_task`. **Never install dependencies or start dev/prod servers by hand; use ProjectRuntime (`run`, `session`, `serve-start`) or the README commands the item names.**
   - Run ALL test, typecheck, seed, and check-runner commands inside the isolated `ProjectRuntime` sandbox (which injects in-memory secrets, a worker-private `DATABASE_URL` copy of the seeded database, and worker-isolated `TEST_DATABASE_URL`; seeds you load are visible only to your own server and screenshots):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <command>
     ```
   - Run ALL browser / HTTP / Playwright evidence captures inside the **Ephemeral Web Session Sandbox** (which starts `next dev` on a conflict-free loopback `$PORT`, waits for readiness, executes your script, and kills the entire process tree on exit):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- <command>
     ```
     Inside your capture script, always read `const baseUrl = \`http://127.0.0.1:\${process.env.PORT || 3000}\`;`.
   - **Several captures or checks against the app (BUG-01, BUG-02, CR-01, CR-03 …): keep one server for the whole item** instead of one `session` per capture:
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" serve-start            # dev server, reused if already up
     python3 "<metadata.project_runtime_path>" ... run -- node capture-all.mjs          # every screenshot / HTTP check in one script, against $PORT
     python3 "<metadata.project_runtime_path>" ... serve-stop                           # ALWAYS before writing worker_result.json
     ```
     `serve-start --server-script start` runs the production server after `pnpm build` (BUG-01 production check). If `serve-start` does not print `"status": "READY"`, or a capture gets "connection refused", fall back to `session`. A `session` call stops the persistent server on the same port, so do not mix them mid-capture.

2. **Pre-Fetched Official Fixtures & MVS Tests (Zero Fixture Missing)**:
   - Root automatically pre-fetches official QA fixtures (`answers/<key>/evidence/<key.lower()>-test-data.json` + `.source.json`) and company KB/Logs (`metadata.company_context`) during `start_work` / `claim_task`.
   - Always check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. If it exists, **use it directly and NEVER overwrite it with synthetic data!**
   - If `challenge/<KEY>-mvs.test.ts` exists, copy it unchanged to the path the MVS file names (older projects: `src/tests/<key.lower()>-mvs.test.ts`) and always commit it; the grader replays it.

3. **Triage by what the item needs (Zero Over-Engineering)**:
   - Follow your type skill (`mtm-bug-worker`, `mtm-cr-worker`, `mtm-task-worker`, `mtm-pi-worker`): it says what to
     read first and in which order to earn points. Use the commands the item's own files give (MVS file, run-checks
     scripts, `env-require/README.md`); the project may use `yarn`/`node .../vitest.mjs` or `pnpm`.
   - **No UI evidence required** (pure logic, trace, data, report-only): never start a web server or browser.
   - **Logs + a few screenshots**: produce logs with the official commands, then ONE server/session pass for all PNGs.
   - **Official seed/loader + UI**: load the official data with the provided script first, then capture everything in
     one pass.

4. **Deadline Protection & Output Contract**:
   - If a UI scenario fails or deadline is within 2 minutes, mark only the uncaptured scenario `NOT_RUN` in `submit.json` (`status: "PARTIAL"`), NEVER fabricate fake/placeholder PNGs, and commit immediately.
   - Always `git add` and `git commit` all modified source files, `src/tests/<key.lower()>-mvs.test.ts`, `answers/<key>/submit.json`, and `answers/<key>/evidence/*` inside your worktree before writing `worker_result.json`.
   - **Checkpoint and final result (attempt-scoped)**: while working, atomically rewrite `worker_checkpoint.json` (`version`, `timestamp`, `worker_id`, `attempt_id`, progress) after each passing part; it never ends your task. Write `worker_result.json` atomically (write a temp file, then rename) only when finished, with the envelope's `worker_id` and `attempt_id`. A result from another attempt is rejected.
   - **Honest test rows**: every executed check stays in `test_results` with `passed=true/false` and its real `exit_code`; never move a failing row elsewhere to obtain DONE. A failure unrelated to your change may go to `diagnostic_test_results` only with `unrelated: true` and `baseline_log` pointing to a log of the same failure on the base commit.
   - **Deadline flush**: when the Root relays `DEADLINE_FLUSH_REQUESTED`, stop starting new work, commit what passes, set unfinished scenarios honestly (`FAIL`/`NOT_RUN`, status `PARTIAL`), and write the final `worker_result.json` immediately.

5. **Incremental delivery (every item)**:
   - Plan the acceptance criteria / test cases first, then deliver them in priority order. After each part that genuinely passes, commit it and update `answers/<key>/submit.json` (real PASS/FAIL/NOT_RUN rows, `status: "PARTIAL"` until everything passes).
   - Do not wait for the whole item to be finished before committing: an uncommitted half-done item scores nothing.
   - When the budget ends or Root asks you to stop, stop coding, make sure the last committed `submit.json` matches what is really verified, and write `worker_result.json`.
   - **Production logs**: for any count, first/last entry or sum over logs, use the MCP tool `collect_logs(query, service, outPath)` (it returns every entry, not just the newest 20) and compute with a script over the saved file.
