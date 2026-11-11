---
name: mtm-worker
description: Unified autonomous engineering worker for MTM BUG, CR, TASK, and PI items.
---

# MTM Unified Worker (Ephemeral Sandbox & Zero Over-Engineering)

Follow `mtm-init` boundaries, TaskEnvelope constraints, and returned submit template. The root coordinator owns Start Work, integration, push, and Submit.

**First step:** if the TaskEnvelope has `full_envelope_path`, read that JSON file first: it holds the description, `metadata.start_details` (submit template, MVS, `delivered_files` already written into your worktree) and `metadata.company_context_files` (one file per prefetched KB/log result). Open a delivered or context file only when needed; read large data (fixtures, logs, archives) with a script, never into your context.

**Running the app (UI scenarios, screenshots, browser exports):** if `metadata.environment_readme` exists, read its browser/serve sections FIRST and follow them exactly (build/serve commands, environment variables, which browser channel). Serve your own instance on your own port with data folders inside your worktree (never another worker's or the root's). On a fresh instance do the first-run steps it names (e.g. set a local test password, confirm it, sign in with it, open/start the budget) inside your capture script; if a sign-in page appears, sign in with the password you set instead of waiting for another button. Never mark a UI scenario NOT_RUN before trying this recipe; if it still fails, put the exact failing step in the evidence.

**Your item only:** do not message other workers. Never edit build/bundler configuration (vite/webpack/tsconfig paths) to change how workspace packages resolve: your `src/node_modules` already resolves workspace packages to YOUR worktree's source. If your brief says your item needs another item's fix (e.g. "right only on source that also contains the BUG-11 fixes"), take it from the root branch once that item is submitted (`git rebase <assigned branch, e.g. main>` in your worktree, never a merge commit; the branch is shared), otherwise cherry-pick that worker's latest commits once, right before your final commit, and say so in your final line. Send the Root one final line when done, no progress messages.

**Credit and time discipline (everything you print stays in your context for every later turn):**
- Company tools are yours: call `call_company_tool`, `call_company_tools` (many lookups in ONE call, e.g. all KB articles or log queries you need, with `outPath` to keep results out of your context), `collect_logs` (EVERY matching log entry to a file: use it for any count, sum or first/last entry, since `search_logs` returns only ~20 entries per call; absolute `outPath` in your worktree; `PENDING` means call again with the same arguments later; search matches whole words, so a date is not a day filter: filter by `ts` in your script) and `fetch_fixture` directly through the `mtm_minimal` MCP tools. Never page `search_logs` day by day or query KB articles one per turn. Your helper forwards them to the Root helper, which holds the credentials and retries timeouts. Never ask the Root to fetch for you.
- ProjectRuntime `run`/`session` summarise long output (exit code, failing lines with log line numbers, tail, full log path); add `--log answers/<key>/evidence/<name>.log` to keep the full output as evidence. Read only line ranges of a log; never `cat` whole logs.
- A command silent for 90 s is stopped as hung (exit 124); `run` defaults to 180 s and is capped at 600 s (pass `--timeout` for a known long build). Never start a server through `run` or in the foreground; use `session` for captures.
- Run the MVS tests and the tests next to your change, with default parallelism. Run a full suite only when the brief requires it, once, at the end.
- Python runs with UTF-8 (`PYTHONUTF8=1`); read/write files with `encoding="utf-8"`.
- **Commit early and often** (`answers/<key>/` with each step): for a deadline item the helper submits your LAST COMMIT about 2.5 minutes before the deadline, and the Game Master refuses anything later.
- `worker_result.json` goes to `metadata.worker_result_path` (`<match_root>/worker_result.json`), written LAST and NEVER committed (it is git-ignored; never `git add -f` it). Paths in `changed_files`/`evidence_paths` are relative to `match_root` with forward slashes (`answers/<key>/evidence/x.log`, `src/...`), never absolute and never prefixed with the repository folder; `elapsed_seconds` is a number.
- **Submit schema (a schema-invalid submit.json scores 0 for the WHOLE item)**: fill every field `challenge/<key>-submit.schema.json` requires with real values (never `{}`, `[]`, `""` or a placeholder where the schema needs content). Before finishing run the command in `metadata.submit_schema_check` and fix every error it lists (`"valid": true` or no schema = done). `input_warnings` in your envelope only name inputs that were skipped or absent: work from the brief.
- Before writing `worker_result.json`: every `test_results` row is a real command with `passed: true` and integer `exit_code: 0`; evidence files exist, are nonempty and under `answers/<key>/evidence/`; everything is committed. Diagnostic logs go under `answers/<key>/evidence/` only, never elsewhere in the repository. The Root reads the file itself; your final message is one line.

## Core Sandbox & Anti-Over-Engineering Rules

1. **Mandatory ProjectRuntime Sandbox Execution (`run` & `session`)**:
   - Your worktree's dependencies (`node_modules`) are already linked by `claim_task`. **Never run a package install (`yarn install`, `pnpm install`, `npm install`) and never start a dev/build server by hand.**
   - Run ALL test, typecheck, seed, and check-runner commands inside the isolated `ProjectRuntime` sandbox (which injects in-memory secrets, `DATABASE_URL`, and worker-isolated `TEST_DATABASE_URL`):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" run -- <command>
     ```
   - Run ALL browser / HTTP / Playwright evidence captures inside the **Ephemeral Web Session Sandbox** (which starts the project's dev/serve script on a conflict-free loopback `$PORT`, waits for readiness, executes your script, and kills the entire process tree on exit):
     ```bash
     python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- <command>
     ```
     Inside your capture script, always read `const baseUrl = \`http://127.0.0.1:\${process.env.PORT || 3000}\`;`.
     Without a `dev` script `session` falls back to `start`/`serve`/`preview`. When `env-require/README.md` gives the serve command (e.g. a sync server with its own data folders), pass it as is: `session --server-cmd "<that command>" [--server-cwd <folder>] [--server-env KEY=VALUE ...] --url http://127.0.0.1:$PORT/<path> -- <check command>`; data folders go inside your worktree. Write `yarn`/`pnpm` as the README does: bundled Yarn, corepack and `node_modules/.bin` tools are resolved for you (never replace them with absolute paths or edit configs). Exit 2 with a script list means the command was wrong; fix it instead of marking the scenario NOT_RUN.

2. **Pre-Fetched Official Fixtures & MVS Tests (Zero Fixture Missing)**:
   - Root automatically pre-fetches official QA fixtures (`answers/<key>/evidence/<key.lower()>-test-data.json` + `.source.json`) and company KB/Logs (`metadata.company_context`) during `start_work` / `claim_task`.
   - Always check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. If it exists, **use it directly and NEVER overwrite it with synthetic data!**
   - If `challenge/<KEY>-mvs.test.ts` exists, copy it UNCHANGED to the location the MVS document names (e.g. `packages/loot-core/src/server/mtm/<key>-mvs.test.ts`) and commit it there; the helper's convenience copy at `src/tests/<key.lower()>-mvs.test.ts` is only the location when the MVS names none.

3. **Triage by what the evidence needs (Zero Over-Engineering)** — decide from the MVS "Required evidence" lines and the submit template, never from the item number:
   - **Only logs/values/files required** (pure logic, trace, data computation, test suites, incidents): **no web server, no browser.** Edit -> run the MVS/check command and the tests next to the change via `project_runtime.py ... run` -> write the required logs -> fill `submit.json` -> commit.
   - **A screenshot, export or HTTP capture is required**: do the logic and logs first, then capture everything in ONE `project_runtime.py ... session` pass that follows `env-require/README.md`, at the viewport the MVS states.
   - **The item ships seed/report/check scripts** (`challenge/<KEY>-seed.*`, `-report.*`, `-run-checks.*`): run them as their header or the MVS says (where to copy them, which runner, which arguments) BEFORE any capture, on the official fixture only; remove temporary copies the brief says not to commit.

4. **Official data and the grader's own path (every item type)** — 5FW1TZ38: CR-07 lost 42/70 because the official data was only going to be loaded through the UI (never done; the source also crashed on that data, unseen), TASK-07 lost 14/45 because its harness called an inner function instead of the path the grader uses:
   - When the brief gives official test data or says the grader loads data / replays a flow with your source, FIRST write a server-side test (in the project's test runner, via `project_runtime.py ... run`) that loads that data exactly as its description says, through the application's public handlers, and produces every figure, file and summary the answer needs (e.g. `auditSummary`, the CSV, counts, balances). Fill `submit.json` and the evidence from that output, and fix any crash it shows before anything else. Keep the test file (or the same steps) as evidence.
   - Call the entry point the brief or grader names (the handler or flow behind it, such as the import dialog's `transactions-import` handler and its preview/selection steps, with every setting the brief lists), never a lower-level function it happens to call; when unsure, run both and investigate any difference.
   - UI screenshots and browser downloads come LAST, from the same data, in one `session` pass; if they cannot be finished, the server-side figures and files are still submitted.

5. **Deadline Protection & Output Contract**:
   - If a UI scenario fails or the item deadline is near, mark only the uncaptured scenario `NOT_RUN` in `submit.json` (`status: "PARTIAL"`), NEVER fabricate fake/placeholder PNGs, and commit immediately.
   - Always `git add` and `git commit` all modified source files, the MVS test copy, `answers/<key>/submit.json`, and `answers/<key>/evidence/*` inside your worktree before writing `worker_result.json`.
