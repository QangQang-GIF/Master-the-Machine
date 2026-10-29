---
name: team-orchestrator
description: Root agent skill orchestrating contestant team lifecycle across preparation, backlog sync, task execution, Git verification, and official submissions.
---

# Team Orchestrator Skill

Authoritative operational specification for Master the Machine (MTM) contestant teams.

## 1. Identity & Runtime Governance (RUNTIME-01)
- **Single-Root Codex Session**: The contestant team operates entirely within the provider-native root Codex session.
- **Strictly Prohibited**: Never spawn nested Codex processes (`codex-cli`, `claude`, sub-terminals, or secondary daemon processes).
- **Multi-Role Coordination**: Coordinate Business Analyst (problem analysis), Developer (implementation), Cyber Security (credential/invariant safety), and Quality Engineer (testing/evidence) within this single session.
- **Subagent Delegation**: Spawn workers only via provider-native subagents or inline profiles (`team_repo_scout`, `team_feature_worker`, `team_quality_checker`).
- **Local Autonomy & No External Cloud Tools**: Never search or invoke external cloud tools (e.g. `google_drive`, `google_docs`). The competition is 100% self-contained locally. All fixtures and standards are local.

## 2. MCP Helper Tools Protocol
All communication with the BTC Game Master and local journal operates through the registered MCP Helper (`mtm_minimal`):
- `configure_session(descriptor)`: Initialize trusted session, resolve Match UUID, wire telemetry NDJSON.
- `login_match()`: Authenticate spectator and verify credentials with Game Master.
- `refresh_match()`: Fetch public match projection (`/match/<matchId>`), discover new waves (min 14 & 29).
- `start_work(backlogItemKey)`: Call `POST /api/v1/matches/{UUID}/backlogs/start`, receive MVS and `submitTemplate`.
- `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)`: Validate Git invariants, record journal entry, and submit solution.
- `report_progress(event)`: Append schema-compliant telemetry event to `<matchId>.progress.ndjson`.
- `wait_for_update(timeoutMs)`: Bounded serial polling for wave releases and match status transitions.

## 3. Preparation Lifecycle (`PREPARE_GAME`)
When `descriptor.type == "PREPARE_GAME"`:
1. Verify host authorizations, parameters, and assigned repository branch (`repository.branch`).
2. Run environment doctor / build command to verify the local toolchain.
3. Generate `PROJECT_MAP.md` strictly outside the Git repository:
   - Path: `<TeamWorkspace>/team-sdlc-pack/state/<matchId>/knowledge/PROJECT_MAP.md`
   - Run verification check to establish verified truth hierarchy. Never commit `PROJECT_MAP.md` to the challenge Git repo.
4. Emit `ENVIRONMENT_READY` event with verified revision, `build: "PASS"`, `matchLogin: "PASS"`.

## 4. Backlog Sync & Wave Handling (`START_GAME`)
When `descriptor.type == "START_GAME"`:
1. Call `configure_session(descriptor)` with official tournament credentials (`apiKeySecret`, `gameMasterApiUrl`, `matchId`).
2. Call `refresh_match()` to synchronize with `/match/<matchId>`.
3. Discover open backlog items; emit `BACKLOG_STATUS` `DISCOVERED` for every new item using its business key (`item.key` e.g. `BUG-01`, `CR-01`), never an internal UUID.
4. Monitor waves: Wave 1 (minute 0), Wave 2 (minute 14), Wave 3 (minute 29).
5. Prioritize items based on type, difficulty, and scoring weight.

## 5. Three-Phase Execution Cycle (Investigation → Implementation → Verification)
For each selected backlog item:
1. **Start Work**: Call `start_work(backlogItemKey)`. The server returns `submitTemplate` and `minimumVerificationScenarios`.
2. **Investigation**: Inspect `PROJECT_MAP.md` and repository code. Pinpoint affected files without modifying immutable assets.
3. **Implementation**:
   - Work in an isolated workspace/worktree.
   - Make minimum surgical changes.
   - Strictly avoid touching `exam-info.json`, `.env*`, or internal `.agents/` / `.codex/` directories.
4. **Verification**:
   - Run targeted test commands.
   - Save output logs to `answers/<key>/evidence/<evidence_name>` (relative paths only).
   - Fill `answers/<key>/submit.json` exactly matching the received `submitTemplate`.

## 6. Git Invariant Verification & Submission Gate (GIT-01..05)
Before submitting, ensure:
1. `answers/<key>/submit.json` is staged and committed with exact parity to the submission `answer` payload (GIT-01).
2. All declared evidence files exist in git tree at `answers/<key>/evidence/<name>` with relative paths (GIT-02).
3. The commit has been pushed to the remote repository: `git push origin <assigned_branch>` (GIT-03).
4. Verify remote ref parity via `git ls-remote origin refs/heads/<assigned_branch>`.
5. No immutable files (`exam-info.json`), secret files (`.env`), or cache directories are modified (GIT-04).
6. **Submission Obligation & Baseline Isolation Rule**:
   - The Submission Gate evaluates ONLY the tests and evidence declared for the CURRENT backlog item in `answers/<key>/submit.json` (Zero `NOT_RUN`, Zero `FAIL` for that item's tests).
   - Pre-existing, unrelated test failures in the repository (e.g. baseline failures noted in `PROJECT_MAP.md` like `cascade.test.ts`) MUST NEVER block or withhold submission.
   - If the code change is implemented, the task's verification scenarios pass, and evidence files are committed/pushed, **CALL `submit_work` IMMEDIATELY**.
   - Local Autonomy: All test fixtures, seed loaders (`seed-cr01.ts`, `BUG-02-seed.ts`), browser captures, and data inputs must be executed locally by the worker. Never search Google Drive or report BLOCKED on missing external fixtures.
7. Call `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)`:
   - MCP Helper verifies Git invariants.
   - Saves canonical request body bytes to SQLite WAL `SubmissionJournal` (`IN_FLIGHT`).
   - Dispatches `POST /api/v1/matches/{UUID}/submissions` with `Idempotency-Key`.
   - Records real server submission receipt (`ACCEPTED`, `201`). Dummy receipts are rejected.

## 7. Match State Transitions & Completion
- **PAUSED**: When spectator signals `PAUSED`, halt submission dispatches until resumed.
- **FINISHED**: When match reaches `FINISHED` or TIME OVER, finalize logs and stop all active agent loops immediately.
