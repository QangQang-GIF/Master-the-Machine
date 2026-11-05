import json
import subprocess

import pytest

from mtm_competition.integration.git_guard import PreSubmissionGitGuard, SubmissionGateError

KEY = "BUG-04"
MVS = "# BUG-04 minimum verification scenarios\n\nRequired evidence:\n- `tc-02-top-order.png`\n"


def _git(repo, *a):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True, check=True)


def _repo(tmp_path, status):
    repo = tmp_path / "r"
    (repo / "challenge").mkdir(parents=True)
    (repo / "answers" / KEY / "evidence").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main") if False else subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "challenge" / f"{KEY}-minimum-verification-scenarios.md").write_text(MVS)
    (repo / "answers" / KEY / "evidence" / "test.log").write_text("ok")
    answer = {"schemaVersion": 1, "backlogId": KEY, "status": status,
              "evidenceFiles": [f"answers/{KEY}/evidence/test.log"],
              "testResults": [{"id": f"{KEY}-TC-01", "result": "PASS", "evidence": [f"answers/{KEY}/evidence/test.log"]},
                              {"id": f"{KEY}-TC-02", "result": "NOT_RUN", "evidence": []}],
              "engineeringRecord": {"analysis": "x", "changes": [], "tests": [], "risks": [], "aiCollaboration": ""}}
    (repo / "answers" / KEY / "submit.json").write_text(json.dumps(answer))
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "work")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, sha, answer


def _validate(repo, sha, answer, allow_partial):
    return PreSubmissionGitGuard(repo).validate_pre_submission(
        target_commit_sha=sha, backlog_item_key=KEY, answer_payload=answer, match_root=repo, checkout_root=repo,
        require_remote=False, raise_on_error=True, allow_partial=allow_partial,
        required_test_ids=[f"{KEY}-TC-01", f"{KEY}-TC-02"])


def test_honest_partial_with_missing_required_screenshot_is_accepted(tmp_path):
    repo, sha, answer = _repo(tmp_path, "PARTIAL")
    assert _validate(repo, sha, answer, allow_partial=True).valid


def test_done_claim_with_missing_required_screenshot_is_still_rejected(tmp_path):
    repo, sha, answer = _repo(tmp_path, "DONE")
    with pytest.raises(SubmissionGateError):
        _validate(repo, sha, answer, allow_partial=False)
