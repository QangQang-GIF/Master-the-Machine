---
name: mtm-evidence
description: Capture, validate, and reference official MTM evidence artifacts and Ephemeral Web Sessions.
---

# MTM Evidence (Ephemeral Web Session Sandbox)

- **Storage Location**: Save artifacts under `matchRoot/answers/<key>/evidence/<filename>`. Reference them in `answers/<key>/submit.json` as `evidence/<filename>`.
- **Official Data First (Zero Fixture Missing)**:
  - Root automatically pre-fetches official QA fixtures to `answers/<key>/evidence/<key.lower()>-test-data.json` (with `.source.json` sidecar) before worker launch.
  - Check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. **NEVER overwrite an existing official fixture or fabricate synthetic `*-test-data.json` files!**
- **Official Check & Seed Scripts First**:
  - Whenever `challenge/<KEY>-run-checks.mjs` exists, ALWAYS generate `test.log` and `typecheck.log` by running `node ../challenge/<KEY>-run-checks.mjs` via `project_runtime.py ... run` so the Grader's strict `command: ...` and `exit_code: 0` format check passes 100%.
  - Whenever `challenge/<KEY>-seed.ts` or `challenge/<KEY>-report.ts` exists, copy it to `src/prisma/` and execute it via `project_runtime.py ... run -- pnpm tsx prisma/<script>.ts ...` (then delete the temporary copy in `src/prisma/` before `git commit`).
- **Ephemeral Web Session Mode (No Manual Servers)**:
  - Only start a web session if `submit.json` requires `.png` screenshots or live HTTP checks (`BUG-01-layout-check.mjs`, `CR-01-signed-out-check.mjs`).
  - Always invoke via:
    `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session -- node runner/fast_capture.mjs answers/<key>/evidence <route> <output.png> [tab]`
  - Inside `<capture_script.mjs>`, connect to `http://127.0.0.1:${process.env.PORT || 3000}`.
- **Fast Headless Playwright Sandbox (Zero-Overhead Capture)**:
  - Use `runner/fast_capture.mjs` for 1.5-second automated capture: it uses headless Chromium with persistent session caching (`D:/Temp/auth-state.json`) to skip repeated `/login` visits.
  - Default seed credentials: username/email `morpheusadam95`, password `change-me-please`.
  - Use desktop viewport `{ width: 1440, height: 900 }` (or `{ width: 390, height: 844 }` when mobile is required).
  - Use `{ waitUntil: "domcontentloaded" }` plus `await page.waitForTimeout(600)` instead of `networkidle` whenever possible.
  - Seed required records directly into PostgreSQL via Prisma BEFORE running `session` so the browser only has to log in, navigate to the URL, and take screenshots in < 15 seconds.
- **Honest Fallback**:
  - Never generate fake/synthetic placeholder PNGs. If a browser check fails after retry, mark that specific test case `NOT_RUN` in `submit.json` (`status: "PARTIAL"`) while keeping all passing code/unit-test scenarios as `PASS`.
