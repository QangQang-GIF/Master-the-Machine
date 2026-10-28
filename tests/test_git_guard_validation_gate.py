"""Tests for Submission Validation Gate in GitGuard (Hard Rules and Dynamic Evidence Extraction)."""
import json
import subprocess
from pathlib import Path
import pytest

from mtm_competition.integration.git_guard import (
    PreSubmissionGitGuard,
    SubmissionGateError,
    extract_required_evidence,
)
try:
    from tests.fixtures.git_harness import run_git
except ModuleNotFoundError:
    from fixtures.git_harness import run_git


def test_extract_required_evidence_scenarios():
    text = """
    # BUG-01
    Capture screenshot of Dashboard before fixing as dashboard_before_fixing.png and commit to evidence/ folder.
    
    ## Scenarios
    Required evidence (screenshot): tc-01-desktop-dashboard.png
    Required evidence: tc-02-production-layout.png, currency-report.txt
    - `tc-03-mobile.png`: mobile layout
    """
    extracted = extract_required_evidence(text)
    assert "dashboard_before_fixing.png" in extracted
    assert "tc-01-desktop-dashboard.png" in extracted
    assert "tc-02-production-layout.png" in extracted
    assert "currency-report.txt" in extracted
    assert "tc-03-mobile.png" in extracted


def test_validation_gate_rejects_non_passing_status(tmp_path: Path):
    # Initialize a temporary git repository
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.name", "MTM Test"])
    run_git(repo, ["config", "user.email", "test@mtm.local"])

    ans_dir = repo / "answers" / "BUG-01"
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True)

    ev_file = ev_dir / "tc-01.png"
    ev_file.write_text("fake png")

    # Status is PARTIAL -> Should be rejected
    answer_payload = {
        "status": "PARTIAL",
        "evidenceFiles": ["evidence/tc-01.png"],
        "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/tc-01.png"]}],
    }
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload))
    run_git(repo, ["add", "-A"])
    run_git(repo, ["commit", "-m", "partial answer"])

    guard = PreSubmissionGitGuard(repo)
    with pytest.raises(SubmissionGateError, match="status 'PARTIAL'"):
        guard.validate_pre_submission(
            repo_path=repo,
            backlog_item_key="BUG-01",
            answer_payload=answer_payload,
            require_remote=False,
            raise_on_error=True,
        )


def test_validation_gate_rejects_not_run_or_fail(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.name", "MTM Test"])
    run_git(repo, ["config", "user.email", "test@mtm.local"])

    ans_dir = repo / "answers" / "BUG-02"
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True)

    ev_file = ev_dir / "test.log"
    ev_file.write_text("log")

    # One test is NOT_RUN
    answer_payload = {
        "status": "DONE",
        "evidenceFiles": ["evidence/test.log"],
        "testResults": [
            {"id": "BUG-02-TC-01", "result": "NOT_RUN", "evidence": []},
            {"id": "BUG-02-TC-02", "result": "PASS", "evidence": ["evidence/test.log"]},
        ],
    }
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload))
    run_git(repo, ["add", "-A"])
    run_git(repo, ["commit", "-m", "has not_run test"])

    guard = PreSubmissionGitGuard(repo)
    with pytest.raises(SubmissionGateError, match="Hard Rule.*BUG-02-TC-01.*NOT_RUN"):
        guard.validate_pre_submission(
            repo_path=repo,
            backlog_item_key="BUG-02",
            answer_payload=answer_payload,
            require_remote=False,
            raise_on_error=True,
        )


def test_validation_gate_enforces_scenario_evidence(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, ["init"])
    run_git(repo, ["config", "user.name", "MTM Test"])
    run_git(repo, ["config", "user.email", "test@mtm.local"])

    # Challenge scenario requires before-fix screenshot
    challenge_dir = repo / "challenge"
    challenge_dir.mkdir()
    (challenge_dir / "BUG-01-minimum-verification-scenarios.md").write_text(
        "Capture screenshot of Dashboard before fixing as dashboard_before_fixing.png\nRequired evidence: tc-01.png"
    )

    ans_dir = repo / "answers" / "BUG-01"
    ev_dir = ans_dir / "evidence"
    ev_dir.mkdir(parents=True)

    (ev_dir / "tc-01.png").write_text("png")
    # Missing dashboard_before_fixing.png!
    answer_payload = {
        "status": "DONE",
        "evidenceFiles": ["evidence/tc-01.png"],
        "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/tc-01.png"]}],
    }
    (ans_dir / "submit.json").write_text(json.dumps(answer_payload))
    run_git(repo, ["add", "-A"])
    run_git(repo, ["commit", "-m", "missing before fixing evidence"])

    guard = PreSubmissionGitGuard(repo)
    with pytest.raises(SubmissionGateError, match="Missing required evidence file 'dashboard_before_fixing.png'"):
        guard.validate_pre_submission(
            repo_path=repo,
            backlog_item_key="BUG-01",
            answer_payload=answer_payload,
            match_root=repo,
            require_remote=False,
            raise_on_error=True,
        )
