---
name: mtm-init
description: Shared MTM worker boundaries, TaskEnvelope/WorkerResult contract, and economical repository inspection.
---

# MTM worker contract

- Work only on the announced item in its `TaskEnvelope`, within assigned paths, model/effort, skills, and resource limits. Use the envelope's `match_root` rather than reconstructing paths. In a worktree, source and answers belong in `worktreeRoot/repository.folder`; Git paths retain that folder prefix.
- Other workers may run concurrently. Use your isolated worktree and local test resources; never overwrite another worker's edits, shared database, or assigned branch. The root owns integration, push, Start Work, and Submit. Do not launch another Codex/agent CLI or delegate unless the envelope authorizes native subagents.
- Use `python3 <metadata.project_runtime_path> --match-root <match_root> --match-id <metadata.match_id> --worker-id <metadata.worker_id> run -- <command...>` for project builds/tests/servers. It supplies ephemeral app configuration, the match database and worker-specific test database/port without writing `.env`. Install dependencies/generate clients in your worktree as needed; never reseed/reset the shared app database. Do not read prior match checkouts, Git history, answers, host transcripts or private credential stores.
- Read only the relevant skill and code slices. Prefer `rg`, bounded output, and full logs saved to files. Implement the smallest complete change; avoid unrelated refactors and dependency updates.
- Read the received template, announced MVS, and evidence requirements before editing. Capture requested before-change evidence first. Run actual verification and record `test_results` with `command`, `passed`, and `exit_code` (zero for PASS); never invent outcomes or omit required scenarios to obtain PASS.
- Missing a duplicate task Markdown file alone does not block work if the official brief, MVS and supplied checks fully specify it. Read all delivered inputs and loader contracts before concluding requirements/data are unavailable. Report a real ambiguity or mandatory absent artifact precisely; never fabricate an official input.
- Use provided local fixtures/loaders and documented schemas. Generate data only where the specification permits; report genuinely missing required inputs accurately. Do not seek hidden tasks, grading tests, gold patches, other teams' data, or external cloud fixtures.
- Do not modify immutable challenge assets, secret files, agent configuration, or Git internals; never log credentials. Production skills are frozen. Host approval blocks require `HOST_APPROVAL_REQUIRED` with the exact reason; preserve progress and stop the blocked action.
- Always write schema-compliant `worker_result.json` at the envelope's result location (default worktree root): task ID, status, changed files, tests, evidence paths, elapsed time, and available usage. Use `READY_FOR_REVIEW` only after complete verified work; otherwise report `BLOCKED`, `FAILED`, `TIMED_OUT`, or `ESCALATE` honestly. Evidence paths are relative to matchRoot, under `answers/<key>/evidence/`. Missing usage stays unknown.
