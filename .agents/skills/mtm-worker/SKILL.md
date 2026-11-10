---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker (Ephemeral Sandbox & Zero Over-Engineering)

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

**First step:** if the TaskEnvelope has `full_envelope_path`, read that JSON file first: it holds the description, `metadata.start_details` (submit template, MVS, `delivered_files` already written into your worktree) and `metadata.company_context_files` (one file per prefetched KB/log result). Open a delivered or context file only when needed; read large data (fixtures, logs, archives) with a script, never into your context.

**Credit and time discipline (everything you print stays in your context for every later turn):**
- Company tools are yours: call `call_company_tool`, `collect_logs` (absolute `outPath` in your worktree; `PENDING` means call again with the same arguments later) and `fetch_fixture` directly through the `mtm_minimal` MCP tools. Your helper forwards them to the Root helper, which holds the credentials and retries timeouts. Never ask the Root to fetch for you.
- ProjectRuntime `run`/`session` summarise long output (exit code, failing lines with log line numbers, tail, full log path); add `--log answers/<key>/evidence/<name>.log` to keep the full output as evidence. Read only line ranges of a log; never `cat` whole logs.
- A command silent for 90 s is stopped as hung (exit 124); `run` defaults to 180 s and is capped at 600 s (pass `--timeout` for a known long build). Never start a server through `run` or in the foreground; use `session` for captures.
- Run the MVS tests and the tests next to your change, with default parallelism. Run a full suite only when the brief requires it, once, at the end.
- Python runs with UTF-8 (`PYTHONUTF8=1`); read/write files with `encoding="utf-8"`.
- **Time**: stop by `finish_by_epoch` (check `python -c "import time;print(int(time.time()))"` between steps). Commit early and often (`answers/<key>/` with each step): at the item deadline the helper submits your LAST COMMIT, and for a deadline item the Game Master refuses anything later. Out of time: set unverified scenarios `NOT_RUN`, `status: "PARTIAL"`, commit, write the result.
- `worker_result.json` goes to `metadata.worker_result_path` (`<match_root>/worker_result.json`), written LAST and NEVER committed (it is git-ignored; never `git add -f` it). Paths in `changed_files`/`evidence_paths` are relative to `match_root` with forward slashes (`answers/<key>/evidence/x.log`, `src/...`), never absolute and never prefixed with the repository folder; `elapsed_seconds` is a number.
- Before writing `worker_result.json`: every `test_results` row is a real command with `passed: true` and integer `exit_code: 0`; evidence files exist, are nonempty and under `answers/<key>/evidence/`; everything is committed. Diagnostic logs go under `answers/<key>/evidence/` only, never elsewhere in the repository. The Root reads the file itself; your final message is one line.

## Core Sandbox & Anti-Over-Engineering Rules

1. **Mandatory ProjectRuntime Sandbox Execution (`run` & `session`)**:
   - Your worktree `src/node_modules` is already pre-linked by `claim_task`. **NEVER run `pnpm install`, `pnpm build`, `pnpm dev`, or `pnpm start` manually!**
   - Run ALL test, typecheck, seed, and check-runner commands inside the isolated `ProjectRuntime` sandbox (which injects in-memory secrets, `DATABASE_URL`, and worker-isolated `TEST_DATABASE_URL`):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <command>
     ```
   - Run ALL browser / HTTP / Playwright evidence captures inside the **Ephemeral Web Session Sandbox** (which starts `next dev` on a conflict-free loopback `$PORT`, waits for readiness, executes your script, and kills the entire process tree on exit):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- <command>
     ```
     Inside your capture script, always read `const baseUrl = \`http://127.0.0.1:\${process.env.PORT || 3000}\`;`.

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
     - Run ONE single `project_runtime.py ... session -- node capture.mjs` pass (capped at 45s) to capture required desktop PNG(s).
   - **Tier 3 — Official Seed + Check Runner + UI Session (`BUG-01`, `BUG-02`, `CR-01`)**:
     - Copy `challenge/<KEY>-seed.ts` (and `challenge/<KEY>-report.ts` if present) into `src/prisma/` so `~/server/db` resolves cleanly when running `pnpm tsx prisma/seed-*.ts` via `project_runtime.py ... run`.
     - Seed data directly into DB via Prisma/service scripts BEFORE opening the browser, then capture all required screenshots and HTTP checks in ONE `project_runtime.py ... session` invocation.

4. **Deadline Protection & Output Contract**:
   - If a UI scenario fails or `finish_by_epoch` is reached, mark only the uncaptured scenario `NOT_RUN` in `submit.json` (`status: "PARTIAL"`), NEVER fabricate fake/placeholder PNGs, and commit immediately.
   - Always `git add` and `git commit` all modified source files, `src/tests/<key.lower()>-mvs.test.ts`, `answers/<key>/submit.json`, and `answers/<key>/evidence/*` inside your worktree before writing `worker_result.json`.
