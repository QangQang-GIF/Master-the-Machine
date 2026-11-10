"""Regression tests for the RQ3I4OHX round02 findings (practice match, 2026-11-10)."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from runner.mcp_server import (MCPHelperServer, announced_test_ids, company_tools_origin, compact_refresh)
from mtm_competition.adapters.company_tools import CompanyToolsClient
from tests.test_credit_optimizations import Board, finish_worker, git, native_server  # noqa: F401 (fixture)


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat().replace("+00:00", "Z")


# 1. Company tools endpoint ------------------------------------------------------------------------------
def test_company_tools_use_the_game_master_origin_not_the_match_page():
    assert company_tools_origin(None, None, None, "https://masterthemachine.tech/match") == "https://masterthemachine.tech"
    assert company_tools_origin("", "https://gm.example/api/v1", None, "https://x/match") == "https://gm.example"
    client = CompanyToolsClient(company_tools_origin(None, None, None, "https://masterthemachine.tech/match"),
                                "RQ3I4OHX", "t")
    assert client.endpoint == "https://masterthemachine.tech/api/v1/mcp/RQ3I4OHX"


def test_init_company_tools_builds_the_origin_endpoint(tmp_path, monkeypatch):
    server = MCPHelperServer(workspace_root=tmp_path)
    server.match_code, server.bearer_token = "RQ3I4OHX", "token"
    server.match_page_url = "https://masterthemachine.tech/match"
    monkeypatch.setattr(CompanyToolsClient, "discover_tools", lambda self: [])
    monkeypatch.setattr(MCPHelperServer, "_ensure_company_bridge", lambda self: None)
    server._init_company_tools()
    assert server.company_tools_client.endpoint == "https://masterthemachine.tech/api/v1/mcp/RQ3I4OHX"


def test_announced_ids_prefer_template_rows_over_prose():
    details = {"submitTemplate": {"testResults": [{"id": "BUG-09-TC-01"}, {"id": "BUG-09-TC-02"}]},
               "minimumVerificationScenarios": "schedules from TC-01 may stay"}
    assert announced_test_ids(details) == ["BUG-09-TC-01", "BUG-09-TC-02"]
    assert announced_test_ids({"minimumVerificationScenarios": "BUG-09-TC-01 and TC-01"}) == ["BUG-09-TC-01"]


# 2. Unknown submit outcome --------------------------------------------------------------------------------
def test_timed_out_submit_is_confirmed_from_the_board(native_server, monkeypatch):
    """CR-07: the POST timed out, the board showed it received. The receipt comes from the board; nothing is
    resent and the item is SUBMITTED (the Player no longer shows it UNFINISHED)."""
    monkeypatch.setenv("MTM_SUBMIT_FAST_REPLAYS", "0")
    server = native_server
    finish_worker(server)
    server.record_worker_result("TASK-01")
    board, sent = server.adapter, []

    def lost_receipt(**kwargs):
        sent.append(kwargs)
        board.items[0]["submission"] = {"latestSubmissionId": "board-receipt", "receivedAt": _iso(time.time()),
                                        "scoreStatus": "PENDING"}
        raise TimeoutError("The read operation timed out")

    board.submit_work = lost_receipt
    with pytest.raises(TimeoutError):
        server.integrate_and_submit("TASK-01")
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and len(sent) == 1
    assert server.journal.get_entry(server.match_uuid, "TASK-01")["state"] == "ACCEPTED"
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_refresh_alone_resolves_an_in_flight_submission(native_server, monkeypatch):
    monkeypatch.setenv("MTM_SUBMIT_FAST_REPLAYS", "0")
    server = native_server
    finish_worker(server)
    server.record_worker_result("TASK-01")
    server.adapter.submit_work = lambda **kw: (_ for _ in ()).throw(TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        server.integrate_and_submit("TASK-01")
    server.adapter.items[0]["submission"] = {"latestSubmissionId": "r-1", "receivedAt": _iso(time.time())}
    server.refresh_match()
    assert server.journal.get_entry(server.match_uuid, "TASK-01")["receipt_id"] == "r-1"
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_old_board_submission_does_not_resolve_a_new_attempt(native_server, monkeypatch):
    monkeypatch.setenv("MTM_SUBMIT_FAST_REPLAYS", "0")
    server = native_server
    finish_worker(server)
    server.record_worker_result("TASK-01")
    server.adapter.items[0]["submission"] = {"latestSubmissionId": "old", "receivedAt": _iso(time.time() - 3600)}
    server.adapter.submit_work = lambda **kw: (_ for _ in ()).throw(TimeoutError("timed out"))
    with pytest.raises(TimeoutError):
        server.integrate_and_submit("TASK-01")
    assert server.journal.get_entry(server.match_uuid, "TASK-01")["state"] == "IN_FLIGHT"


def test_replay_is_not_blocked_by_the_local_deadline_guard(native_server, monkeypatch):
    """CR-07: the replay at the deadline was refused locally ('not officially open'); the Game Master decides."""
    monkeypatch.setenv("MTM_SUBMIT_FAST_REPLAYS", "0")
    server = native_server
    finish_worker(server)
    server.record_worker_result("TASK-01")
    board, sent = server.adapter, []
    real = Board.submit_work

    def flaky(**kwargs):
        sent.append(kwargs)
        if len(sent) == 1:
            raise TimeoutError("The read operation timed out")
        return real(board, **kwargs)

    board.submit_work = flaky
    with pytest.raises(TimeoutError):
        server.integrate_and_submit("TASK-01")
    board.items[0]["status"] = "CLOSED"            # the item closed locally in the meantime
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and len(sent) == 2
    assert sent[0]["serialized_body_bytes"] == sent[1]["serialized_body_bytes"]


def test_game_master_posts_get_a_longer_read_timeout(monkeypatch):
    from mtm_competition.adapters.official_game_master import OfficialGameMasterAdapter
    a = OfficialGameMasterAdapter(api_base_url="https://masterthemachine.tech", api_key="k")
    assert a._post_timeout("POST", "/api/v1/matches/x/submissions") == 30.0
    assert a._post_timeout("POST", "/api/v1/matches/x/backlogs/start") == 30.0
    assert a._post_timeout("GET", "/api/v1/matches/x") == 15.0
    monkeypatch.setenv("MTM_GM_SUBMIT_TIMEOUT", "45")
    assert a._post_timeout("POST", "/api/v1/matches/x/submissions") == 45.0


# 3. worker_result.json never blocks integration --------------------------------------------------------------
def test_worker_result_json_is_ignored_and_its_conflicts_resolved(native_server):
    server = native_server
    worker, match, _, _ = finish_worker(server)
    exclude = Path(git(server.checkout_root, "rev-parse", "--git-common-dir").strip())
    exclude = exclude if exclude.is_absolute() else server.checkout_root / exclude
    assert "worker_result.json" in (exclude / "info" / "exclude").read_text()
    # A worker forces the file into its commit and the root already tracks a different copy: conflict.
    git(worker, "add", "-f", "project/worker_result.json")
    git(worker, "commit", "-m", "record result")
    (server.match_root / "worker_result.json").write_text('{"task_id": "OTHER"}')
    git(server.checkout_root, "add", "-f", "project/worker_result.json")
    git(server.checkout_root, "commit", "-m", "another worker's result")
    git(server.checkout_root, "push", "origin", "main")
    server.record_worker_result("TASK-01")
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED"
    assert (server.match_root / "README.md").read_text().startswith("After")


# 4. Worker results ------------------------------------------------------------------------------------------
def test_worker_result_slips_are_normalised(native_server):
    server = native_server
    worker, match, _, result = finish_worker(server, write_result=False)
    result.update({"elapsed_seconds": "942",
                   "changed_files": ["project/README.md", "answers\\TASK-01\\submit.json",
                                     str(match / "answers/TASK-01/evidence/check.txt")],
                   "evidence_paths": [str(match / "answers" / "TASK-01" / "evidence" / "check.txt")]})
    result.pop("skill_ids")
    (match / "worker_result.json").write_text(json.dumps(result))
    out = server.record_worker_result("TASK-01")
    assert out["phase"] == "READY_TO_INTEGRATE"
    stored = server.native.task_details("TASK-01")["workerResult"]
    assert stored["changed_files"] == ["README.md", "answers/TASK-01/submit.json", "answers/TASK-01/evidence/check.txt"]
    assert stored["evidence_paths"] == ["answers/TASK-01/evidence/check.txt"] and stored["elapsed_seconds"] == 942


def test_contract_errors_name_the_offending_path(native_server):
    server = native_server
    _, match, _, result = finish_worker(server, write_result=False)
    result["evidence_paths"] = ["answers/TASK-01/evidence/missing.log"]
    (match / "worker_result.json").write_text(json.dumps(result))
    with pytest.raises(Exception, match="missing.log"):
        server.record_worker_result("TASK-01")


# 5. Deadlines ---------------------------------------------------------------------------------------------
def _deadline_server(native_server, seconds_left):
    server = native_server
    server.adapter.items[0]["deadlineEpoch"] = Board.server_epoch + seconds_left
    return server


def test_budget_ends_well_before_the_deadline(native_server):
    server = _deadline_server(native_server, 1000)
    server.start_work("TASK-01")
    claim = server.claim_task("TASK-01", "worker-01", 900)
    budget = claim["taskEnvelope"]["time_budget_seconds"]
    assert budget <= 1000 - 300 + 5 and claim["taskEnvelope"]["finish_by_epoch"] > time.time()


def test_deadline_flush_commits_answers_and_reports_it(native_server, monkeypatch):
    server = native_server
    server.start_work("TASK-01")
    claim = server.claim_task("TASK-01", "worker-01", 120)
    match, worker = Path(claim["matchRoot"]), Path(claim["worktreeRoot"])
    (match / "answers/TASK-01").mkdir(parents=True, exist_ok=True)
    (match / "answers/TASK-01/submit.json").write_text(json.dumps({"status": "PARTIAL"}))
    # 200 s left (<= 240 s flush): the uncommitted answer is committed and the item becomes ready
    server.adapter.items[0]["deadlineEpoch"] = Board.server_epoch + 200
    server.refresh_match()
    plan = server.native.plan_tasks()
    assert [r["key"] for r in plan.get("deadlineFlushed", [])] == ["TASK-01"]
    assert server.native.task_details("TASK-01")["phase"] == "READY_TO_INTEGRATE"
    assert "deadline flush" in git(worker, "log", "-1", "--format=%s")
    # the worker finishes afterwards with a valid result: it replaces the flush placeholder
    finish = {"task_id": "TASK-01", "worker_id": "worker-01", "status": "PARTIAL", "skill_ids": [],
              "changed_files": ["answers/TASK-01/submit.json"], "test_results": [], "evidence_paths": [],
              "elapsed_seconds": 5}
    (match / "worker_result.json").write_text(json.dumps(finish))
    out = server.record_worker_result("TASK-01")
    assert out["phase"] == "READY_TO_INTEGRATE"
    assert "deadline_flush" not in server.native.task_details("TASK-01")["workerResult"]


def test_items_too_close_to_their_deadline_are_not_started(native_server):
    server = _deadline_server(native_server, 200)
    server.refresh_match()
    assert server.native.plan_tasks()["selected"] == []


# 6. Root output and waiting --------------------------------------------------------------------------------
def test_compact_refresh_drops_descriptions():
    val = {"status": "RUNNING", "matchCode": "X", "wave": 1, "newlyDiscovered": [],
           "backlogItems": [{"key": "CR-07", "type": "CR", "status": "OPEN", "workStatus": "IN_PROGRESS",
                             "points": 100, "descriptionMd": "x" * 5000,
                             "submission": {"scoreStatus": "GRADED"}}],
           "scheduling": {"server_epoch": 100.0, "remaining_seconds": 900.4,
                          "items": [{"key": "CR-07", "deadline_epoch": 400.0, "description": "y" * 5000}]}}
    out = compact_refresh(val)
    assert len(json.dumps(out)) < 400
    assert out["items"][0]["deadlineSecondsLeft"] == 300 and out["remainingSeconds"] == 900


def test_wait_returns_when_a_worker_writes_its_result(native_server):
    server = native_server
    finish_worker(server)
    t0 = time.time()
    out = server.wait_for_update(55000)
    assert out["status"] == "EVENT" and out["workerResultsAvailable"] == ["TASK-01"] and time.time() - t0 < 10
    # reported once: the next wait does not spin on the same result
    server.native.plan_tasks()
    assert "TASK-01" not in (server._local_wait_events() or {}).get("workerResultsAvailable", [])


def test_dispatch_tasks_starts_and_claims_in_one_call(native_server, tmp_path):
    from mtm_competition.scheduling.native import NativeTaskCoordinator
    server = native_server
    server.native = NativeTaskCoordinator(tmp_path / "native2", server.checkout_root, server.match_root, "project",
                                          "TEST1234", max_workers=3, normal_workers=2)
    server.adapter.items.append({"key": "TASK-02", "type": "TASK", "status": "OPEN", "points": 10})
    out = server.dispatch_tasks([{"backlogItemKey": "TASK-01", "workerId": "w1"},
                                 {"backlogItemKey": "TASK-02", "workerId": "w2"}])
    assert [d.get("workerId") for d in out["dispatched"]] == ["w1", "w2"]
    assert all("taskEnvelope" in d and "full_envelope_path" in d["taskEnvelope"] for d in out["dispatched"])
    assert server.native.task_details("TASK-02")["phase"] == "RUNNING"


# 7. Preparation -------------------------------------------------------------------------------------------
def test_bundled_yarn_and_missing_programs(tmp_path, monkeypatch):
    from runner import project_runtime
    from runner.project_runtime import ProjectRuntime
    match = tmp_path / "M1"
    (match / ".yarn" / "releases").mkdir(parents=True)
    (match / ".yarn" / "releases" / "yarn-4.17.1.cjs").write_text("console.log('yarn ' + process.argv.slice(2).join(' '))")
    (match / "package.json").write_text("{}")
    rt = ProjectRuntime(match, "M1")
    assert rt._bundled_yarn().name == "yarn-4.17.1.cjs"
    real_which = project_runtime.shutil.which
    monkeypatch.setattr(project_runtime.shutil, "which", lambda n, *a, **k: None if n.startswith("yarn") else real_which(n, *a, **k))
    if real_which("node"):
        res = rt.run(["yarn", "install"], timeout=60)
        assert res.ok and "yarn install" in res.stdout
    missing = rt.run(["definitely-not-a-program-xyz"], timeout=10)
    assert missing.returncode == 127 and "cannot start" in missing.stderr


def test_prepare_runs_the_exams_own_setup_script(tmp_path):
    from runner.project_runtime import ProjectRuntime
    match = tmp_path / "M2"
    (match / "env-require").mkdir(parents=True)
    (match / "env-require" / "prepare-source.sh").write_text("echo official-setup-ran > setup.txt\n")
    (match / "package.json").write_text('{"packageManager": "yarn@4.17.1"}')
    results = ProjectRuntime(match, "M2").prepare()
    assert results and results[0].ok and (match / "setup.txt").read_text().strip() == "official-setup-ran"


def test_recorded_result_reports_backlog_tests(native_server):
    events = []
    server = native_server
    server.telemetry = type("T", (), {"emit_backlog_tests": lambda self, **kw: events.append(kw),
                                      "__getattr__": lambda self, name: (lambda *a, **k: None)})()
    finish_worker(server)
    server.record_worker_result("TASK-01")
    assert events and events[0]["status"] == "PASSED" and events[0]["passed"] == 1


def test_wait_reports_a_free_slot_once(native_server):
    server = native_server
    server.refresh_match()
    events = server._local_wait_events()
    assert events and events.get("dispatchable") == ["TASK-01"]
    assert not (server._local_wait_events() or {}).get("dispatchable")
