"""
tests/test_challenger_2_gen6_adversarial.py: Empirical Adversarial Stress Test Suite.

Author: challenger_2_gen6 (Role: Git Gate & Telemetry Adversarial Challenger)
Mission:
1. Test Git tree validation when answers/<ID>/submit.json has altered whitespace/keys vs request payload -> verify strict equality enforcement.
2. Test evidence validation with invalid relative paths (e.g. ../evidence/, /abs/path, missing blobs in commit tree) -> verify fail-closed.
3. Test staging attempts with .env, .env.production, exam-info.json -> verify rollback and original file preservation.
4. Test telemetry writer with high event volume and verify 100% of emitted lines validate against schemas/progress-event.schema.json.
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import jsonschema
import pytest

# Ensure project root and tests directory are in sys.path
ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"
for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.git_harness import (
        offline_git_env,
        run_git,
        verify_git_file_exists_in_tree,
        read_git_file_content_from_tree,
        verify_remote_ref_sha,
    )
except ModuleNotFoundError:
    from fixtures.git_harness import (
        offline_git_env,
        run_git,
        verify_git_file_exists_in_tree,
        read_git_file_content_from_tree,
        verify_remote_ref_sha,
    )

try:
    from tests.fixtures.telemetry_validator import (
        validate_progress_file,
        validate_event_object,
        get_cached_schema,
    )
except ModuleNotFoundError:
    from fixtures.telemetry_validator import (
        validate_progress_file,
        validate_event_object,
        get_cached_schema,
    )

from mtm_core.contracts import WorkerResult, WorkerStatus
from mtm_competition.integration.git_guard import (
    GitGuard,
    PreSubmissionGitGuard,
    SubmissionGateError,
    ValidationResult,
)
from mtm_competition.integration.integration_guard import (
    IntegrationGuard,
    IntegrationResult,
    is_forbidden_path,
)
from runner.telemetry import (
    TelemetryLogger,
    clean_summary,
    validate_progress_event,
)


# ==============================================================================
# 1. Git Tree Validation Parity & Key/Whitespace Mutation Tests
# ==============================================================================

def test_adv_01_submit_json_strict_key_and_value_parity(offline_git_env: Dict[str, Any]):
    """
    Stress-tests commit parity validation when answers/<ID>/submit.json has:
    - Missing keys
    - Extra injected keys
    - Renamed keys
    - Whitespace inside key names (e.g., 'summary ')
    - Whitespace inside values (e.g., 'Fix  verified')
    - Value type mutations (bool vs str, int vs str)
    - Deeply nested key alterations
    - Malformed JSON syntax
    - Verifies fail-closed enforcement across all mutations.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]
    task_id = "TASK-ADV-KEY-01"
    guard = PreSubmissionGitGuard(checkout_dir)

    base_answer = {
        "codeChanges": True,
        "summary": "Fix verified cleanly",
        "scoreEstimate": 100,
        "details": {
            "module": "payment_gateway",
            "criticality": "HIGH",
            "tags": ["security", "auth"],
        },
    }

    # Setup git answers dir
    ans_dir = checkout_dir / "answers" / task_id
    ans_dir.mkdir(parents=True, exist_ok=True)
    submit_file = ans_dir / "submit.json"

    # Helper to commit submit.json and get SHA
    def _commit_and_push_answer(payload_bytes: bytes, msg: str) -> str:
        submit_file.write_bytes(payload_bytes)
        run_git(checkout_dir, ["add", str(submit_file)])
        run_git(checkout_dir, ["commit", "-m", msg])
        run_git(checkout_dir, ["push", "origin", branch])
        return run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    # Base valid commit
    valid_sha = _commit_and_push_answer(
        json.dumps(base_answer, indent=2).encode("utf-8"),
        "commit baseline valid submit.json",
    )
    res_valid = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=valid_sha,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res_valid.valid is True
    assert res_valid.error_message is None

    # Mutation 1: Missing top-level key ("scoreEstimate" removed)
    mutated_missing = dict(base_answer)
    del mutated_missing["scoreEstimate"]
    sha_missing = _commit_and_push_answer(
        json.dumps(mutated_missing, indent=2).encode("utf-8"),
        "mutation: missing key",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_missing,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 2: Injected extra key ("hackedField": True)
    mutated_extra = dict(base_answer)
    mutated_extra["hackedField"] = True
    sha_extra = _commit_and_push_answer(
        json.dumps(mutated_extra, indent=2).encode("utf-8"),
        "mutation: injected extra key",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_extra,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 3: Renamed key ("summary" -> "Summary")
    mutated_renamed = {
        "codeChanges": True,
        "Summary": "Fix verified cleanly",
        "scoreEstimate": 100,
        "details": base_answer["details"],
    }
    sha_renamed = _commit_and_push_answer(
        json.dumps(mutated_renamed, indent=2).encode("utf-8"),
        "mutation: renamed key case",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_renamed,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 4: Trailing whitespace in key name ("summary " vs "summary")
    mutated_key_ws = {
        "codeChanges": True,
        "summary ": "Fix verified cleanly",
        "scoreEstimate": 100,
        "details": base_answer["details"],
    }
    sha_key_ws = _commit_and_push_answer(
        json.dumps(mutated_key_ws, indent=2).encode("utf-8"),
        "mutation: trailing whitespace in key name",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_key_ws,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 5: Whitespace in string value ("Fix  verified cleanly" with double space)
    mutated_val_ws = dict(base_answer)
    mutated_val_ws["summary"] = "Fix  verified cleanly"
    sha_val_ws = _commit_and_push_answer(
        json.dumps(mutated_val_ws, indent=2).encode("utf-8"),
        "mutation: double space in value",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_val_ws,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 6: Type mutation (int 100 -> string "100")
    mutated_type = dict(base_answer)
    mutated_type["scoreEstimate"] = "100"
    sha_type = _commit_and_push_answer(
        json.dumps(mutated_type, indent=2).encode("utf-8"),
        "mutation: type mutation int to string",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_type,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 7: Deeply nested dictionary alteration
    mutated_nested = json.loads(json.dumps(base_answer))
    mutated_nested["details"]["criticality"] = "LOW"
    sha_nested = _commit_and_push_answer(
        json.dumps(mutated_nested, indent=2).encode("utf-8"),
        "mutation: nested field change",
    )
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_nested,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "parity mismatch" in res.error_message.lower()

    # Mutation 8: Malformed JSON syntax (trailing comma)
    bad_json_bytes = b'{\n  "codeChanges": true,\n  "summary": "Fix",\n}\n'
    sha_bad_json = _commit_and_push_answer(bad_json_bytes, "mutation: malformed json")
    res = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_bad_json,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res.valid is False
    assert "invalid json" in res.error_message.lower()

    # Mutation 9: Formatting whitespace check (compact vs indent=4 vs blank lines)
    # Semantic JSON content is identical; verify robust JSON parsing without false rejections
    compact_json = json.dumps(base_answer, separators=(",", ":")).encode("utf-8")
    sha_compact = _commit_and_push_answer(compact_json, "formatting: compact json")
    res_compact = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_compact,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=base_answer,
    )
    assert res_compact.valid is True


# ==============================================================================
# 2. Evidence Validation Invalid Relative Paths & Missing Blobs
# ==============================================================================

def test_adv_02_evidence_validation_traversal_and_missing_blobs(offline_git_env: Dict[str, Any]):
    """
    Stress-tests evidence validation:
    - Path traversal attacks: ../evidence/, ../../secret.txt, URL-encoded %2e%2e/
    - Absolute paths: posix /abs/path, windows C:\\path, UNC \\\\server\\share
    - Cross-task traversal: answers/OTHER_TASK/evidence/
    - Missing blob in commit tree (never committed)
    - Uncommitted file: exists in checkout working tree but not in git commit tree
    - Evidence embedded inside answer_payload['evidence']
    - Directory instead of file blob
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]
    task_id = "TASK-ADV-EV-02"
    guard = PreSubmissionGitGuard(checkout_dir)

    # Prepare and commit answer and valid evidence
    ans_dir = checkout_dir / "answers" / task_id
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)

    answer_payload = {"codeChanges": True, "summary": "Evidence verified"}
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload), encoding="utf-8")
    (ev_dir / "valid_test.log").write_text("All 42 tests passed\n", encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "commit base answer and valid evidence"])
    run_git(checkout_dir, ["push", "origin", branch])
    commit_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    # Valid baseline
    res_valid = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=commit_sha,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=answer_payload,
        evidence_files=["evidence/valid_test.log"],
    )
    assert res_valid.valid is True

    # Attack Vector 1: Parent directory traversal variants
    traversal_paths = [
        "../evidence/test.log",
        "evidence/../../secret.txt",
        "../../../etc/passwd",
        "evidence/subdir/../../../escape.txt",
        "..\\evidence\\test.log",
        "evidence\\..\\..\\secret.txt",
    ]
    for bad_path in traversal_paths:
        res = guard.validate_pre_submission(
            repo_path=checkout_dir,
            target_commit_sha=commit_sha,
            remote_name="origin",
            assigned_branch_ref=f"refs/heads/{branch}",
            backlog_item_key=task_id,
            answer_payload=answer_payload,
            evidence_files=[bad_path],
        )
        assert res.valid is False, f"Expected traversal rejection for {bad_path}"
        assert "traversal" in res.error_message.lower()

    # Attack Vector 2: URL-encoded path traversal
    url_encoded_paths = [
        "%2e%2e/evidence/out.log",
        "evidence/%2e%2e/secret.txt",
        "%2e%2e%2fevidence%2fout.log",
    ]
    for bad_url in url_encoded_paths:
        res = guard.validate_pre_submission(
            repo_path=checkout_dir,
            target_commit_sha=commit_sha,
            remote_name="origin",
            assigned_branch_ref=f"refs/heads/{branch}",
            backlog_item_key=task_id,
            answer_payload=answer_payload,
            evidence_files=[bad_url],
        )
        assert res.valid is False, f"Expected rejection for URL encoded path {bad_url}"
        assert "traversal" in res.error_message.lower()

    # Attack Vector 3: Absolute paths (Posix, Windows, UNC)
    absolute_paths = [
        "/tmp/evidence.log",
        "/var/log/syslog",
        "/answers/TASK-ADV-EV-02/evidence/valid_test.log",
        "C:\\evidence\\log.txt",
        "C:/evidence/log.txt",
        "D:\\data\\evidence.log",
        "\\\\server\\share\\evidence.log",
        "//server/share/evidence.log",
    ]
    for abs_path in absolute_paths:
        res = guard.validate_pre_submission(
            repo_path=checkout_dir,
            target_commit_sha=commit_sha,
            remote_name="origin",
            assigned_branch_ref=f"refs/heads/{branch}",
            backlog_item_key=task_id,
            answer_payload=answer_payload,
            evidence_files=[abs_path],
        )
        assert res.valid is False, f"Expected absolute path rejection for {abs_path}"
        assert "absolute" in res.error_message.lower() or "traversal" in res.error_message.lower()

    # Attack Vector 4: Cross-task directory traversal
    res_cross = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=commit_sha,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=answer_payload,
        evidence_files=["answers/OTHER_TASK/evidence/leak.log"],
    )
    assert res_cross.valid is False
    assert "outside task" in res_cross.error_message.lower()

    # Attack Vector 5: Missing blob in commit tree (never committed)
    res_missing = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=commit_sha,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=answer_payload,
        evidence_files=["evidence/missing_file_never_committed.log"],
    )
    assert res_missing.valid is False
    assert "missing from commit" in res_missing.error_message.lower()

    # Attack Vector 6: File exists on disk in working tree but uncommitted
    (ev_dir / "uncommitted_file.log").write_text("on disk only\n", encoding="utf-8")
    res_uncommitted = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=commit_sha,  # target commit does not contain this file
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=answer_payload,
        evidence_files=["evidence/uncommitted_file.log"],
    )
    assert res_uncommitted.valid is False
    assert "missing from commit" in res_uncommitted.error_message.lower()

    # Attack Vector 7: Evidence declared inside answer_payload['evidence']
    # Even if evidence_files parameter is empty, evidence list in answer_payload is checked!
    tampered_answer_with_bad_ev = {
        "codeChanges": True,
        "summary": "Tampered evidence in answer",
        "evidence": ["evidence/non_existent_blob.log"],
    }
    # Update committed submit.json to match tampered_answer_with_bad_ev
    (ans_dir / "submit.json").write_text(json.dumps(tampered_answer_with_bad_ev), encoding="utf-8")
    run_git(checkout_dir, ["add", str(ans_dir / "submit.json")])
    run_git(checkout_dir, ["commit", "-m", "commit tampered answer with missing evidence"])
    run_git(checkout_dir, ["push", "origin", branch])
    sha_with_bad_ev = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    res_embedded = guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=sha_with_bad_ev,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key=task_id,
        answer_payload=tampered_answer_with_bad_ev,
        evidence_files=[],  # Empty list; should extract from tampered_answer_with_bad_ev
    )
    assert res_embedded.valid is False
    assert "missing from commit" in res_embedded.error_message.lower()

    # Verify raise_on_error mode raises SubmissionGateError
    with pytest.raises(SubmissionGateError):
        guard.validate_pre_submission(
            repo_path=checkout_dir,
            target_commit_sha=commit_sha,
            remote_name="origin",
            assigned_branch_ref=f"refs/heads/{branch}",
            backlog_item_key=task_id,
            answer_payload=answer_payload,
            evidence_files=["../escape.log"],
            raise_on_error=True,
        )


# ==============================================================================
# 3. Forbidden File Staging Attempts, Rollback & Preservation
# ==============================================================================

def test_adv_03_staging_forbidden_files_rollback_and_preservation(
    offline_git_env: Dict[str, Any],
    tmp_path: Path,
):
    """
    Stress-tests staging attempts touching forbidden files:
    - .env, .env.production, .env.local, .env.staging
    - exam-info.json (immutable challenge metadata)
    - project_map.md, index-meta.json (runtime knowledge caches)
    - Mixed batches: legitimate files + forbidden files
    Verifies:
    1. Integration fails closed (success=False).
    2. Target repository working tree is 100% clean and HEAD SHA is unchanged.
    3. Original content and SHA256 of pre-existing forbidden files are preserved bit-for-bit.
    4. Worker workspace undergoes rollback (git checkout HEAD / clean -f).
    5. PreSubmissionGitGuard independently blocks any commit touching forbidden files.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]

    # 1. Establish baseline canonical repository with sensitive & immutable files
    original_env_bytes = b"DATABASE_URL=postgres://root:secret@prod:5432/db\nAPI_KEY=prod_live_abc123\n"
    original_exam_bytes = b'{\n  "examId": "official-mtm-2026",\n  "locked": true,\n  "checksum": "deadbeef"\n}\n'
    original_src_bytes = b"def calculate_tax(amount):\n    return amount * 0.1\n"

    (checkout_dir / ".env").write_bytes(original_env_bytes)
    (checkout_dir / "exam-info.json").write_bytes(original_exam_bytes)
    (checkout_dir / "src").mkdir(parents=True, exist_ok=True)
    (checkout_dir / "src" / "tax.py").write_bytes(original_src_bytes)

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "baseline repository with secrets and exam-info"])
    run_git(checkout_dir, ["push", "origin", branch])

    baseline_head = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()
    env_sha_pre = hashlib.sha256(original_env_bytes).hexdigest()
    exam_sha_pre = hashlib.sha256(original_exam_bytes).hexdigest()

    guard = IntegrationGuard(checkout_dir, push_to_remote=False, verify_post_integration=False)

    # Attack Case 1: Worker attempts to modify existing .env
    ws1 = tmp_path / "worker_env_tamper"
    ws1.mkdir(parents=True, exist_ok=True)
    # Clone baseline into worker workspace so ws has git HEAD
    run_git(tmp_path, ["clone", str(checkout_dir), str(ws1)])
    (ws1 / ".env").write_bytes(b"DATABASE_URL=postgres://attacker:pwned@evil:5432/db\n")

    worker_res_1 = WorkerResult(
        task_id="TASK-TAMPER-ENV",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=[".env"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    res1 = guard.integrate(worker_res_1, ws1)
    assert res1.success is False
    assert "forbidden" in res1.error_message.lower()

    # Target repository must be completely untouched
    assert (checkout_dir / ".env").read_bytes() == original_env_bytes
    assert hashlib.sha256((checkout_dir / ".env").read_bytes()).hexdigest() == env_sha_pre
    assert run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip() == baseline_head
    assert run_git(checkout_dir, ["status", "--porcelain"]).stdout.strip() == ""

    # Worker workspace .env must be rolled back to HEAD (normalize CRLF on Windows)
    assert (ws1 / ".env").read_bytes().replace(b"\r\n", b"\n") == original_env_bytes

    # Attack Case 2: Worker attempts to create new .env.production
    ws2 = tmp_path / "worker_env_prod"
    ws2.mkdir(parents=True, exist_ok=True)
    run_git(tmp_path, ["clone", str(checkout_dir), str(ws2)])
    (ws2 / ".env.production").write_bytes(b"DEPLOY_SECRET=injected_leak\n")

    worker_res_2 = WorkerResult(
        task_id="TASK-TAMPER-PROD",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=[".env.production"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    res2 = guard.integrate(worker_res_2, ws2)
    assert res2.success is False
    assert "forbidden" in res2.error_message.lower()

    # File must NOT exist in canonical repo
    assert not (checkout_dir / ".env.production").exists()
    assert run_git(checkout_dir, ["status", "--porcelain"]).stdout.strip() == ""

    # Attack Case 3: Worker attempts to modify exam-info.json
    ws3 = tmp_path / "worker_exam_tamper"
    ws3.mkdir(parents=True, exist_ok=True)
    run_git(tmp_path, ["clone", str(checkout_dir), str(ws3)])
    (ws3 / "exam-info.json").write_bytes(b'{"examId": "pwned", "locked": false}\n')

    worker_res_3 = WorkerResult(
        task_id="TASK-TAMPER-EXAM",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["exam-info.json"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    res3 = guard.integrate(worker_res_3, ws3)
    assert res3.success is False
    assert "forbidden" in res3.error_message.lower()

    # Preservation check
    assert (checkout_dir / "exam-info.json").read_bytes() == original_exam_bytes
    assert hashlib.sha256((checkout_dir / "exam-info.json").read_bytes()).hexdigest() == exam_sha_pre
    assert run_git(checkout_dir, ["status", "--porcelain"]).stdout.strip() == ""

    # Attack Case 4: Mixed Batch Attack (legitimate src fix + .env.production + exam-info.json)
    ws4 = tmp_path / "worker_mixed_batch"
    ws4.mkdir(parents=True, exist_ok=True)
    run_git(tmp_path, ["clone", str(checkout_dir), str(ws4)])
    (ws4 / "src" / "tax.py").write_bytes(b"def calculate_tax(amount):\n    return amount * 0.15\n")
    (ws4 / ".env.production").write_bytes(b"PRODUCTION_KEY=stolen\n")
    (ws4 / "exam-info.json").write_bytes(b'{"tampered": true}\n')

    worker_res_4 = WorkerResult(
        task_id="TASK-MIXED-ATTACK",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["src/tax.py", ".env.production", "exam-info.json"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    res4 = guard.integrate(worker_res_4, ws4)
    assert res4.success is False
    assert "forbidden" in res4.error_message.lower()

    # Atomic rollback: src/tax.py must NOT be applied to canonical repo
    assert (checkout_dir / "src" / "tax.py").read_bytes() == original_src_bytes
    assert not (checkout_dir / ".env.production").exists()
    assert (checkout_dir / "exam-info.json").read_bytes() == original_exam_bytes
    assert run_git(checkout_dir, ["status", "--porcelain"]).stdout.strip() == ""
    assert run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip() == baseline_head

    # Attack Case 5: PreSubmissionGitGuard blocks commit containing forbidden file
    # Manually craft a commit in checkout_dir that touches .env alongside an answer file
    (checkout_dir / "answers" / "TASK-TEST").mkdir(parents=True, exist_ok=True)
    (checkout_dir / "answers" / "TASK-TEST" / "submit.json").write_text('{"codeChanges": true}', encoding="utf-8")
    (checkout_dir / ".env").write_bytes(b"LEAK=true\n")
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "illicit commit with .env"])
    run_git(checkout_dir, ["push", "origin", branch])
    bad_commit_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    pre_guard = PreSubmissionGitGuard(checkout_dir)
    res_git_block = pre_guard.validate_pre_submission(
        repo_path=checkout_dir,
        target_commit_sha=bad_commit_sha,
        remote_name="origin",
        assigned_branch_ref=f"refs/heads/{branch}",
        backlog_item_key="TASK-TEST",
        answer_payload={"codeChanges": True},
        require_remote=False,
    )
    assert res_git_block.valid is False
    assert "forbidden file" in res_git_block.error_message.lower()


# ==============================================================================
# 4. Telemetry High Volume Concurrency & Schema Validation Stress
# ==============================================================================

def test_adv_04_telemetry_high_volume_concurrency_and_schema_validation(tmp_path: Path):
    """
    Stress-tests TelemetryLogger:
    1. Concurrently emits 1,000 events across 10 threads.
    2. Covers all 14 official event types from schemas/progress-event.schema.json:
       - ORCHESTRATOR_STARTED, ORCHESTRATOR_RESTARTED
       - PREPARATION_STATUS, ENVIRONMENT_READY
       - GAME_START_STATUS, BACKLOG_STATUS, BACKLOG_TESTS
       - GAME_MASTER_API, MODEL_USAGE, TOOL_ACTIVITY, NETWORK_ACTIVITY
       - BLOCKER_OPENED, BLOCKER_RESOLVED, ORCHESTRATOR_ENDED
    3. Validates:
       - 100% of emitted lines are valid JSON.
       - 100% of emitted lines strictly validate against schemas/progress-event.schema.json.
       - draft 2020-12 / additionalProperties: false is strictly enforced.
       - Zero lines have corrupted or interleaved JSON.
       - Exactly 1,000 events written.
       - Consistent, non-divergent playerSessionId across all lines.
    4. Negative testing:
       - Unknown event type fails closed.
       - Extra undeclared property fails closed.
       - Malformed summary with newlines is sanitized to schema-compliant string.
       - Invalid enum values (e.g. invalid status or phase) strictly fail closed.
    """
    log_file = tmp_path / "high_volume.progress.ndjson"
    session_id = "test-session-gen6-stress-uuid"

    logger = TelemetryLogger(
        log_path=log_file,
        player_session_id=session_id,
        match_code="MTM2026X",
    )

    total_threads = 10
    events_per_thread = 100
    expected_total_events = total_threads * events_per_thread  # 1,000 events

    # All 14 official event emitter functions to cycle through with strict schema enums
    def emit_event_variant(worker_idx: int, event_idx: int) -> Dict[str, Any]:
        variant = (worker_idx + event_idx) % 14
        task_key = f"TASK-{worker_idx:02d}-{event_idx:03d}"

        if variant == 0:
            return logger.emit_orchestrator_started(
                summary=f"Worker {worker_idx} orchestrator start iteration {event_idx}"
            )
        elif variant == 1:
            return logger.emit_orchestrator_restarted(
                attempt=(event_idx % 10) + 1,
                phase="GAME" if event_idx % 2 == 0 else "PREPARATION",
                reason=f"SIGTERM_RESTART_{event_idx}",
                summary=f"Worker {worker_idx} restart attempt {event_idx}",
            )
        elif variant == 2:
            stages = ["GIT_AUTH", "REPOSITORY", "ENVIRONMENT", "BUILD", "MATCH_LOGIN", "PREPARE_COMPLETE"]
            return logger.emit_preparation_status(
                stage=stages[event_idx % len(stages)],
                status="COMPLETED" if event_idx % 2 == 0 else "IN_PROGRESS",
                summary=f"Prep stage update {event_idx}",
            )
        elif variant == 3:
            return logger.emit_environment_ready(
                request_id=f"req-{worker_idx}-{event_idx}",
                repo_url="https://git.mtm.local/repo.git",
                branch="main",
                folder="repo",
                revision="b" * 40,
                build="PASS",
                match_login="PASS",
            )
        elif variant == 4:
            # Schema enum for GAME_START_STATUS stage is strictly BACKLOG_SYNC
            return logger.emit_game_start_status(
                stage="BACKLOG_SYNC",
                status="COMPLETED",
                summary=f"Game sync iteration {event_idx}",
            )
        elif variant == 5:
            # Schema enum: DISCOVERED, IN_PROGRESS, COMPLETED, SUBMITTED, BLOCKED, FAILED
            valid_statuses = ["DISCOVERED", "IN_PROGRESS", "COMPLETED", "SUBMITTED", "BLOCKED", "FAILED"]
            return logger.emit_backlog_status(
                backlog_id=task_key,
                title=f"Task title for {task_key}",
                status=valid_statuses[event_idx % len(valid_statuses)],
                summary=f"Backlog status update for {task_key}",
                revision="c" * 40,
                receipt_id=f"receipt-{worker_idx}-{event_idx}",
            )
        elif variant == 6:
            # Schema enum: PASSED, FAILED, BLOCKED
            return logger.emit_backlog_tests(
                backlog_id=task_key,
                status="PASSED" if event_idx % 2 == 0 else "FAILED",
                required=10,
                executed=10,
                passed=8,
                failed=2,
                skipped=0,
                summary=f"Test run stats for {task_key}",
            )
        elif variant == 7:
            # Schema enum for phase: REQUESTED, SUCCEEDED, FAILED
            api_phases = ["REQUESTED", "SUCCEEDED", "FAILED"]
            return logger.emit_game_master_api(
                operation="SUBMISSION",
                phase=api_phases[event_idx % len(api_phases)],
                summary=f"GM submission API call {event_idx}",
                backlog_id=task_key,
                method="POST",
                origin="https://gm.mtm.local",
                path="/api/v1/matches/00000000-0000-0000-0000-000000000000/submissions",
                http_status=201,
                request_id=f"req-gm-{event_idx}",
            )
        elif variant == 8:
            return logger.emit_model_usage(
                model="claude-3-5-sonnet-20241022",
                input_tokens=1500 * (event_idx + 1),
                cached_input_tokens=500 * (event_idx + 1),
                output_tokens=300 * (event_idx + 1),
                reasoning_tokens=0,
                summary=f"Token consumption {event_idx}",
            )
        elif variant == 9:
            # Schema enum for phase: REQUESTED, SUCCEEDED, FAILED
            tool_phases = ["REQUESTED", "SUCCEEDED", "FAILED"]
            return logger.emit_tool_activity(
                tool_name="git_integration",
                operation="commit_and_push",
                phase=tool_phases[event_idx % len(tool_phases)],
                summary=f"Tool activity {event_idx}",
                backlog_id=task_key,
            )
        elif variant == 10:
            # Schema enum for phase: REQUESTED, SUCCEEDED, FAILED
            net_phases = ["REQUESTED", "SUCCEEDED", "FAILED"]
            return logger.emit_network_activity(
                operation="health_ping",
                method="GET",
                origin="https://gm.mtm.local",
                path="/health",
                phase=net_phases[event_idx % len(net_phases)],
                summary=f"Network ping {event_idx}",
                http_status=200,
            )
        elif variant == 11:
            return logger.emit_blocker_opened(
                blocker_id=f"BLK-{worker_idx}-{event_idx}",
                summary=f"Blocker detected on {task_key}",
                backlog_id=task_key,
            )
        elif variant == 12:
            return logger.emit_blocker_resolved(
                blocker_id=f"BLK-{worker_idx}-{event_idx}",
                summary=f"Blocker resolved cleanly for {task_key}",
            )
        else:
            # Schema enum for status: COMPLETED, BLOCKED, FAILED
            ended_statuses = ["COMPLETED", "BLOCKED", "FAILED"]
            return logger.emit_orchestrator_ended(
                status=ended_statuses[event_idx % len(ended_statuses)],
                summary=f"Orchestrator finished worker {worker_idx} run {event_idx}",
            )

    def worker_job(worker_idx: int) -> int:
        count = 0
        for i in range(events_per_thread):
            emit_event_variant(worker_idx, i)
            count += 1
        return count

    # Run multi-threaded stress execution
    with concurrent.futures.ThreadPoolExecutor(max_workers=total_threads) as executor:
        futures = [executor.submit(worker_job, w_idx) for w_idx in range(total_threads)]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]

    assert sum(results) == expected_total_events
    assert logger.events_written_count == expected_total_events

    # Verify NDJSON file on disk
    assert log_file.exists()
    raw_lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(raw_lines) == expected_total_events

    # Validate 100% of lines using the official strict validator fixture
    parsed_events = validate_progress_file(log_file, expected_session_id=session_id)
    assert len(parsed_events) == expected_total_events

    # Verify each event independently against draft 2020-12 schema
    schema = get_cached_schema()
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)

    seen_types = set()
    for idx, ev in enumerate(parsed_events):
        validator.validate(ev)
        assert ev["schemaVersion"] == 1
        assert ev["playerSessionId"] == session_id
        seen_types.add(ev["type"])

    # Verify that all 14 official event types were emitted and validated
    assert len(seen_types) == 14, f"Expected 14 event types, got {len(seen_types)}: {seen_types}"

    # Negative Test 1: Extra undeclared property rejected fail-closed
    with pytest.raises(jsonschema.ValidationError):
        logger.log_event({
            "type": "ORCHESTRATOR_STARTED",
            "summary": "Attempting extra property injection",
            "unauthorized_field": "hacked_value",
        })

    # Negative Test 2: Unknown event type rejected fail-closed
    with pytest.raises(jsonschema.ValidationError):
        logger.log_event({
            "type": "UNKNOWN_INVENTED_EVENT",
            "summary": "Non-schema event type",
        })

    # Negative Test 3: Empty summary rejected fail-closed
    with pytest.raises(jsonschema.ValidationError):
        validate_event_object({
            "schemaVersion": 1,
            "playerSessionId": session_id,
            "type": "ORCHESTRATOR_STARTED",
            "summary": "",  # Min length violation
        })

    # Negative Test 4: summary sanitization eliminates newlines and null bytes
    raw_dirty = "First line\r\nSecond line\x00with null\nThird line"
    cleaned = clean_summary(raw_dirty)
    assert "\r" not in cleaned and "\n" not in cleaned and "\x00" not in cleaned
    ev_cleaned = logger.emit_orchestrator_started(summary=raw_dirty)
    validator.validate(ev_cleaned)

    # Negative Test 5: Invalid enum value for BACKLOG_STATUS rejected fail-closed
    with pytest.raises(jsonschema.ValidationError):
        logger.emit_backlog_status(
            backlog_id="TASK-INVALID-STATUS",
            title="Task with invalid status",
            status="NOT_STARTED",  # Disallowed by schema enum
            summary="Should be rejected",
        )

    # Negative Test 6: Invalid phase value for GAME_MASTER_API rejected fail-closed
    with pytest.raises(jsonschema.ValidationError):
        logger.emit_game_master_api(
            operation="SUBMISSION",
            phase="GAME",  # Disallowed by schema enum (must be REQUESTED, SUCCEEDED, or FAILED)
            summary="Invalid phase enum",
        )

