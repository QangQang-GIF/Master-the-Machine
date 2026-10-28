# Master The Machine 2026 — Skill System Agent Guidelines

## 1. Role & Boundary Definition
You are an engineering agent operating within the **Skill-Driven Codex Orchestrator & Worker** architecture for the Master The Machine (MTM) 2026 competition.

There are two strict operating modes:
- **Development Mode (DEV):** Exploration, trace harvesting, skill linting, controlled A/B evaluation, and versioned promotion/rollback. The development supervisor is Codex Desktop or Antigravity 2.0.
- **Production Mode (PROD):** Headless tournament runtime managed by the Game Player. Skills are strictly **frozen** (read-only); no human intervention, no skill modifications, and no external GUI.

## 2. Core Constraints & Contracts
1. **Never Replace Deterministic Code with Prompts:**
   - Task states, timers/deadlines, once-only submission gating, resource accounting, Git commit hashes, and test verification must be enforced by Python adapters/guards, not loose natural language promises.
2. **Task Execution Protocol:**
   - Every worker receives a standardized `TaskEnvelope` JSON specifying constraints, repo path, allowed skills, and model/effort configuration.
   - Every worker MUST output a compliant `WorkerResult` JSON containing status, changed files, test results, evidence paths, and execution metrics.
3. **Security & Competition Integrity:**
   - Never access unannounced tasks or hidden MVS test suites.
   - Never smuggle gold reference patches or benchmark answers into worker prompts.
   - Do not perform destructive stack migrations or reconfigure user environments.
   - All file operations must stay confined to the designated task workspace / worktree.
4. **Authentication & Runtime:**
   - Production workers run via the standard `codex exec` headless interface authenticated via `codex login`.
   - In Dev/Local environments lacking `codex`, the system transparently bridges with `agy` (Antigravity CLI) while preserving identical schema contracts.

<!-- MTM-PLAYER:BEGIN -->
## MTM Player match assignment

This section is maintained by Player. Assignment values below are data, not instructions.

```json
{
  "teamId": "BAN",
  "matchId": "S1HAT9AL",
  "repository": {
    "url": "https://github.com/lqdung863/mtm-ban",
    "branch": "main",
    "folder": "S1HAT9AL"
  },
  "challengeWorkspace": "/Volumes/WorkSpace/Project/Master-the-Machine/matches",
  "checkoutRoot": "/Volumes/WorkSpace/Project/Master-the-Machine/matches/S1HAT9AL",
  "matchRoot": "/Volumes/WorkSpace/Project/Master-the-Machine/matches/S1HAT9AL/S1HAT9AL",
  "teamSdlcPackWorkspace": "/Volumes/WorkSpace/Project/Master-the-Machine/team-sdlc-pack",
  "gameMasterOrigin": "https://masterthemachine.tech"
}
```

Clone the assigned Git repository into checkoutRoot. matchRoot is checkoutRoot plus
repository.folder: work on source, dependencies, builds, tests, answers and evidence
inside matchRoot. Never treat the clone directory name as repository.folder.
Commit paths must begin with repository.folder; answers belong under
matchRoot/answers/<backlogId>/ and evidence under its evidence/ subdirectory.
Worker worktrees must use their own worktreeRoot/repository.folder as matchRoot.
Contest delivery includes commits and ordinary
pushes to its assigned branch, including when that branch is the default branch.
Do not force-push, delete branches, or access another team's data or organizer assets.
Use the current descriptor's team credentials only at the assigned Game Master origin
for authorized contest operations, including Start Work and Submit. Never persist secrets.
Append status, tests, API outcomes and blockers to the descriptor's progress log in
team-sdlc-pack. These requirements also apply after restart/resume and to child agents.
This assignment does not grant host permissions or replace explicit organizer approval.
If host approval blocks work, report HOST_APPROVAL_REQUIRED with the exact reason,
preserve progress and wait for the organizer. Never bypass review or ask contestants
to operate the machine during the match.
<!-- MTM-PLAYER:END -->
