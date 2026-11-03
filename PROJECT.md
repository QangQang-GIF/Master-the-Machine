# Project: Master_the_Machine Tournament Workspace & Repository Preparation

## Architecture
- **Master-the-Machine-main**: Git repository at `D:\Working\Master_the_Machine\Master-the-Machine-main`, upstream source of truth at `d:\Working\Cuộc thi Agent\repo`. Contains core runtime engine, competition adapters (`company_tools.py`, `official_game_master.py`), verification suites, and scheduling algorithms.
- **Thi Workspace**: Live match competition workspace at `D:\Working\Master_the_Machine\Thi\Workspace`, initialized from `d:\Working\Cuộc thi Agent\runtime\workspace` and installed via `scripts/install.py`. Contains active runtime environment, `.agents` skills, `.codex` subagent configs, and `team-sdlc-pack`.
- **MTM Player Application**: Electron/Desktop player configured via `C:\Users\truon\AppData\Local\MTM Player\player-workspace.json`, binding the player GUI directly to `D:\Working\Master_the_Machine\Thi\Workspace`.
- **Codex Agent Platform**: OpenAI Codex CLI execution engine targeting Windows PowerShell 5.1 (`C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`) with model `gpt-6.1-sol` and reasoning effort `medium`.
- **Company Tools MCP Bridge**: QA fixture transport adapter (`src/mtm_competition/adapters/company_tools.py`) embedded in `team-sdlc-pack` providing official test fixtures and eliminating `BUG-02-OFFICIAL-QA [BLOCKED]`.
- **PostgreSQL 16**: Competition database engine running on port 5433 with data directory `D:\PostgreSQL\16\data`.

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Repo Synchronization | Fast-forward and sync `Master-the-Machine-main` to commit `aeae4b9` matching `repo` | Phase 1 (M1) | ORIGINAL_REQUEST §R1 |
| 2 | Workspace Synchronization | Sync `Thi\Workspace` with `runtime\workspace` including `team-sdlc-pack` and dynamic paths | Phase 1 (M1) | ORIGINAL_REQUEST §R1 |
| 3 | Player Workspace Configuration | Verify `player-workspace.json` points to `D:\Working\Master_the_Machine\Thi\Workspace` | Phase 1 (M1) | ORIGINAL_REQUEST §R1 |
| 4 | Shell Permission & Fix | Neutralize WindowsApps `pwsh.exe` reparse stub; ensure execution via System32 `powershell.exe` | Phase 2 (M2) | ORIGINAL_REQUEST §R2 |
| 5 | PostgreSQL 5433 Service | Verify and ensure PostgreSQL is active and accepting connections on port 5433 | Phase 2 (M2) | ORIGINAL_REQUEST §R2 |
| 6 | Company Tools MCP Bridge | Integrate `company_tools.py` in `team-sdlc-pack` for QA fixture transport (resolve BUG-02) | Phase 2 (M2) | ORIGINAL_REQUEST §R2 |
| 7 | Model Profile Configuration | Update all `.codex/config.toml` files to `model = "gpt-6.1-sol"` and reasoning effort `"medium"` | Phase 3 (M3) | ORIGINAL_REQUEST §R3 |
| 8 | Distribution & Readiness Checks | Execute `check-distribution.py`, doctor checks, verify port 5433 and model settings | Phase 4 (M4) | ORIGINAL_REQUEST §R4 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | Repository & Workspace Sync | Synchronize `Master-the-Machine-main`, `Thi\Workspace`, and verify `player-workspace.json` | none | DONE |
| M2 | Local Env & Shell Permissions | Disable Store `pwsh.exe`, verify System32 PowerShell, check PostgreSQL 5433, verify MCP bridge | M1 | DONE |
| M3 | Model Profile Configuration | Enforce `gpt-6.1-sol` and `medium` across all `.codex/config.toml` in workspace & team-sdlc-pack | M1, M2 | DONE |
| M4 | Readiness & Doctor Verification | Run `check-distribution.py`, environment doctor, PostgreSQL test, and audit readiness | M1, M2, M3 | IN_PROGRESS |

## Code Layout
- Repository: `D:\Working\Master_the_Machine\Master-the-Machine-main\`
  - `src/mtm_competition/adapters/company_tools.py` (QA fixture transport bridge)
  - `runner/mcp_server.py` (MCP runtime server)
  - `scripts/install.py` (Canonical installer for workspace synchronization)
  - `scripts/check-distribution.py` (Distribution validator)
  - `config/model_profiles.yaml` (Model profile definitions)
- Competition Workspace: `D:\Working\Master_the_Machine\Thi\Workspace\`
  - `team-sdlc-pack/` (Pack containing code, adapters, and runner)
  - `.codex/config.toml` (Workspace Codex config)
  - `.agents/skills/` (Subagent skills)
  - `package.json`, `AGENTS.md` (Workspace manifest & instructions)
- Local Environment & Runtime:
  - `C:\Users\truon\AppData\Local\MTM Player\player-workspace.json`
  - `C:\Users\truon\AppData\Local\Microsoft\WindowsApps\pwsh.exe.disabled`
  - `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`
  - `D:\PostgreSQL\16\data` (Port 5433)

## Interface Contracts
## Edge Cases & Operational Caveats
1. **PostgreSQL Daemon Lifecycle & Stale Lockfile Recovery**:
   - PostgreSQL is running as a detached daemon on port 5433 with data directory `D:\PostgreSQL\16\data` (not as a Windows Service).
   - If PostgreSQL ever terminates abruptly or the host restarts, check `D:\PostgreSQL\16\data\postmaster.pid`. If the recorded PID is dead, remove the lockfile before invoking `D:\PostgreSQL\16\bin\pg_ctl.exe start -D "D:\PostgreSQL\16\data"`.
2. **Windows Store `pwsh.exe` Alias Re-creation**:
   - `C:\Users\truon\AppData\Local\Microsoft\WindowsApps\pwsh.exe` has been disabled by renaming to `pwsh.exe.disabled`.
   - Windows Store updates could potentially re-link this execution alias. If `Access is denied (os error 5)` recurs, verify the alias remains neutralized and ensure execution invokes `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`.
3. **Codex Windows Sandbox & Reasoning Configuration**:
   - Target workspace `.codex/config.toml` explicitly configures `default_subagent_model = "gpt-6.1-sol"` and `default_subagent_reasoning_effort = "medium"`.
   - CWD and PYTHONPATH are configured to `D:/Working/Master_the_Machine/Thi/Workspace/team-sdlc-pack`, avoiding path pollution from `Cuộc thi Agent`.
4. **Company Tools MCP Bridge & QA Fixtures**:
   - `src/mtm_competition/adapters/company_tools.py` is present in both `Master-the-Machine-main` and `Thi\Workspace\team-sdlc-pack`.
   - Subagents executing tasks (such as `BUG-02-OFFICIAL-QA`) now have access to official QA test fixtures via the MCP adapter, unblocking test fixture transport.

## Conclusion of Phase 1 and Phase 2
- **Phase 1 (Repository & Workspace Synchronization)**: **CONCLUDED & VERIFIED**
  - Git repository fast-forwarded to commit `aeae4b9`.
  - Workspace cleanly synced with updated `team-sdlc-pack`, `AGENTS.md`, and `package.json`.
  - `player-workspace.json` verified pointing to `D:\Working\Master_the_Machine\Thi\Workspace`.
- **Phase 2 (Local Environment, Shell Permissions & Service Integrity)**: **CONCLUDED & VERIFIED**
  - App Execution Alias `pwsh.exe` disabled; Codex executes System32 Windows PowerShell.
  - PostgreSQL active and accepting connections on port 5433.
  - Model profile set to `gpt-6.1-sol` (`medium`).
  - Distribution verification (`check-distribution.py`) confirmed `ALL_VERIFIED` and `READY`.

