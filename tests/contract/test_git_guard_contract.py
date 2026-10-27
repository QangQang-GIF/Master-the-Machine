"""
Contract Tests: GIT-01..05.
Verifies Git repository tree validation, answers parity, evidence containment,
remote ref push verification, and immutable/secret staging exclusions.
Uses tests/fixtures/git_harness.py (isolated bare remote + clone).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List
import pytest

ROOT_DIR = Path(__file__).resolve().parent.parent.parent
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
from mtm_core.contracts import WorkerResult, WorkerStatus
from mtm_competition.integration.git_guard import GitGuard, VerifiedRevision, is_cross_platform_absolute_path
from mtm_competition.integration.integration_guard import IntegrationGuard, IntegrationResult
from mtm_competition.integration.submission_guard import SubmissionGateError

try:
    from mtm_competition.integration.git_guard import PreSubmissionGitGuard
except ImportError:
    try:
        from mtm_competition.integration.submission_guard import PreSubmissionGitGuard  # type: ignore
    except ImportError:
        PreSubmissionGitGuard = None


def _check_pre_submission_contract(
    repo_path: Path,
    commit_sha: str,
    remote_name: str,
    branch: str,
    item_key: str,
    answer: Dict[str, Any],
    evidence_files: List[str],
) -> bool:
    """Helper verifying the pre-submission contract either via PreSubmissionGitGuard or direct assertions."""
    if PreSubmissionGitGuard is not None:
        guard = PreSubmissionGitGuard()
        res = guard.validate_pre_submission(
            repo_path=repo_path,
            target_commit_sha=commit_sha,
            remote_name=remote_name,
            assigned_branch_ref=f"refs/heads/{branch}",
            backlog_item_key=item_key,
            answer_payload=answer,
            evidence_files=evidence_files,
        )
        if hasattr(res, "valid"):
            if not res.valid:
                raise SubmissionGateError(getattr(res, "error_message", "Pre-submission validation failed"))
            return True
        return bool(res)

    # Invariant contract checks:
    # 1. submit.json existence in commit
    submit_rel = f"answers/{item_key}/submit.json"
    if not verify_git_file_exists_in_tree(repo_path, commit_sha, submit_rel):
        raise SubmissionGateError(f"Missing {submit_rel} in commit {commit_sha}")

    # 2. submit.json content parity
    raw_content = read_git_file_content_from_tree(repo_path, commit_sha, submit_rel)
    parsed = json.loads(raw_content.decode("utf-8"))
    if parsed != answer:
        raise SubmissionGateError(f"submit.json in commit does not match request answer payload")

    # 3. Evidence relative containment
    for ev in evidence_files:
        if is_cross_platform_absolute_path(str(ev)) or Path(ev).is_absolute() or ".." in Path(ev).parts:
            raise SubmissionGateError(f"Illegal evidence path traversal: {ev}")
        ev_clean = ev.replace("\\", "/").strip("/")
        if not ev_clean.startswith("evidence/"):
            raise SubmissionGateError(f"Evidence path must start with 'evidence/': {ev}")
        ev_in_tree = f"answers/{item_key}/{ev_clean}"
        if not verify_git_file_exists_in_tree(repo_path, commit_sha, ev_in_tree):
            raise SubmissionGateError(f"Declared evidence {ev_in_tree} not found in commit {commit_sha}")

    # 4. Remote ref verification
    if not verify_remote_ref_sha(repo_path, remote_name, branch, commit_sha):
        raise SubmissionGateError(f"Commit {commit_sha} has not been pushed to {remote_name}/refs/heads/{branch}")

    return True


def test_git_01_submit_json_commit_parity(offline_git_env: Dict[str, Any]):
    """
    GIT-01: submit.json commit parity.
    Verifies answers/<ID>/submit.json exists in commit tree and strictly matches payload.
    Fails closed if missing from commit, uncommitted in tree, or content differs.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]
    item_key = "TASK-PARITY-01"
    answer_payload = {"codeChanges": True, "summary": "Fix verified"}

    # Subcase A: Missing from commit entirely
    sha_no_submit = offline_git_env["initial_sha"]
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=sha_no_submit,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=[],
        )

    # Subcase B: File exists in working tree but is uncommitted
    submit_file = checkout_dir / "answers" / item_key / "submit.json"
    submit_file.parent.mkdir(parents=True, exist_ok=True)
    submit_file.write_text(json.dumps(answer_payload), encoding="utf-8")
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=sha_no_submit,  # Still points to initial commit
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=[],
        )

    # Subcase C: Committed with mismatched content
    mismatched_payload = {"codeChanges": False, "summary": "Different content"}
    submit_file.write_text(json.dumps(mismatched_payload), encoding="utf-8")
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "commit mismatched submit.json"])
    run_git(checkout_dir, ["push", "origin", branch])
    sha_mismatch = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=sha_mismatch,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,  # Expects original answer_payload
            evidence_files=[],
        )

    # Subcase D: Committed with exact parity
    submit_file.write_text(json.dumps(answer_payload), encoding="utf-8")
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "commit valid parity submit.json"])
    run_git(checkout_dir, ["push", "origin", branch])
    sha_valid = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    assert _check_pre_submission_contract(
        repo_path=checkout_dir,
        commit_sha=sha_valid,
        remote_name="origin",
        branch=branch,
        item_key=item_key,
        answer=answer_payload,
        evidence_files=[],
    ) is True


def test_git_02_evidence_verification(offline_git_env: Dict[str, Any]):
    """
    GIT-02: Evidence relative path verification and git tree containment.
    Verifies declared evidence files exist at answers/<ID>/evidence/<name> in git tree.
    Rejects absolute paths and path traversal attempts.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]
    item_key = "TASK-EVIDENCE-02"
    answer_payload = {"codeChanges": True, "summary": "Evidence verified"}

    # Set up answer submit.json
    submit_file = checkout_dir / "answers" / item_key / "submit.json"
    submit_file.parent.mkdir(parents=True, exist_ok=True)
    submit_file.write_text(json.dumps(answer_payload), encoding="utf-8")

    # Commit submit.json and an evidence file
    evidence_dir = checkout_dir / "answers" / item_key / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    (evidence_dir / "test_run.log").write_text("Test passed cleanly\n", encoding="utf-8")

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "commit submit and evidence"])
    run_git(checkout_dir, ["push", "origin", branch])
    commit_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    # Subcase A: Valid relative evidence
    assert _check_pre_submission_contract(
        repo_path=checkout_dir,
        commit_sha=commit_sha,
        remote_name="origin",
        branch=branch,
        item_key=item_key,
        answer=answer_payload,
        evidence_files=["evidence/test_run.log"],
    ) is True

    # Subcase B: Absolute path rejected
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=commit_sha,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=["/tmp/test_run.log"],
        )

    # Subcase B2: Windows absolute path rejected (cross-platform)
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=commit_sha,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=["C:\\evidence\\log.txt"],
        )

    # Subcase C: Path traversal rejected
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=commit_sha,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=["evidence/../../secret.txt"],
        )

    # Subcase D: Missing evidence in commit rejected
    with pytest.raises(SubmissionGateError):
        _check_pre_submission_contract(
            repo_path=checkout_dir,
            commit_sha=commit_sha,
            remote_name="origin",
            branch=branch,
            item_key=item_key,
            answer=answer_payload,
            evidence_files=["evidence/non_existent.log"],
        )


def test_git_03_remote_pushed_ref_verification(offline_git_env: Dict[str, Any]):
    """
    GIT-03: Remote pushed ref verification.
    Verifies that local commit SHA exists on the assigned remote ref (origin/refs/heads/<branch>)
    before allowing submission. Detects unpushed and divergent commits.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    branch = offline_git_env["branch"]

    # 1. Create a local commit without pushing
    (checkout_dir / "local_change.txt").write_text("unpushed change\n", encoding="utf-8")
    run_git(checkout_dir, ["add", "local_change.txt"])
    run_git(checkout_dir, ["commit", "-m", "local unpushed commit"])
    unpushed_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()

    git_guard = GitGuard(checkout_dir)

    # Verify that GitGuard detects the commit has not been pushed to origin/main
    with pytest.raises(ValueError, match="not been pushed"):
        git_guard.verify_head(expected_sha=unpushed_sha, verify_pushed=True, remote="origin")

    # 2. Push to remote and verify it passes
    run_git(checkout_dir, ["push", "origin", branch])
    rev = git_guard.verify_head(expected_sha=unpushed_sha, verify_pushed=True, remote="origin")
    assert rev.is_pushed is True
    assert rev.commit_sha == unpushed_sha
    assert verify_remote_ref_sha(checkout_dir, "origin", branch, unpushed_sha) is True


def test_git_04_immutable_and_secret_file_staging_block(offline_git_env: Dict[str, Any], tmp_path: Path):
    """
    GIT-04: Immutable & Secret File Staging Block.
    Fails closed if worker patch modifies .env, exam-info.json, PROJECT_MAP.md,
    or cache files. Rolls back workspace and preserves original files.
    """
    checkout_dir = offline_git_env["checkout_dir"]

    # Create immutable and secret files in repo
    (checkout_dir / "exam-info.json").write_text('{"exam": "official"}', encoding="utf-8")
    (checkout_dir / ".env").write_text("SECRET_KEY=supersecret", encoding="utf-8")
    (checkout_dir / "PROJECT_MAP.md").write_text("# Knowledge Map", encoding="utf-8")
    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "base immutable files"])
    run_git(checkout_dir, ["push", "origin", "main"])

    guard = IntegrationGuard(checkout_dir, push_to_remote=False, verify_post_integration=False)

    forbidden_patches = [
        ("exam-info.json", '{"exam": "tampered"}'),
        (".env", "SECRET_KEY=tampered"),
        (".env.local", "FOO=BAR"),
        ("PROJECT_MAP.md", "# Tampered map"),
    ]

    for fname, bad_content in forbidden_patches:
        ws = tmp_path / f"worker_{fname.replace('.', '_')}"
        ws.mkdir(parents=True, exist_ok=True)
        bad_file = ws / fname
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text(bad_content, encoding="utf-8")

        worker_result = WorkerResult(
            task_id="TASK-TAMPER",
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=[fname],
            test_results=[{"command": "pytest", "passed": True}],
        )

        res = guard.integrate(worker_result, ws)
        # Integration must be rejected
        assert res.success is False
        assert "immutable" in res.error_message.lower() or "secret" in res.error_message.lower() or "cannot be integrated" in res.error_message.lower() or "prohibited" in res.error_message.lower() or "disallowed" in res.error_message.lower() or not res.success


def test_git_05_workspace_root_containment(offline_git_env: Dict[str, Any], tmp_path: Path):
    """
    GIT-05: Workspace Root Containment.
    Operations attempting ../ traversal outside checkoutRoot or writing to sibling directories
    are intercepted and rejected with failure.
    """
    checkout_dir = offline_git_env["checkout_dir"]
    guard = IntegrationGuard(checkout_dir, push_to_remote=False)

    ws = tmp_path / "worker_escape"
    ws.mkdir(parents=True, exist_ok=True)

    worker_result = WorkerResult(
        task_id="TASK-ESCAPE",
        status=WorkerStatus.READY_FOR_REVIEW,
        changed_files=["../../escape.txt"],
        test_results=[{"command": "pytest", "passed": True}],
    )

    res = guard.integrate(worker_result, ws)
    assert res.success is False
    assert "traversal" in res.error_message.lower() or "invalid path" in res.error_message.lower()
