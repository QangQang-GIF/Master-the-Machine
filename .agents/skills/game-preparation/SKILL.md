---
name: game-preparation
description: Prepare the assigned MTM repository and verify build/login before emitting ENVIRONMENT_READY.
---

# Preparation

Handle `PREPARE_GAME_V2` only in the Player-owned root. Preserve the current descriptor's IDs, paths, origin, repository, and authorization; do not reuse another match's values. No backlog work before `START_GAME`. No nested agent CLI or host reconfiguration.

1. Parse the descriptor and verify Git access to the assigned repository. Clone to `checkoutRoot` only if absent; otherwise inspect/fetch without destroying existing changes. Source, dependencies, builds, tests, and answers belong in `matchRoot = checkoutRoot/repository.folder`. Verify origin, assigned branch, folder, and full HEAD SHA.
2. Inspect project manifests and documented setup. Verify the required toolchain, install permitted dependencies, run doctor/build, and record baseline tests. Derive service/configuration requirements from this project; never hardcode another project's credentials. Keep local secrets uncommitted. Report real failures or host approval blocks honestly.
3. Call `configure_session(descriptor)` after setup; its preparation pipeline verifies doctor/build, login and readiness. Use `login_match()` as needed to verify current credentials at the descriptor's approved Game Master origin. Never print or persist secrets yourself.
4. Keep a small verified architecture/test map at `teamSdlcPackWorkspace/state/<matchId>/knowledge/PROJECT_MAP.md`, outside the challenge repository. Record baseline failures and useful commands, not speculative architecture.

Append validated UTF-8 NDJSON events to the exact `progressLogFile`; never truncate it or repeat Player's `ORCHESTRATOR_STARTED`. `PREPARATION_STATUS` requires schemaVersion=1, playerSessionId, type, stage, status, summary. Stages: `GIT_AUTH`, `REPOSITORY`, `ENVIRONMENT`, `BUILD`, `MATCH_LOGIN`. Statuses: `IN_PROGRESS`, `COMPLETED`, `BLOCKED`, `FAILED`; only verified stages become COMPLETED.

When all readiness gates actually pass, emit `ENVIRONMENT_READY` with schemaVersion=1, playerSessionId, type, requestId, exact repository object, full revision, build=`PASS`, matchLogin=`PASS`. Player sends Preparation Complete; do not call that endpoint. End preparation and wait for `START_GAME`.
