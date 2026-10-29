---
name: game-preparation
description: Prepare the assigned MTM repository and verify build/login before emitting ENVIRONMENT_READY.
---

# Preparation

Handle `PREPARE_GAME_V2` only in the Player-owned root. Preserve the current descriptor's IDs, paths, origin, repository, and authorization; do not reuse another match's values. No backlog work before `START_GAME`. No nested agent CLI or host reconfiguration.

1. Parse the descriptor and verify Git access. Clone only if absent, with sparse checkout restricted to `repository.folder` (`git clone --filter=blob:none --sparse --branch <branch> ... <checkoutRoot>`, then `git -C <checkoutRoot> sparse-checkout set --cone <folder>`). Never inspect old match folders/history/answers. Preserve existing work during recovery. Source and artifacts belong in `matchRoot = checkoutRoot/repository.folder`; verify origin, branch, folder, full HEAD SHA.
2. From the team workspace run `python3 <teamSdlcPackWorkspace>/runner/project_runtime.py --match-root <matchRoot> --match-id <matchId> prepare`. This bounded Engine derives project configuration, uses existing tools/approved local roles, keeps generated development secrets in memory, isolates databases, runs real setup/doctor/build, and preserves logs. Missing production credentials or denied host actions remain real blockers; a sample placeholder alone is not a reason to stop if the documented local setup succeeds. Do not write `.env`, install/reconfigure host services, alter immutable scripts, or fake READY. Save baseline checks.
3. Call `configure_session(descriptor)` after setup; its pipeline verifies real doctor/build, login and readiness. If MCP is not exposed, start `python3 <teamSdlcPackWorkspace>/runner/control.py` with native `exec_command(tty=true)` in the team workspace. Wait for `BRIDGE_READY` (echo disabled), retain its session ID, and send newline-delimited `{"operation":"configure_session","arguments":{"descriptor":<exact descriptor>}}` using native `write_stdin`. The same memory-only helper accepts all guarded operations with their usual argument names. Do not launch another AI CLI, persist the descriptor, scan host private files, or declare MATCH_LOGIN unavailable merely because MCP discovery failed. Native MCP is preferred when available.
4. Keep a small verified architecture/test map at `teamSdlcPackWorkspace/state/<matchId>/knowledge/PROJECT_MAP.md`, outside the challenge repository. Record baseline failures and useful commands, not speculative architecture.

Append validated UTF-8 NDJSON events to the exact `progressLogFile`; never truncate it or repeat Player's `ORCHESTRATOR_STARTED`. `PREPARATION_STATUS` requires schemaVersion=1, playerSessionId, type, stage, status, summary. Stages: `GIT_AUTH`, `REPOSITORY`, `ENVIRONMENT`, `BUILD`, `MATCH_LOGIN`. Statuses: `IN_PROGRESS`, `COMPLETED`, `BLOCKED`, `FAILED`; only verified stages become COMPLETED.

When all readiness gates actually pass, emit `ENVIRONMENT_READY` with schemaVersion=1, playerSessionId, type, requestId, exact repository object, full revision, build=`PASS`, matchLogin=`PASS`. Player sends Preparation Complete; do not call that endpoint. End preparation and wait for `START_GAME`.
