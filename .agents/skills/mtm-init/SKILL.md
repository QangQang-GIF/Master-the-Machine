---
name: mtm-init
description: Core MTM worker boundaries, TaskEnvelope/WorkerResult contract, and repository hygiene.
---

# MTM Worker Contract

- **Work Scope**: Operate strictly within assigned worktree (`worktreeRoot/repository.folder`), model/effort, and time budget. The root agent owns Start Work, integration, push, and Submit.
- **Project Runtime**: Execute build, test, and server commands exclusively via:
  `python3 <metadata.project_runtime_path> --match-root <match_root> --match-id <metadata.match_id> --worker-id <metadata.worker_id> run -- <command...>`
  This supplies ephemeral config, test database, and worker-isolated ports without writing `.env`. Never reseed shared database.
- **Repository Inspection**: Use `rg` and bounded output for surgical file reading. Never scan entire repositories or read prior checkouts, host transcripts, or credential stores.
- **Evidence & Verification**: Capture requested pre-change evidence before editing code. Run actual tests; never fabricate test results. Evidence artifacts belong in `answers/<key>/evidence/`.
- **Safety Boundaries**: Never modify `.env*`, `exam-info.json`, secret files, agent configurations, or Git internals. All file changes must stay confined to your assigned worktree.
- **Output Contract**: Write valid `worker_result.json` at worktree root with `task_id`, `status` (`READY_FOR_REVIEW`, `BLOCKED`, `FAILED`, `TIMED_OUT`), `changed_files`, `test_results`, `evidence_paths`, and `elapsed_seconds`.
