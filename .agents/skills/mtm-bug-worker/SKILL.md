---
name: mtm-bug-worker
description: Reproduce, minimally fix, and verify an announced MTM BUG item inside the ProjectRuntime sandbox.
---

# BUG (reproduce, minimal fix, MVS evidence)

Follow `mtm-init` and `mtm-worker`. Root owns Start Work, integration, push, and Submit.

1. **Commands**: run every test/check/seed command through `project_runtime.py ... run` and every browser/HTTP capture through `project_runtime.py ... session` (exact syntax in `mtm-worker`). Use the commands the MVS file, the brief or `env-require/README.md` state (their package manager, `node .../vitest.mjs`, check scripts); never assume pnpm/npm/yarn and never start a dev or build server by hand.
2. **Reproduce first**: run the failing MVS scenario (or a focused test) on the unchanged source; keep that output when the MVS or template asks for before-fix evidence.
3. **Minimal fix**: change only the code the brief and MVS point to. Keep public handler names, arguments, database schema and migrations; never edit, skip or weaken existing tests. Add a focused regression test next to the changed module.
4. **MVS run**: copy a provided `challenge/<KEY>-mvs.test.ts` unchanged to the location the MVS states and run it with the MVS command, writing the log under the exact required name (e.g. `answers/<key>/evidence/mvs-tests.log`). Also run the tests next to your change and the type check the brief names.
5. **Official data**: when the item ships a QA fixture, seed or report script (`challenge/<KEY>-seed.*`, `<KEY>-report.*`, `answers/<key>/evidence/<key>-test-data.json`), run it the way its header or the MVS says, on the official fixture only. Remove temporary copies the brief says not to commit.
6. **UI scenarios**: only when a required evidence file is a screenshot or HTTP capture: one `session` pass that follows `env-require/README.md`, at the viewport the MVS states.
7. **Hand-off**: each PASS row cites its required evidence; `status: "DONE"` only when every row is PASS, otherwise `PARTIAL` with honest `NOT_RUN`/`FAIL`. Run `metadata.submit_schema_check`, commit, then write `worker_result.json`.
