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
mtm-skill-system/
├── AGENTS.md                         # Rules of engagement & Dev/Prod boundary
├── README.md
├── config/
│   ├── development.yaml               # Mutable registry & trace tracking
│   ├── production.yaml                # Locked skills & strict submission gates
│   └── model_profiles.yaml            # Model allowlists & effort profiles
├── schemas/
│   ├── task_envelope.json             # TaskEnvelope schema
│   ├── worker_result.json             # WorkerResult schema
│   └── skill_evaluation.json          # Skill benchmark evaluation schema
├── runner/
│   ├── codex_adapter.py               # Worker execution adapter (Codex CLI + agy fallback)
│   ├── trace_parser.py                # JSONL event parser (tokens, commands, diffs)
│   └── qa_gate.py                     # Deterministic schema & evidence validator
└── tests/
    ├── fixtures/                      # Task envelope & fixture repos
    ├── test_contracts.py              # Schema contract unit tests
    ├── test_trace_parser.py           # Trace parser tests
    └── test_smoke_worker.py           # Autonomous end-to-end worker run
```

## 3. Current Phase Status: P0 Complete (Foundation)
- [x] Initialized repository & Git structure
- [x] `AGENTS.md` and configuration files (`development.yaml`, `production.yaml`, `model_profiles.yaml`)
- [x] JSON Schemas for `TaskEnvelope`, `WorkerResult`, and `SkillEvaluation`
- [x] Deterministic `qa_gate.py` with validation and file/evidence existence verification
- [x] Resilient `trace_parser.py` supporting both Codex JSONL stream and Antigravity stream
- [x] Robust `codex_adapter.py` providing headless execution with automated `WorkerResult` extraction
- [x] Full test suite (8 contract/trace tests + 1 autonomous end-to-end smoke test passed)

## 4. Running Tests
```powershell
$env:PYTHONPATH = "."
python -m pytest tests/test_contracts.py tests/test_trace_parser.py -v
python -m pytest tests/test_smoke_worker.py -v -s
```
