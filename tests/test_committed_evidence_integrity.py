"""Submission gates use committed artifacts and the announced test inventory."""
import base64
import json
from pathlib import Path

import pytest

from mtm_competition.integration.git_guard import PreSubmissionGitGuard, SubmissionGateError
from mtm_core.git.repository import run_git


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for args in (["init"], ["config", "user.name", "MTM Test"], ["config", "user.email", "test@mtm.local"]):
        assert run_git(root, args).returncode == 0
    (root / "answers" / "BUG-01" / "evidence").mkdir(parents=True)
    return root


def commit_answer(repo, answer, evidence=None):
    for filename, content in (evidence or {}).items():
        (repo / "answers" / "BUG-01" / "evidence" / filename).write_bytes(content)
    (repo / "answers" / "BUG-01" / "submit.json").write_text(json.dumps(answer))
    assert run_git(repo, ["add", "-A"]).returncode == 0
    assert run_git(repo, ["commit", "-m", "answer"]).returncode == 0


def validate(repo, answer, **kwargs):
    return PreSubmissionGitGuard(repo).validate_pre_submission(
        backlog_item_key="BUG-01", answer_payload=answer, match_root=repo,
        require_remote=False, raise_on_error=True, **kwargs,
    )


def test_empty_committed_evidence_cannot_be_replaced_with_nonempty_local_file(repo):
    answer = {"status": "DONE", "evidenceFiles": ["evidence/tests.log"]}
    commit_answer(repo, answer, {"tests.log": b""})
    (repo / "answers/BUG-01/evidence/tests.log").write_bytes(b"only local")
    with pytest.raises(SubmissionGateError, match="nonempty regular file"):
        validate(repo, answer)


def test_directory_is_not_evidence(repo):
    answer = {"status": "DONE", "evidenceFiles": ["evidence/captures"]}
    (repo / "answers/BUG-01/evidence/captures").mkdir()
    commit_answer(repo, answer, {"captures/screen.png": b"nonempty"})
    with pytest.raises(SubmissionGateError, match="nonempty regular file"):
        validate(repo, answer)


def test_committed_symlink_is_not_evidence(repo):
    answer = {"status": "DONE", "evidenceFiles": ["evidence/link.log"]}
    (repo / "answers/BUG-01/evidence/link.log").symlink_to("tests.log")
    commit_answer(repo, answer, {"tests.log": b"real log"})
    with pytest.raises(SubmissionGateError, match="nonempty regular file"):
        validate(repo, answer)


def test_binary_png_blob_is_supported_without_text_decoding(repo):
    png = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Y9ZlK0AAAAASUVORK5CYII=")
    answer = {"status": "DONE", "evidenceFiles": ["evidence/screen.png"]}
    commit_answer(repo, answer, {"screen.png": png})
    assert validate(repo, answer).valid


def test_committed_scenario_wins_over_modified_worktree(repo):
    scenario = repo / "challenge/BUG-01-minimum-verification-scenarios.md"
    scenario.parent.mkdir()
    scenario.write_text("Required evidence: before.png\n")
    answer = {"status": "DONE", "evidenceFiles": ["evidence/tests.log"]}
    commit_answer(repo, answer, {"tests.log": b"PASS"})
    scenario.write_text("No evidence required\n")
    with pytest.raises(SubmissionGateError, match="Missing required evidence file 'before.png'"):
        validate(repo, answer)


def test_untracked_start_work_scenario_is_still_checked(repo):
    answer = {"status": "DONE", "evidenceFiles": ["evidence/tests.log"]}
    commit_answer(repo, answer, {"tests.log": b"PASS"})
    scenario = repo / "challenge/BUG-01-minimum-verification-scenarios.md"
    scenario.parent.mkdir()
    scenario.write_text("Required evidence: before.png\n")
    with pytest.raises(SubmissionGateError, match="Missing required evidence file 'before.png'"):
        validate(repo, answer)


@pytest.mark.parametrize("test_results", [None, [], [{"id": "TC-01", "result": "PASS"}]])
def test_required_test_inventory_prevents_omission(repo, test_results):
    answer = {"status": "DONE"}
    if test_results is not None:
        answer["testResults"] = test_results
    commit_answer(repo, answer)
    with pytest.raises(SubmissionGateError, match="Missing required test"):
        validate(repo, answer, required_test_ids=["TC-01", "TC-02"])


def test_duplicate_test_ids_cannot_fake_coverage(repo):
    answer = {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS"}] * 2}
    commit_answer(repo, answer)
    with pytest.raises(SubmissionGateError, match="duplicate test ID"):
        validate(repo, answer, required_test_ids=["TC-01"])


def test_all_required_tests_are_accepted(repo):
    answer = {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS"}]}
    commit_answer(repo, answer)
    assert validate(repo, answer, required_test_ids=["TC-01"]).valid
    assert validate(repo, answer, required_test_ids=[]).valid


def test_task_without_mvs_does_not_require_an_invented_test_inventory(repo):
    answer = {"status": "DONE", "summary": "Requested documentation updated"}
    commit_answer(repo, answer)
    assert validate(repo, answer, required_test_ids=[]).valid


def test_malformed_test_result_is_not_ignored(repo):
    answer = {"status": "DONE", "testResults": ["PASS"]}
    commit_answer(repo, answer)
    with pytest.raises(SubmissionGateError, match="Malformed test result"):
        validate(repo, answer)
