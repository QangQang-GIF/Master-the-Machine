---
name: mtm-evidence
description: Capture, validate, and reference official MTM evidence artifacts and Ephemeral Web Sessions.
---

# MTM Evidence (Ephemeral Web Session Sandbox)

- **Storage Location**: Save artifacts under `matchRoot/answers/<key>/evidence/<filename>`. Reference them in `answers/<key>/submit.json` as `evidence/<filename>`.
- **Required evidence per scenario**: when the MVS document says `Required evidence: \`<file>\`` under a scenario, that exact file (produced by the MVS command, e.g. `mvs-tests.log`) must be in that row's `evidence` list; extra per-case logs may follow it but never replace it (39PSWT5C BUG-11 cited `tc-01.log` instead of `mvs-tests.log`). `metadata.submit_schema_check` reports rows that miss it.
- **Official Data First (Zero Fixture Missing)**:
  - Root automatically pre-fetches official QA fixtures to `answers/<key>/evidence/<key.lower()>-test-data.json` (with `.source.json` sidecar) before worker launch.
  - Check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. **NEVER overwrite an existing official fixture or fabricate synthetic `*-test-data.json` files!**
- **Official Check & Seed Scripts First**:
  - Whenever `challenge/<KEY>-run-checks.*` exists, generate the logs it produces by running it as its header says via `project_runtime.py ... run`, so the Grader's `command: ...` / `exit_code: 0` format check passes.
  - Whenever `challenge/<KEY>-seed.*` or `challenge/<KEY>-report.*` exists, run it the way its header or the MVS says (where to copy it, which runner such as `node`/`tsx`, which arguments) via `project_runtime.py ... run`, and delete temporary copies before `git commit` when the brief says so.
- **Ephemeral Web Session Mode (No Manual Servers)**:
  - Start a web session only when a required evidence file (MVS "Required evidence" or the submit template) is a screenshot, an exported file or a live HTTP check.
  - Always invoke via:
    `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- node <capture_script.mjs>`
  - Inside `<capture_script.mjs>`, connect to `http://127.0.0.1:${process.env.PORT || 3000}`.
- **Authentication & Fast Playwright Capture**:
  - Sign-in: follow `env-require/README.md` (a fresh instance usually needs a local password that you set, confirm and then sign in with); never assume default credentials.
  - Use the viewport the MVS states; when it states none, desktop `{ width: 1440, height: 900 }` (mobile `{ width: 390, height: 844 }`).
  - Use `{ waitUntil: "domcontentloaded" }` plus `await page.waitForTimeout(800)` instead of `networkidle` whenever possible.
  - Load required records with the item's seed scripts or the application's own import/API BEFORE running `session`, so the browser only has to sign in, navigate and capture.
- **Honest Fallback**:
  - Never generate fake/synthetic placeholder PNGs. If a browser check fails after retry, mark that specific test case `NOT_RUN` in `submit.json` (`status: "PARTIAL"`) while keeping all passing code/unit-test scenarios as `PASS`.
