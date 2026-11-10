"""K2EMUW3I credit fixes: fewer Root turns and a smaller Root context.

- record_worker_result reads worker_result.json itself and fixes lossless JSON type slips
- integrate_and_submit = lock + cherry-pick + submit in one call (conflicts are returned, not raised)
- submit_work fills answer / revision / clientSubmissionId from disk and journal
- worker sessions reach company tools through the Root helper's file bridge (no credential leaves the Root)
- compact plan_tasks, KB article ids with letters, optional PI worker cap
"""
import json
import subprocess
import threading
import time
from pathlib import Path

import pytest

from runner.mcp_server import MCPHelperServer, compact_plan, kb_article_ids
from mtm_competition.scheduling.native import NativeTaskCoordinator, normalize_test_row, passing_row_problem
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, text=True).stdout.strip()


class Board:
    api_base_url = "http://localhost"
    server_epoch = 1900000000.0

    def __init__(self, items=None):
        self.server_clock_monotonic = time.monotonic()
        self.calls = []
        self.items = items or [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 10}]

    def get_match_projection(self, _):
        return {"matchUuid": "11111111-2222-3333-4444-555555555555", "matchCode": "TEST1234",
                "status": "RUNNING", "remainingSec": 1200, "backlogItems": self.items}

    def start_working(self, **_):
        return {"submitTemplate": {"status": "DONE", "testResults": [{"id": "TC-01", "result": "NOT_RUN"}],
                                   "evidenceFiles": ["evidence/check.txt"]},
                "files": {"challenge/TASK-01-spec.md": "Document the new behavior."}}

    def submit_work(self, **kwargs):
        self.calls.append(kwargs)
        return {"submission": {"id": f"server-receipt-{len(self.calls):02d}"}}


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


def finish_worker(server, readme="After: documented behavior.\n", write_result=True, exit_code=0):
    server.start_work("TASK-01")
    claim = server.claim_task("TASK-01", "worker-01", 120)
    worker, match = Path(claim["worktreeRoot"]), Path(claim["matchRoot"])
    (match / "README.md").write_text(readme)
    evidence = match / "answers/TASK-01/evidence/check.txt"
    evidence.parent.mkdir(parents=True)
    evidence.write_text("git diff --check: exit 0\n")
    answer = {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS", "evidence": ["evidence/check.txt"]}],
              "evidenceFiles": ["evidence/check.txt"]}
    (match / "answers/TASK-01/submit.json").write_text(json.dumps(answer))
    git(worker, "add", "project/README.md", "project/answers")
    git(worker, "commit", "-m", "Document behavior with verification")
    result = {"task_id": "TASK-01", "worker_id": "worker-01", "status": "READY_FOR_REVIEW",
              "skill_ids": ["mtm-task-worker"],
              "changed_files": ["README.md", "answers/TASK-01/submit.json", "answers/TASK-01/evidence/check.txt"],
              # string types as a model sometimes writes them: fixed losslessly by the coordinator
              "test_results": [{"command": "git diff --check", "passed": "true", "exit_code": str(exit_code)}],
              "evidence_paths": ["answers/TASK-01/evidence/check.txt"], "elapsed_seconds": 1}
    if write_result:
        (match / "worker_result.json").write_text(json.dumps(result))
    return worker, match, answer, result


def test_record_reads_worker_result_from_disk_and_returns_a_short_verdict(native_server):
    server = native_server
    finish_worker(server)
    out = server.record_worker_result("TASK-01")             # no workerResult argument
    assert out["phase"] == "READY_TO_INTEGRATE"
    assert set(out["workerResult"]) <= {"status", "answer_status", "verification_status", "mvs_passed"}
    stored = server.native.task_details("TASK-01")["workerResult"]["test_results"][0]
    assert stored["passed"] is True and stored["exit_code"] == 0


def test_record_without_result_file_is_a_clear_error(native_server):
    finish_worker(native_server, write_result=False)
    with pytest.raises(FileNotFoundError):
        native_server.record_worker_result("TASK-01")


def test_integrate_and_submit_is_one_call_and_replays_nothing_twice(native_server):
    server = native_server
    worker, _, answer, _ = finish_worker(server)
    server.record_worker_result("TASK-01")
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and out["receiptId"] == "server-receipt-01"
    assert len(server.adapter.calls) == 1
    # the worker commit is on the remote branch and the root answer equals the worker answer
    assert json.loads((server.match_root / "answers/TASK-01/submit.json").read_text()) == answer
    remote_head = git(server.checkout_root, "ls-remote", "origin", "refs/heads/main").split()[0]
    assert remote_head == git(server.checkout_root, "rev-parse", "HEAD") == out["gitRevision"]
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_integrate_conflict_is_returned_and_releases_the_lock(native_server):
    server = native_server
    finish_worker(server, readme="Worker version\n")
    server.record_worker_result("TASK-01")
    # the root changed the same line after the worker started
    (server.match_root / "README.md").write_text("Root version\n")
    git(server.checkout_root, "commit", "-am", "root edit")
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "CONFLICT" and "project/README.md" in out["conflicts"]
    assert not server.adapter.calls
    assert git(server.checkout_root, "status", "--porcelain", "--untracked-files=no") == ""   # cherry-pick aborted
    with server.native._db() as db:
        assert server.native._meta(db, "integration_owner") is None


def test_submit_work_fills_answer_revision_and_id_from_disk(native_server):
    server = native_server
    worker, _, answer, _ = finish_worker(server)
    server.record_worker_result("TASK-01")
    server.integration_gate("TASK-01")
    git(server.checkout_root, "cherry-pick", git(worker, "rev-parse", "HEAD"))
    server.submit_work(backlog_item_key="TASK-01")
    assert len(server.adapter.calls) == 1
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_company_tool_bridge_serves_worker_sessions(tmp_path, monkeypatch):
    bridge = tmp_path / "bridge"
    monkeypatch.setenv("MTM_COMPANY_BRIDGE_DIR", str(bridge))

    class FakeClient:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "boom":
                raise RuntimeError("Tool 'boom' error: nope")
            return {"articles": [{"id": arguments.get("query")}]}

        def discover_tools(self):
            return [{"name": "kb_search", "category": "kb"}]

    root = MCPHelperServer(workspace_root=tmp_path / "ws")
    root.match_code, root.bearer_token = "M1", "secret-token-value"
    root.company_tools_client = FakeClient()
    root._ensure_company_bridge()
    try:
        deadline = time.time() + 5
        while not (bridge / "root.json").exists() and time.time() < deadline:
            time.sleep(0.1)
        # the worker helper runs in another process; simulate it with a pid that differs from the root's
        beat = json.loads((bridge / "root.json").read_text())
        beat["pid"] = -1
        (bridge / "root.json").write_text(json.dumps(beat))
        worker = MCPHelperServer(workspace_root=tmp_path / "elsewhere")      # no credentials at all
        out = worker.call_company_tool("kb_search", {"query": "KB-SUP-9901"})
        assert out == {"articles": [{"id": "KB-SUP-9901"}]}
        assert root.company_tools_client.calls == [("kb_search", {"query": "KB-SUP-9901"})]
        assert worker.list_company_tools() == [{"name": "kb_search", "category": "kb"}]
        with pytest.raises(RuntimeError, match="nope"):
            worker.call_company_tool("boom", {})
        # the token never appears in the bridge folder
        for f in bridge.iterdir():
            assert "secret-token-value" not in f.read_text(encoding="utf-8", errors="replace")
    finally:
        root.running = False


def test_worker_without_bridge_keeps_the_explicit_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_COMPANY_BRIDGE_DIR", str(tmp_path / "none"))
    worker = MCPHelperServer(workspace_root=tmp_path)
    with pytest.raises(RuntimeError, match="not initialized"):
        worker.call_company_tool("kb_search", {"query": "x"})
    assert worker.list_company_tools() == []
    assert worker.fetch_fixture("BUG-01")["status"] == "UNAVAILABLE"


def test_kb_article_ids_with_letters():
    text = "Follow KB-SUP-9901 and KB-12; see kb-bank-rule-06 and KB-SUP-9901 again."
    assert kb_article_ids(text) == ["KB-SUP-9901", "KB-12", "KB-BANK-RULE-06"]


def test_compact_plan_has_no_paths_duplicates_or_empty_lists():
    val = {"matchStatus": "IN_PROGRESS", "availableSlots": 2, "selected": [], "skippedReady": [],
           "active": [{"key": "PI-02", "phase": "RUNNING", "workerId": "pi02", "worktreeRoot": "D:/very/long/path"}],
           "readyToIntegrate": [{"key": "BUG-09", "workerId": "bug09r2", "worktreeRoot": "D:/x", "slackSeconds": 506.98,
                                 "decision": {"action": "SUBMIT", "reason": "PARTIAL", "time_left": 506.98}}],
           "readyDiagnostics": [{"key": "BUG-09", "slackSeconds": 506.9}, {"key": "BUG-11", "reason": "Waiting"}],
           "submitBudgetSeconds": 90.0}
    out = compact_plan(val)
    assert out["active"] == ["PI-02:pi02"]
    assert out["readyToIntegrate"] == [{"key": "BUG-09", "workerId": "bug09r2", "slackSeconds": 507,
                                        "decision": {"action": "SUBMIT", "reason": "PARTIAL", "time_left": 507}}]
    assert out["readyDiagnostics"] == [{"key": "BUG-11", "reason": "Waiting"}]
    assert "selected" not in out and "skippedReady" not in out
    assert "worktreeRoot" not in json.dumps(out)


def test_long_command_output_is_summarised_with_full_log(tmp_path, monkeypatch, capsys):
    from runner import project_runtime as pr
    big = "\n".join(f"test case {i} ... ok" for i in range(5000)) + \
        "\nFAIL test_transfer: expected 100 received 99\nTests: 1 failed, 4999 passed\n"
    runtime = type("R", (), {"match_id": "m1", "worker_id": "w1"})()
    log = tmp_path / "evidence" / "test.log"
    pr.emit_output(big, "", 1, 2.5, "vitest run", runtime, log_path=str(log))
    out = capsys.readouterr().out
    assert log.read_text(encoding="utf-8") == big                      # full log kept (usable as evidence)
    assert len(out) < 2500 and "exit=1" in out and str(log) in out
    assert "5001: FAIL test_transfer: expected 100 received 99" in out  # failure with its log line number
    assert "Tests: 1 failed, 4999 passed" in out                       # tail keeps the verdict


def test_short_command_output_is_printed_unchanged(tmp_path, capsys):
    from runner import project_runtime as pr
    runtime = type("R", (), {"match_id": "m1", "worker_id": "w1"})()
    pr.emit_output("3 passed\n", "", 0, 0.1, "vitest", runtime)
    assert capsys.readouterr().out == "3 passed\n"


def test_unknown_submit_outcome_is_replayed_in_the_same_call(native_server, monkeypatch):
    """A Submit timeout no longer holds the lock for the unknown-outcome TTL: the same journaled request
    (same bytes, same idempotency key) is resent a few seconds later inside integrate_and_submit."""
    monkeypatch.setenv("MTM_SUBMIT_REPLAY_DELAY", "0")
    server = native_server
    finish_worker(server)
    server.record_worker_result("TASK-01")
    board = server.adapter
    real_submit, sent = board.submit_work, []

    def flaky(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            raise TimeoutError("The read operation timed out")
        return real_submit(**kwargs)

    board.submit_work = flaky
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and len(sent) == 2
    assert sent[0]["serialized_body_bytes"] == sent[1]["serialized_body_bytes"]
    assert sent[0]["idempotency_key"] == sent[1]["idempotency_key"]
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_hung_command_is_stopped_by_the_idle_watchdog(tmp_path, monkeypatch):
    from runner.project_runtime import ProjectRuntime
    import sys as _sys
    monkeypatch.setenv("MTM_RUN_IDLE_TIMEOUT", "1.5")
    rt = ProjectRuntime(tmp_path, "M1", "w1")
    t0 = time.monotonic()
    res = rt.run([_sys.executable, "-c", "import time; print('start', flush=True); time.sleep(30)"], timeout=60)
    assert time.monotonic() - t0 < 15
    assert res.timed_out and res.returncode == 124 and "start" in res.stdout and "no output" in res.stderr
    ok = rt.run([_sys.executable, "-c", "import time\nfor i in range(4):\n print(i, flush=True); time.sleep(0.5)"], timeout=60)
    assert ok.returncode == 0 and not ok.timed_out and ok.stdout.split() == ["0", "1", "2", "3"]


def test_watchdog_caps_worker_overrides(tmp_path, monkeypatch):
    from runner.project_runtime import ProjectRuntime
    import sys as _sys
    monkeypatch.delenv("MTM_RUN_IDLE_TIMEOUT", raising=False)
    monkeypatch.setenv("MTM_RUN_IDLE_MAX", "2")        # a worker passing --idle-timeout 1600 is still capped
    monkeypatch.setenv("MTM_RUN_IDLE_TIMEOUT", "1600")
    rt = ProjectRuntime(tmp_path, "M1", "w1")
    t0 = time.monotonic()
    res = rt.run([_sys.executable, "-c", "import time; time.sleep(30)"], timeout=60)
    assert res.timed_out and time.monotonic() - t0 < 15


def test_test_row_normalization_is_lossless():
    assert normalize_test_row({"command": "x", "passed": "true", "exit_code": "0"}) == \
        {"command": "x", "passed": True, "exit_code": 0}
    assert "exit_code" not in normalize_test_row({"command": "x", "passed": True})       # nothing invented
    assert passing_row_problem({"command": "x", "passed": True, "exit_code": 2}) == "exit_code must be the integer 0"
    assert passing_row_problem({"command": "", "passed": True, "exit_code": 0}) == "missing command"


def test_pi_burst_slots_add_workers_only_for_pi(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_PI_BURST_SLOTS", "2")
    repo = tmp_path / "checkout"
    (repo / "project").mkdir(parents=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "t@example.invalid")
    git(repo, "config", "user.name", "t")
    (repo / "project" / "a.txt").write_text("a")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "base")
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "project", "project", "MATCH",
                              max_workers=5, normal_workers=4)
    items = [{"key": f"TASK-{i:02d}", "type": "TASK", "status": "OPEN", "points": 10} for i in range(1, 8)]
    items += [{"key": "PI-02", "type": "PI", "status": "OPEN", "points": 90},
              {"key": "PI-03", "type": "PI", "status": "OPEN", "points": 80}]
    c.register_projection({"status": "RUNNING", "server_epoch": time.time(), "remaining_seconds": 2400, "items": items})
    sel = c.plan_tasks()["selected"]
    assert {t["key"] for t in sel if t["type"] == "PI"} == {"PI-02", "PI-03"}
    assert sum(t["type"] != "PI" for t in sel) == 4          # ordinary capacity unchanged (normal_workers=4)
    assert len(sel) == 6
