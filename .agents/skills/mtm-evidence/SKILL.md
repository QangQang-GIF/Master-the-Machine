---
name: mtm-evidence
description: Capture, validate, and reference official MTM evidence artifacts with a background dev server and one-pass screenshot capture.
---

# MTM Evidence (Evidence Lane)

- **Storage Location**: Save artifacts under `matchRoot/answers/<key>/evidence/<filename>`. Reference them in `answers/<key>/submit.json` as `evidence/<filename>`.
- **Official Data First (Zero Fixture Missing)**:
  - Root automatically pre-fetches official QA fixtures to `answers/<key>/evidence/<key.lower()>-test-data.json` (with `.source.json` sidecar) before worker launch.
  - Check `answers/<key>/evidence/<key.lower()>-test-data.json` FIRST. **NEVER overwrite an existing official fixture or fabricate synthetic `*-test-data.json` files!**
- **Official Check & Seed Scripts First**:
  - Whenever `challenge/<KEY>-run-checks.mjs` exists, ALWAYS generate `test.log` and `typecheck.log` by running `node ../challenge/<KEY>-run-checks.mjs` via `project_runtime.py ... run` so the Grader's strict `command: ...` and `exit_code: 0` format check passes 100%.
  - Whenever `challenge/<KEY>-seed.ts` or `challenge/<KEY>-report.ts` exists, copy it to `src/prisma/` and execute it via `project_runtime.py ... run -- pnpm tsx prisma/<script>.ts ...` (then delete the temporary copy in `src/prisma/` before `git commit`).
- **Start the server FIRST, while you code (only if `submit.json` needs `.png` files or live HTTP checks)**:
  - As your first action, run: `python3 "<metadata.project_runtime_path>" --match-root "<match_root>" --match-id "<metadata.match_id>" --worker-id "<worker_id>" session-start --routes /route1,/route2` (the routes you will capture). It returns JSON with `port` and `baseUrl` and keeps compiling in the background while you edit code (`next dev` hot-reloads).
  - Live HTTP checks (e.g. `BUG-01-layout-check.mjs`, `CR-01-signed-out-check.mjs`) then run with `project_runtime.py ... run -- node ../challenge/<check>.mjs <baseUrl>...` using the `baseUrl` printed by `session-start`.
- **Capture every screenshot in ONE run**:
  - Write a small spec file OUTSIDE the Git repository folder so it is never committed, e.g. `<worktreeRoot>/../<key>-capture-spec.json`: `{"outDir": "<match_root>/answers/<key>/evidence", "shots": [{"route": "/accounting", "file": "tc-01.png", "tab": "Invoices"}, {"route": "/", "file": "mobile-390x844.png", "viewport": {"width": 390, "height": 844}}]}`.
  - Run: `python3 "<metadata.project_runtime_path>" ... run -- node "<metadata.capture_multi_path>" <spec file> --cutoff-epoch <taskEnvelope.evidence_cutoff_local_epoch>`. It logs in once (seed account), captures all shots (desktop default 1440x900), and prints a JSON report.
  - Seed required records directly into PostgreSQL via Prisma BEFORE capturing so pages render real data.
  - Finish with `python3 "<metadata.project_runtime_path>" ... session-stop`.
- **Honest Fallback**:
  - Never generate fake/synthetic placeholder PNGs. A shot reported `skipped` (cutoff reached) or failed after one retry is marked `NOT_RUN` in `submit.json` (`status: "PARTIAL"`), keeping all passing code/unit-test scenarios as `PASS`. Root's `finalize_item` also enforces this.
