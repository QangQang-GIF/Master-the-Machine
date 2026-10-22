# Skill-Driven Codex Orchestrator & Worker System (MTM 2026)

Skill-driven autonomous engineering system designed for the **Master The Machine 2026** competition.

## 1. Architecture Overview
- **Development Mode (DEV):** Supervised by Antigravity 2.0 / Codex Desktop. Trace capture, failure clustering, and A/B skill evolution.
- **Production Mode (PROD):** Fully headless under BTC Game Player. Uses locked, checksum-verified native Agent Skills under `.agents/skills/`. Deterministic Python guards handle git revisions, one-shot submission, deadlines, and state gates.
- **Contract Boundary:**
  - `TaskEnvelope`: Standardized task input specifying repo path, constraints, allowed skills, model/effort, and announced MVS.
  - `WorkerResult`: Standardized execution output containing status, changed files, test results, evidence, and token accounting.

## 2. Directory Layout
```text
Master-the-Machine/
├── AGENTS.md                         # Rules of engagement & Dev/Prod boundary
├── README.md
├── pyproject.toml
├── .agents/skills/                   # 7 specialized native Agent Skills
│   ├── mtm-init/
│   ├── mtm-bug-worker/
│   ├── mtm-cr-worker/
│   ├── mtm-task-worker/
│   ├── mtm-pi-worker/
│   ├── mtm-verification/
│   └── mtm-evidence/
├── config/
│   ├── development.yaml               # Mutable registry & trace tracking
│   ├── production.yaml                # Locked skills & strict submission gates
│   └── model_profiles.yaml            # Model allowlists & effort profiles
├── schemas/
│   ├── task_envelope.json             # TaskEnvelope schema
│   ├── worker_result.json             # WorkerResult schema
│   └── skill_evaluation.json          # Skill benchmark evaluation schema
├── src/
│   ├── mtm_core/                      # Shared runtime, contracts, Git, config
│   │   ├── contracts/
│   │   ├── git/
│   │   ├── runtime/
│   │   └── config/
│   ├── mtm_competition/               # Production Tournament Architecture
│   │   ├── tasks/
│   │   ├── scheduling/
│   │   ├── routing/
│   │   ├── workers/
│   │   ├── verification/
│   │   ├── integration/
│   │   ├── adapters/
│   │   └── orchestrator.py
│   ├── mtm_arena/                     # Benchmark simulation & scoring
│   │   ├── metrics.py
│   │   └── simulator.py
│   └── mtm_training/                  # Offline self-learning & skill evolution
│       ├── failure_analysis.py
│       ├── lesson_extractor.py
│       ├── skill_evolver.py
│       └── promotion.py
├── AI-Arena/                          # Benchmark arena harnesses & test suites
└── tests/                             # 59 automated unit, integration & E2E scenario tests
```

## 3. Architecture Modules
- **`mtm_core`**: Universal contracts (`TaskEnvelope`, `WorkerResult`), unified headless execution (`CodexRuntime` supporting `codex` and `agy`), Git worktree isolation, and typed config loader with SHA256 skill verification.
- **`mtm_competition`**: Strict tournament pipeline with prioritized backlog scheduling, server-clock synchronization, PI preemption, deterministic state machine, prompt routing, worker isolation, QA gating, post-integration verification, Git push verification, and once-only in-flight submission guards.
  - **`OfficialGameMasterAdapter`**: Production HTTP REST adapter with retry backoff, client error safety, server clock sync, and two-phase grading support (`ACCEPTED` → `PASSED`/`FAILED`).
  - **`IntegrationGuard`**: Clean file/diff integration, post-integration test verification, and automated push to remote tracking branches (`git push origin <branch>`).
  - **`SubmissionGuard`**: Zero-duplicate submission gating with atomic in-flight ledger markers and crash/network timeout reconciliation.
- **`mtm_arena`**: Dynamic leaderboard evaluation, composite scoring, and offline scenario simulation.
- **`mtm_training`**: Autonomous failure clustering, lesson extraction, shadow A/B benchmarking, and rollback-safe skill promotion.

## 4. Running Tests
```bash
pytest tests/ -v
```
