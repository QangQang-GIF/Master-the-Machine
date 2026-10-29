"""Exercise the native MCP -> worktree -> Git/evidence -> receipt path without a model or live GM."""
import json
from pathlib import Path
import subprocess
import time

import pytest

from runner.mcp_server import (MCPHelperServer, normalize_native_projection,
                               resolve_stored_session_credentials, save_stored_session_credentials,
                               announced_test_ids)
from mtm_competition.scheduling.native import NativeTaskCoordinator
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


class Board:
    api_base_url = "http://localhost"
    server_epoch = 1900000000.0

    def __init__(self):
        self.server_clock_monotonic = time.monotonic()
        self.calls = []
        self.item = {"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 10}

    def get_match_projection(self, _):
        return {"matchUuid": "11111111-2222-3333-4444-555555555555", "matchCode": "TEST1234",
                "status": "RUNNING", "remainingSec": 1200, "backlogItems": [self.item]}

    def start_working(self, **_):
        return {"submitTemplate": {"status": "DONE", "testResults": [{"id": "TC-01", "result": "NOT_RUN"}],
                                   "evidenceFiles": ["evidence/check.txt"]},
                "files": {"challenge/TASK-01-spec.md": "Document the new behavior."}}

    def submit_work(self, **kwargs):
        self.calls.append(kwargs)
        return {"submission": {"id": "server-receipt-01"}}


@pytest.fixture
def native_server(tmp_path):
    repo = tmp_path / "checkout"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Native test")
    (repo / "project").mkdir()
    (repo / "project" / "README.md").write_text("Before\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "baseline")
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "origin", "main")
    server = MCPHelperServer(workspace_root=tmp_path)
    server.checkout_root, server.match_root = repo, repo / "project"
    server.match_code, server.match_uuid = "TEST1234", "11111111-2222-3333-4444-555555555555"
    server.assigned_branch = "main"
    server.adapter = Board()
    server.git_guard = PreSubmissionGitGuard(repo)
    server.journal = SubmissionJournal(tmp_path / "journal.sqlite3")
    server.native = NativeTaskCoordinator(tmp_path / "native", repo, repo / "project", "project", "TEST1234")
    return server


def test_full_native_path_pushes_answer_and_releases_lock_on_real_receipt(native_server):
    server = native_server
    assert [t["key"] for t in server.plan_tasks()["selected"]] == ["TASK-01"]
    server.start_work("TASK-01")
    assert not (server.match_root / "answers/TASK-01/submit.json").exists()
    claim = server.claim_task("TASK-01", "worker-01", 120)
    worker = Path(claim["worktreeRoot"])
    match = Path(claim["matchRoot"])
    assert (match / "challenge/TASK-01-spec.md").read_text() == "Document the new behavior."
    (match / "README.md").write_text("After: documented behavior.\n")
    evidence = match / "answers/TASK-01/evidence/check.txt"
    evidence.parent.mkdir(parents=True)
    checked = subprocess.run(["git", "-C", str(worker), "diff", "--check"], capture_output=True, text=True)
    assert checked.returncode == 0
    evidence.write_text("git diff --check: exit 0\n")
    answer = {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/check.txt"]}],
              "evidenceFiles": ["evidence/check.txt"]}
    (match / "answers/TASK-01/submit.json").write_text(json.dumps(answer))
    git(worker, "add", "project/README.md", "project/answers")
    git(worker, "commit", "-m", "Document behavior with verification")
    result = {"task_id": "TASK-01", "status": "READY_FOR_REVIEW", "skill_ids": ["mtm-task-worker"],
              "changed_files": ["README.md", "answers/TASK-01/submit.json", "answers/TASK-01/evidence/check.txt"],
              "test_results": [{"command": "git diff --check", "passed": True, "exit_code": checked.returncode}],
              "evidence_paths": ["answers/TASK-01/evidence/check.txt"], "elapsed_seconds": 1}
    assert server.record_worker_result("TASK-01", result)["phase"] == "READY_TO_INTEGRATE"
    server.integration_gate("TASK-01")
    git(server.checkout_root, "cherry-pick", git(worker, "rev-parse", "HEAD"))
    git(server.checkout_root, "push", "origin", "main")
    sha = git(server.checkout_root, "rev-parse", "HEAD")
    receipt = server.submit_work("native-request-01", "TASK-01", sha, answer)
    assert receipt and len(server.adapter.calls) == 1
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"
    assert server.native.plan_tasks()["selected"] == []


def test_latest_board_closes_item_without_duplicate_aliases(native_server):
    s = native_server
    s.adapter.item["id"] = "server-item-uuid"
    assert len(s.refresh_match()["backlogItems"]) == 1
    s.adapter.item = {**s.adapter.item, "status": "CLOSED"}
    assert s.refresh_match()["backlogItems"][0]["status"] == "CLOSED"
    assert s.plan_tasks()["selected"] == []


def test_missing_clock_cannot_launch(native_server):
    s = native_server
    s.adapter.server_epoch = None
    assert s.plan_tasks()["selected"] == []


def test_descriptor_folder_with_trailing_slash_can_claim(native_server, tmp_path):
    s = native_server
    s.native = NativeTaskCoordinator(tmp_path / "native-slash", s.checkout_root, s.match_root, "project/", "TEST1234")
    s.start_work("TASK-01")
    assert Path(s.claim_task("TASK-01", "slash-worker")["matchRoot"]).is_dir()


def test_no_credentials_on_disk_or_cross_match(tmp_path):
    save_stored_session_credentials(tmp_path, "TEST1234", {"password": "fake", "apiKeySecret": "fake"})
    assert resolve_stored_session_credentials(tmp_path, "TEST1234")["password"] == "fake"
    assert resolve_stored_session_credentials(tmp_path, "OTHER123") == {}
    assert resolve_stored_session_credentials(tmp_path / "other", "TEST1234") == {}
    assert not list(tmp_path.rglob("*.session.json"))


def test_projection_uses_server_duration_and_countdown():
    board = Board()
    result = normalize_native_projection({"match": {"status": "RUNNING", "remainingSec": 3600},
                "backlogItems": [{"key": "PI-01", "status": "OPEN", "remainingSec": 80}]}, board)
    assert result["remaining_seconds"] == 3600
    assert result["items"][0]["deadline_epoch"] == pytest.approx(result["server_epoch"] + 80)


def test_inventory_includes_nested_and_markdown_announced_scenarios():
    assert announced_test_ids({"submitTemplate": {"testResults": [{"id": "TC-01"}]},
                              "minimumVerificationScenarios": {"scenarios": [{"id": "TC-02"}]}}) == ["TC-01", "TC-02"]
    assert announced_test_ids({"minimumVerificationScenarios": "| BUG-01-TC-01 | Check input |\n## TC-02"}) == ["BUG-01-TC-01", "TC-02"]


def test_receipt_crash_window_reconciles_without_resubmitting(native_server):
    s = native_server
    # Use the full pipeline, then model a crash after receipt persistence but before lease release.
    test_full_native_path_pushes_answer_and_releases_lock_on_real_receipt(s)
    with s.native._db() as db:
        db.execute("UPDATE tasks SET phase='INTEGRATING',receipt=NULL WHERE key='TASK-01'")
        s.native._set_meta(db, "integration_owner", "TASK-01")
    s._reconcile_native_receipts()
    assert s.native.task_details("TASK-01")["phase"] == "SUBMITTED"
    assert len(s.adapter.calls) == 1
