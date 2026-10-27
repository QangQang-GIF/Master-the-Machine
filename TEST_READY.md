# TEST_READY: MTM Competition Offline Contract Test Suite

**Author:** `test_writer_contract_gen6`  
**Date:** 2026-10-27  
**Status:** COMPLETED — ALL FIXTURES AND 27 CONTRACT TESTS IMPLEMENTED AND PASSING (100%)

---

## 1. Overview & Test Architecture

The Gen6 Offline Contract Test Suite provides complete, opaque-box, offline contract verification for the MTM Competition Engine, strictly aligned with:
- `docs/digital-cuder-api.md`: Official Digital Cuder Game Master HTTP REST wire contract.
- `docs/team-orchestrator-technical-guide.md`: MTM Player pack standards and single-process ownership.
- `schemas/progress-event.schema.json`: Strict NDJSON progress telemetry schema (`additionalProperties: false`).
- `MTM_MASTER_PLAN.md` & `PROJECT.md`: Requirements R1–R5 and 27-feature contract matrix.

The test suite runs 100% offline without external network calls, Docker, or uninstalled third-party packages (`respx`, `responses`), utilizing Python standard library `http.server.ThreadingHTTPServer` bound to ephemeral ports (`127.0.0.1:0`) and local bare+clone Git environments.

---

## 2. Fixtures Implemented

1. **`tests/fixtures/mock_gm_server.py`**:
   - `MockGameMasterHTTPServer(ThreadingHTTPServer)` bound to `127.0.0.1:0`.
   - Real TCP socket server handling:
     - `POST /api/v1/matches/{MATCH_UUID}/backlogs/start`: Returns 201 Created (`replay: false`) on first start with `submitTemplate`, `minimumVerificationScenarios`, and files; returns 200 OK (`replay: true`) on replay.
     - `POST /api/v1/matches/{MATCH_UUID}/submissions`: Enforces `Idempotency-Key` header, Bearer authentication token, 36-char Match UUID path validation, root-level `gitRevision`, dynamic `answer`, and returns 201 Created with `{submission: {id, status: "ACCEPTED", scoreStatus: "PENDING"}}`.
     - Idempotency & Conflict Taxonomy: Returns HTTP 409 `IDEMPOTENCY_CONFLICT` on payload mismatch for same key; HTTP 409 `ALREADY_SUBMITTED` on new key for already accepted item; HTTP 409 `BACKLOG_NOT_STARTED` if submission attempted before start.
     - Fault Injection: `inject_rate_limit(count, retry_after)`, `inject_network_timeout()`, `inject_validation_error(details)`.

2. **`tests/fixtures/git_harness.py`**:
   - Pytest fixture `offline_git_env(tmp_path)` creating an isolated bare remote repository (`remote.git`) and working clone (`checkout_root`).
   - Configures local test identity (`MTM Robot <bot@mtm.local>`) and initializes `main` branch.
   - Tree inspection helpers:
     - `verify_git_file_exists_in_tree(repo_path, commit_sha, rel_posix_path)` via `git cat-file -e`.
     - `read_git_file_content_from_tree(repo_path, commit_sha, rel_posix_path)` via `git show`.
     - `verify_remote_ref_sha(repo_path, remote_name, branch, expected_sha)` via `git ls-remote`.

3. **`tests/fixtures/telemetry_validator.py`**:
   - `validate_progress_file(file_path, expected_session_id)` validating `<matchCode>.progress.ndjson`.
   - Validates draft 2020-12 schema conformance against `schemas/progress-event.schema.json` with `additionalProperties: false`.
   - Verifies persistent, non-divergent `playerSessionId` across all progress events.

4. **`tests/contract/conftest.py`**:
   - Automatically configures `sys.path` to include repository root and `tests/`.
   - Exposes fixtures globally to all contract tests.

---

## 3. Contract Test Matrix Coverage (27 Test Features)

| Feature ID | Category | Test File | Test Function | Status |
|------------|----------|-----------|---------------|--------|
| **API-01** | Wire API | `tests/contract/test_api_contract.py` | `test_api_01_start_work` | **PASS** |
| **API-02** | Wire API | `tests/contract/test_api_contract.py` | `test_api_02_start_work_replay` | **PASS** |
| **API-03** | Wire API | `tests/contract/test_api_contract.py` | `test_api_03_match_uuid_preflight_validation` | **PASS** |
| **API-04** | Wire API | `tests/contract/test_api_contract.py` | `test_api_04_submit_without_start_work` | **PASS** |
| **API-05** | Wire API | `tests/contract/test_api_contract.py` | `test_api_05_valid_submission_receipt` | **PASS** |
| **API-06** | Wire API | `tests/contract/test_api_contract.py` | `test_api_06_submission_replay_on_timeout` | **PASS** |
| **API-07** | Wire API | `tests/contract/test_api_contract.py` | `test_api_07_idempotency_conflict_and_duplicate_submission` | **PASS** |
| **API-08** | Wire API | `tests/contract/test_api_contract.py` | `test_api_08_error_envelopes_and_429_backoff` | **PASS** |
| **SEC-01** | Security | `tests/contract/test_api_contract.py` | `test_sec_01_prohibited_origin_and_endpoint_blocking` | **PASS** |
| **GIT-01** | Git Gate | `tests/contract/test_git_guard_contract.py` | `test_git_01_submit_json_commit_parity` | **PASS** |
| **GIT-02** | Git Gate | `tests/contract/test_git_guard_contract.py` | `test_git_02_evidence_verification` | **PASS** |
| **GIT-03** | Git Gate | `tests/contract/test_git_guard_contract.py` | `test_git_03_remote_pushed_ref_verification` | **PASS** |
| **GIT-04** | Git Gate | `tests/contract/test_git_guard_contract.py` | `test_git_04_immutable_and_secret_file_staging_block` | **PASS** |
| **GIT-05** | Git Gate | `tests/contract/test_git_guard_contract.py` | `test_git_05_workspace_root_containment` | **PASS** |
| **PREP-01** | Prep | `tests/contract/test_prep_contract.py` | `test_prep_01_environment_doctor_preflight` | **PASS** |
| **PREP-02** | Prep | `tests/contract/test_prep_contract.py` | `test_prep_02_knowledge_bootstrap_and_readiness` | **PASS** |
| **KNO-01** | Knowledge | `tests/contract/test_prep_contract.py` | `test_kno_01_task_knowledge_lookup` | **PASS** |
| **KNO-02** | Knowledge | `tests/contract/test_prep_contract.py` | `test_kno_02_incremental_knowledge_refresh` | **PASS** |
| **KNO-03** | Knowledge | `tests/contract/test_prep_contract.py` | `test_kno_03_missing_index_fallback` | **PASS** |
| **LOG-01** | Telemetry | `tests/contract/test_telemetry_contract.py` | `test_log_01_progress_ndjson_schema_compliance` | **PASS** |
| **LOG-02** | Telemetry | `tests/contract/test_telemetry_contract.py` | `test_log_02_telemetry_event_fields_and_playersessionid` | **PASS** |
| **LIFE-01** | Lifecycle | `tests/contract/test_lifecycle_contract.py` | `test_life_01_tournament_paused_quiescence` | **PASS** |
| **LIFE-02** | Lifecycle | `tests/contract/test_lifecycle_contract.py` | `test_life_02_tournament_resumed_reconcile` | **PASS** |
| **LIFE-03** | Lifecycle | `tests/contract/test_lifecycle_contract.py` | `test_life_03_two_phase_grading_lifecycle` | **PASS** |
| **RUNTIME-01** | Runtime | `tests/contract/test_lifecycle_contract.py` | `test_runtime_01_zero_nested_cli_processes` | **PASS** |

---

## 4. Execution Commands

- **Run all contract tests:**
  ```bash
  py -3.12 -m pytest tests/contract/ -v
  ```
- **Run contract tests with concise summary:**
  ```bash
  py -3.12 -m pytest tests/contract/ -q
  ```
- **Run full test suite (regression + contract):**
  ```bash
  py -3.12 -m pytest tests/ -q -m "not live_cli and not staging_game_master"
  ```
