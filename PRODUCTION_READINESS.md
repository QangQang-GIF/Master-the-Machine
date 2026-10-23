# MTM 2026 Skill System — Production Readiness & Runbook

**Document Version:** 1.0.0  
**Target Tournament:** MTM 2026 Live Contest  
**Status:** **RELEASE-READY**

---

## 1. Definition of Done Compliance Matrix

| Requirement | Target Criteria | Actual Status | Verification Method |
|---|---|:---:|---|
| **Python Compatibility** | Python 3.12 & 3.13 | **PASS** | CI Matrix on Ubuntu & local Windows 3.12/3.13 verification |
| **P0/P1 Remediations** | 100% of P0/P1 tests passing | **PASS** | 88 core regression tests + 12 component E2E tests pass (0 failures) |
| **Independent QA & MVS** | Independent test re-verification in production | **PASS** | `QAGate` re-runs tests with real subprocesses and validates MVS |
| **Remote Git Verification** | Commit SHA verified on remote before submit | **PASS** | `IntegrationGuard` and `GitGuard.verify_head(verify_pushed=True)` |
| **Submission Idempotency** | Zero duplicate submissions on network timeouts | **PASS** | `SubmissionGuard` uses UUID idempotency keys and reconciles in-flight |
| **Two-Phase Grading** | Confirmed points awarded only upon Grader PASSED | **PASS** | `TaskStore` cleanly separates provisional vs confirmed points |
| **Clean Process Tree** | Preemption/cancel does not leave orphan processes | **PASS** | `WorkerSupervisor` cross-platform process tree termination |
| **Real CLI Execution** | Live CLI smoke tests executed without mock | **PASS** | `agy` (v1.4.6) passed live tests; `codex` properly marked BLOCKED |
| **Contest Simulation** | Full 12-event tournament lifecycle simulation | **PASS** | `tests/e2e/test_e2e_b_tournament_sim.py` passed |
| **Skill Checksums** | Checksums locked and frozen in release gate | **PASS** | `verify_skill_checksums('.')` returns 0 mismatches |
| **Structured E2E Report** | Comprehensive test evidence across all tiers | **PASS** | `E2E_REPORT.md` |
| **Operational Runbook** | Config, operating instructions, rollback procedures | **PASS** | `PRODUCTION_READINESS.md` |

---

## 2. Production Environment Configuration

### Required Environment Variables

Set the following environment variables prior to launching the orchestrator:

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

Run these four commands on the contest machine before the match begins:

```bash
# 1. Install dependencies in editable mode
python -m pip install -e ".[dev]"

# 2. Verify frozen skill package SHA-256 checksums
python -c "from mtm_core.config.loader import verify_skill_checksums; verify_skill_checksums('.')"

# 3. Run full automated test suite
python -m pytest -v

# 4. Run E2E tournament suite
python -m pytest tests/e2e/ -v
```

All commands must exit with returncode `0`.

---

## 3. Operational Runbook

### Starting the Orchestrator

To start the autonomous competition loop:

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

## 4. Rollback and Disaster Recovery Procedures

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

## 5. Security & Verification Guardrails

1. **Path Containment:** `IntegrationGuard` rejects any patch touching files outside the repository root or attempting relative path traversal (`../`).
2. **Symlink Prohibition:** `validate_qa_gate` inspects all changed files and rejects any symlinks or symbolic link targets.
3. **No Unpushed Submissions:** `GitGuard.verify_head(verify_pushed=True)` checks the remote repository with `git ls-remote` before calling `submit()`.
4. **Hard Token Budget Enforcement:** `CompetitionOrchestrator` terminates dispatch if cumulative tokens exceed `token_budget` (default: 1,000,000 tokens) and downshifts model effort to `low` when reaching 85% of budget.
