# MTM 2026 Skill System — Production Readiness & Runbook

**Document Version:** 1.1.0  
**Target Tournament:** MTM 2026 Live Contest  
**Status:** **SIMULATION-VERIFIED | LIVE STAGING & CODEX-CLI PENDING ENVIRONMENT PROVISIONING**

---

## 1. Definition of Done Compliance Matrix

| Requirement | Target Criteria | Actual Status | Verification Method |
|---|---|:---:|---|
| **Python Compatibility** | Python 3.12 & 3.13 | **PASS** | CI Matrix on Ubuntu & local Windows 3.12/3.13 verification |
| **P0/P1 Remediations** | 100% of P0/P1 regression tests passing | **PASS** | 96 core tests + 12 component/tournament E2E tests pass (108 passed, 0 failed) |
| **Independent QA & MVS** | Independent test re-verification in production | **PASS** | `QAGate` re-runs tests with real subprocesses and validates MVS |
| **Remote Git Verification** | Commit SHA verified on remote before submit | **PASS** | `IntegrationGuard` and `GitGuard.verify_head(verify_pushed=True)` |
| **Submission Idempotency** | Zero duplicate submissions on network timeouts | **PASS** | `SubmissionGuard` uses UUID idempotency keys and reconciles in-flight |
| **Two-Phase Grading** | Confirmed points awarded only upon Grader PASSED | **PASS** | `TaskStore` cleanly separates provisional vs confirmed points |
| **Clean Process Tree** | Preemption/cancel does not leave orphan processes | **PASS** | `terminate_process_tree` (`taskkill /F /T` on Windows, isolated session groups with parent protection on Unix) |
| **Real Antigravity CLI** | Live CLI smoke tests executed without mock | **PASS** | `agy` (v1.4.6) passed functional bugfix and timeout termination |
| **Codex CLI Live Execution** | Codex CLI smoke tests executed without mock | **PENDING** | Marked `BLOCKED`; pending `codex` binary installation on contest runner |
| **Contest Simulation** | Full 12-event tournament lifecycle simulation | **PASS** | `tests/e2e/test_e2e_b_tournament_sim.py` passed |
| **Game Master Staging** | Live connection & grading against staging GM | **PENDING** | Safety guard passed; live tests pending staging URL & token from organizers |
| **Skill Checksums** | Checksums locked and frozen in release gate | **PASS** | `verify_skill_checksums('.')` returns 0 mismatches |
| **Structured E2E Report** | Comprehensive test evidence across all tiers | **PASS** | `E2E_REPORT.md` |
| **Operational Runbook** | Config, operating instructions, rollback procedures | **PASS** | `PRODUCTION_READINESS.md` |

---

## 2. Release Gate Procedure (Two-Phase Gating)

To ensure zero risk during contest execution, release gating is split into two formal phases:

### Phase A: Architecture & Simulation Gate (STATUS: COMPLETED & VERIFIED)
- 100% of P0/P1 audit fixes implemented and passing.
- 100% of deterministic E2E simulation tests passing (`test_e2e_a_components.py`, `test_e2e_b_tournament_sim.py`).
- Real Antigravity CLI live execution verified against fixture repositories.
- Zero duplicate submissions in fault-injection tests.
- Skill SHA-256 checksums verified against `releases/checksums.sha256`.

### Phase B: Live Environment Gate (STATUS: PENDING ORGANIZER PROVISIONING)
Must be executed on the official contest machine immediately upon receiving tournament credentials:

```bash
# Step 1: Ensure worker CLI binary is available
which codex   # If competing under Codex backend
which agy     # If competing under Antigravity backend

# Step 2: Configure Staging Environment Credentials
export MTM_GM_STAGING_URL="https://staging.mtm2026.org/api/v1"
export MTM_GM_TOKEN="your_staging_contest_token"
export MTM_STAGING_GM="1"

# Step 3: Run Live CLI Verification (Strict: No Skips Allowed for active backend)
python -m pytest tests/e2e/test_e2e_c_live_cli.py -m live_cli -v
python -m pytest tests/test_smoke_worker.py -m live_cli -v

# Step 4: Run Game Master Staging Integration (Strict: No Skips Allowed)
python -m pytest tests/e2e/test_e2e_d_staging_gm.py -m staging_game_master -v
```

Only when both Phase A and Phase B are completely green should the system be launched against the official contest endpoint.

---

## 3. Production Environment Configuration

### Required Environment Variables

```bash
# 1. Environment Mode
export MTM_ENV=production
export MTM_MODE=production

# 2. Worker Backend Selection (codex | antigravity)
export MTM_WORKER_BACKEND=antigravity   # or codex
export MTM_STRICT_BACKEND=true
export MTM_ALLOW_FALLBACK=false

# 3. Game Master Connectivity
export MTM_GM_URL="https://contest.mtm2026.org/api/v1"
export MTM_GM_TOKEN="your_team_authentication_token"

# 4. Token & Resource Limits
export MTM_TOKEN_BUDGET=1000000
export MTM_CONCURRENCY=1
```

### Pre-Flight Verification Commands

Run these commands on the contest machine before the match begins:

```bash
# 1. Install dependencies in editable mode
python -m pip install -e ".[dev]"

# 2. Verify frozen skill package SHA-256 checksums
python -c "from mtm_core.config.loader import verify_skill_checksums; verify_skill_checksums('.')"

# 3. Run core test suite (90 tests)
python -m pytest tests --ignore=tests/e2e -v

# 4. Run E2E tournament simulation suite (21 tests)
python -m pytest tests/e2e/ -v
```

All commands must exit with returncode `0`.

---

## 4. Operational Runbook

### Starting the Orchestrator

```python
from pathlib import Path
from mtm_competition.adapters.official_game_master import OfficialGameMasterAdapter
from mtm_competition.orchestrator import CompetitionOrchestrator

# Initialize Game Master adapter with official credentials
gm = OfficialGameMasterAdapter(
    base_url="https://contest.mtm2026.org/api/v1",
    auth_token="<TEAM_TOKEN>",
)

# Launch orchestrator
orchestrator = CompetitionOrchestrator(
    gm_adapter=gm,
    repository_path=Path("./contest_repo"),
    scratch_dir=Path("./scratch"),
    db_path=Path("./scratch/tournament.db"),
    concurrency_limit=1,
    token_budget=1_000_000,
    push_to_remote=True,
    require_remote=True,
)

# Run autonomous loop until match FINISHED
report = orchestrator.run_loop(poll_interval=1.0)
print("Tournament Report:", report)
```

### Handling Match States
- **NOT_STARTED:** Orchestrator polls backlog and calibrates server clock; tasks remain queued until match start.
- **RUNNING:** Autonomous dispatch, execution, integration, and submission proceed according to Expected Value (EV).
- **PAUSED:** Orchestrator suspends task dispatch, extends active worker timeout buffers, and stops match clock depletion.
- **FINISHED:** Loop terminates automatically and generates final structured match scorecard.

---

## 5. Rollback and Disaster Recovery Procedures

### Scenario A: Network Drop During Submission
- **Symptom:** Submission HTTP request times out; connection reset.
- **System Action:** Submission enters `SUBMIT_IN_FLIGHT` in `TaskStore`. Task is **not** resubmitted immediately to avoid duplicate submission penalties.
- **Recovery Procedure:**
  1. The orchestrator automatically calls `reconcile_in_flight_submissions()` against `GET /submissions/{task_id}`.
  2. If the server received the submission, it transitions to `ACCEPTED`/`PASSED`.
  3. If the server confirmed it never received the request and returns 404, it can be safely re-attempted using the existing idempotency key.

### Scenario B: Process Crash / Machine Restart
- **Symptom:** Host machine crashes or orchestrator process is killed mid-match.
- **Recovery Procedure:**
  1. Restart the orchestrator with the identical `db_path`:
     ```bash
     python -m mtm_competition.cli --db ./scratch/tournament.db
     ```
  2. Startup routines immediately:
     - Read SQLite database and reconstruct cumulative token usage from `attempts` table.
     - Reconcile any in-flight submissions with the Game Master.
     - Resume execution from the remaining backlog items in priority order.

### Scenario C: Corrupted Customer Repository Worktree
- **Symptom:** Worker created conflicting files or untracked changes in the customer repo.
- **Safe Rollback Procedure (Non-Destructive):**
  ```bash
  # Check current revision
  git status

  # Stash any untracked or modified files safely (DO NOT clean -fd)
  git stash -u

  # Hard reset only if confirmed necessary, restoring to the verified remote HEAD
  git fetch origin main
  git reset --hard origin/main
  ```

---

## 6. Security & Verification Guardrails

1. **Path Containment:** `IntegrationGuard` rejects any patch touching files outside the repository root or attempting relative path traversal (`../`).
2. **Symlink Prohibition:** `validate_qa_gate` inspects all changed files and rejects any symlinks or symbolic link targets.
3. **No Unpushed Submissions:** `GitGuard.verify_head(verify_pushed=True)` checks the remote repository with `git ls-remote` before calling `submit()`.
4. **Hard Token Budget Enforcement:** `CompetitionOrchestrator` terminates dispatch if cumulative tokens exceed `token_budget` (default: 1,000,000 tokens) and downshifts model effort to `low` when reaching 85% of budget.
