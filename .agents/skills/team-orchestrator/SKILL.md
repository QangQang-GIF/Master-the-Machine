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
3. Discover open backlog items; emit `BACKLOG_STATUS` `DISCOVERED` for every new item.
4. Monitor waves: Wave 1 (minute 0), Wave 2 (minute 14), Wave 3 (minute 29).
5. **Priority Order**:
   - **TIER 1 (IMMEDIATE PRIORITY)**: Any item with an active deadline (e.g. `BUG-02` 18-minute deadline from reveal, or any `PI` Production Incident).
   - **TIER 2**: Regular Bugs (e.g. `BUG-01`).
   - **TIER 3**: Change Requests (e.g. `CR-01`).
   - **TIER 4**: Tasks & Chores (e.g. `TASK-02`, `TASK-01`).

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
6. Call `submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)`:
   - MCP Helper verifies Git invariants.
   - Saves canonical request body bytes to SQLite WAL `SubmissionJournal` (`IN_FLIGHT`).
   - Dispatches `POST /api/v1/matches/{UUID}/submissions` with `Idempotency-Key`.
   - Records real server submission receipt (`ACCEPTED`, `201`). Dummy receipts are rejected.

## 7. Immediate Pipeline Delivery for Timed Items (Production Incidents)
- For any timed item or Production Incident (`PI`):
  - **NEVER** wait to batch multiple backlog items together in Git.
  - Implement the fix $\rightarrow$ run minimal verification $\rightarrow$ `git add` $\rightarrow$ `git commit` $\rightarrow$ `git push origin <branch>` $\rightarrow$ `submit_work` **IMMEDIATELY**!
  - Target total turnaround time for PI tasks is $\le 3$ minutes.

## 8. Handling Terminal States & 409 Errors
- If `submit_work` returns HTTP 409 `BACKLOG_ITEM_CLOSED`:
  - The deadline has passed; Game Master has closed this item permanently.
  - DO NOT retry submission or enter recovery/browser loops.
  - Append `{"type": "BACKLOG_STATUS", "backlogId": "<key>", "status": "CLOSED", "summary": "Item closed by deadline."}`.
  - **PROCEED IMMEDIATELY to the next OPEN backlog item** (`CR-01`, `TASK-02`, `TASK-01`).

## 9. Token Discipline & Safe Tool Execution
1. **Token Conservation**:
   - Never run unbounded commands that dump megabytes of output into the conversation.
   - Pipe or filter large outputs: `head -n 30`, `tail -n 30`, `grep`.
   - Never print full schemas or entire repository file trees into context.
2. **Never Hang on Browser/Playwright Events**:
   - NEVER call `waitForEvent('download')` without a strict timeout (max 3000ms).
   - For CSV / file export evidence: ALWAYS generate and save the file directly (e.g. write CSV string from export function or fetch endpoint directly to `answers/<key>/evidence/<file>.csv`). Do not wait for browser download events.
   - All async browser actions in `cua_repl` or playwright MUST have explicit short timeouts (max 5000ms).
