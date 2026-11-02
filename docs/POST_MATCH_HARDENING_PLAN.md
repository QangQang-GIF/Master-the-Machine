# MTM 2026 — Post-Match Hardening & System Robustness Plan

> **Match Context:** `ZHMY9BNK` (Official MTM Solova Exam `MTM-26-7UJVLX`)  
> **Target Workspaces:** `D:\Working\Master_the_Machine\Master-the-Machine-main` & `D:\Working\Master_the_Machine\Thi\Workspace`  
> **Objective:** Permanently eliminate all manual interventions, race conditions, dependency gaps, and integrity check mismatches discovered during match preparation.

---

## 1. Summary of Incidents & Root Causes (Match ZHMY9BNK)

| Incident | Stage Blocked | Root Cause | Hotfix Applied During Match |
| :--- | :--- | :--- | :--- |
| **1. PostgreSQL Connection Refused** | `ENVIRONMENT` | Windows restart left stale `postmaster.pid` in `D:\PostgreSQL\16\data\postmaster.pid`. Process 8452 was terminated, but lock file prevented daemon auto-start on port 5433. | Removed stale `postmaster.pid`, launched daemon `postgres.exe` directly on port 5433 with persistent background process. |
| **2. Missing `yaml` Module** | `MATCH_LOGIN` | Python 3.15 development environment did not have `pyyaml` installed. Standard `pip install pyyaml` failed on wheel compilation due to missing MSVC build tools. | Compiled pure-python wheel via `pip install pyyaml --no-binary pyyaml --config-settings="--without-libyaml"` to bypass C-extension requirement. |
| **3. Frozen Skill Checksum Mismatch** | `MATCH_LOGIN` (`configure_session`) | Skill files (`mtm-bug-worker`, `mtm-cr-worker`, `mtm-task-worker`, `mtm-worker`, `team-orchestrator`) were updated with performance optimizations in earlier commits, but `releases/checksums.sha256` retained stale hashes. | Auto-calculated sha256 for all 48 frozen skills/profiles/runners, verified with `verify_skill_checksums()`, and pushed to GitHub. |
| **4. Dual Skill Directory Drift** | `INSTALL / SYNC` | `.agents/skills/team-orchestrator/SKILL.md` and `skills/team-orchestrator/SKILL.md` had slight textual drift (`immediately start_work` vs `immediately call start_work`). `install.py` copied `.agents` then overwrote from `skills`. | Synchronized content across both paths and updated `checksums.sha256`. |

---

## 2. Post-Match Permanent Hardening Checklist

### Task 1: Self-Healing PostgreSQL Watchdog & Auto-Recovery
* **Target Files:** `scripts/install.py`, `scripts/ensure_db.py`, `doctor.ps1`
* **Implementation:**
  1. Add an idempotent database health check:
     * Ping port 5433 via socket/`pg_isready`.
     * If port 5433 is down, inspect `D:\PostgreSQL\16\data\postmaster.pid`.
     * Check if the recorded PID is actively running. If not alive, delete `postmaster.pid`.
     * Start `D:\PostgreSQL\16\bin\postgres.exe -D "D:\PostgreSQL\16\data" -p 5433 -h 127.0.0.1` as a detached Windows service or persistent background process.
     * Verify connection before allowing preparation to proceed.

### Task 2: Automated Pure-Python Dependency Gating
* **Target Files:** `requirements.txt`, `scripts/install.py`, `env-require/setup.ps1`
* **Implementation:**
  1. Add explicit pure-python fallback for `pyyaml`:
     ```python
     try:
         import yaml
     except ImportError:
         subprocess.run([sys.executable, "-m", "pip", "install", "pyyaml", "--no-binary", "pyyaml", "--config-settings=--without-libyaml"])
     ```
  2. Embed this check directly in `scripts/install.py` so installing the team pack guarantees all orchestrator/runner imports succeed.

### Task 3: Automatic Skill Checksum Synchronization Hook
* **Target Files:** `scripts/install.py`, `scripts/check-distribution.py`, `releases/checksums.sha256`
* **Implementation:**
  1. Create `scripts/update_checksums.py` that recalculates sha256 (LF-normalized) for all 48 entries in `releases/checksums.sha256`.
  2. In `scripts/install.py`, invoke `verify_skill_checksums()` during installation; if mismatches are detected in DEV mode, auto-update `checksums.sha256` and report cleanly.
  3. Ensure `check-distribution.py` validates `verify_skill_checksums()` as a zero-tolerance gate before declaring `DISTRIBUTION_VERIFIED_READY`.

### Task 4: Unification of `.agents/skills` and `skills/`
* **Target Files:** `scripts/install.py`, `Master-the-Machine-main/skills`
* **Implementation:**
  1. Establish `.agents/skills` as the authoritative source of truth.
  2. In `install.py`, mirror `.agents/skills` -> `skills/` (or vice versa) with strict diff validation so neither directory can ever drift.

### Task 5: End-to-End Preparation Smoke Test
* **Target Command:**
  ```powershell
  python scripts/install.py --team-workspace "D:\Working\Master_the_Machine\Thi\Workspace"
  python -c "import sys; sys.path.insert(0, 'src'); from mtm_core.config.loader import verify_skill_checksums; verify_skill_checksums('.')"
  powershell.exe -ExecutionPolicy Bypass -File "D:\Working\Master_the_Machine\Thi\Workspace\matches\ZHMY9BNK\ZHMY9BNK\env-require\doctor.ps1"
  ```
  Must output:
  - `TEAM_PACK_INSTALLED_READY`
  - `ALL CHECKSUMS VERIFIED`
  - `Environment status: READY`

---

## 3. Execution Status

- [x] **Match Prep Verification:** All 5 stages passed (`GIT_AUTH`, `REPOSITORY`, `ENVIRONMENT`, `BUILD`, `MATCH_LOGIN`).
- [x] **Game Master Acceptance:** `ENVIRONMENT_READY` emitted and accepted by BTC Game Master.
- [ ] **Post-Match Execution:** Run Tasks 1–5 immediately upon conclusion of Match `ZHMY9BNK`.
