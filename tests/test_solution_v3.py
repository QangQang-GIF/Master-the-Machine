"""Regression suite for Solution v3 (MTM_Solution_v3.md, section 5).

Each test names the scenario number of section 5 it covers.
"""
import json
import subprocess
import sys
import time
import unicodedata
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mtm_competition.integration.git_guard import (  # noqa: E402
    PreSubmissionGitGuard, SubmissionGateError, answer_field_differences,
)
from mtm_competition.scheduling import policy  # noqa: E402
from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator  # noqa: E402
from mtm_competition.tasks.journal import SubmissionJournal  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


def make_repo(tmp_path, with_origin=False):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for args in (["config", "user.email", "t@t"], ["config", "user.name", "T"], ["add", "."],
                 ["commit", "-q", "-m", "init"], ["branch", "-M", "main"]):
        git(repo, *args)
    if with_origin:
        origin = tmp_path / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
        git(repo, "remote", "add", "origin", str(origin))
        git(repo, "push", "-q", "-u", "origin", "main")
    return repo


def coordinator(tmp_path, items, remaining=2000, epoch=1000, repo=None, **kw):
    repo = repo or make_repo(tmp_path)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH",
                              max_workers=4, normal_workers=3, **kw)
    c.register_projection({"status": "RUNNING", "server_epoch": epoch, "remaining_seconds": remaining, "items": items})
    return c


def result(key, worker, **over):
    base = {"task_id": key, "worker_id": worker, "status": "READY_FOR_REVIEW", "skill_ids": ["s"],
            "changed_files": ["main.py"], "test_results": [{"command": "pytest tests/x.py", "passed": True, "exit_code": 0}],
            "evidence_paths": [], "elapsed_seconds": 5}
    base.update(over)
    return base


def write_answer(claim, key, answer):
    path = Path(claim["matchRoot"]) / "answers" / key / "submit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer), encoding="utf-8")
    return path


def started_claim(c, key, worker, details=None):
    c.register_started(key, details or {})
    return c.claim_task(key, worker)


# ---- 1. deadline flush never touches a running worker ----------------------------------------------
def test_1_deadline_flush_only_requests_and_never_changes_phase(tmp_path):
    c = coordinator(tmp_path, [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100, "deadline_epoch": 1500}])
    claim = started_claim(c, "BUG-02", "w1")
    (Path(claim["matchRoot"]) / "main.py").write_text("print(2)\n")        # uncommitted work in the worktree
    c.register_projection({"status": "RUNNING", "server_epoch": 1380, "remaining_seconds": 1620})  # slack 120 s
    plan = c.plan_tasks()
    details = c.task_details("BUG-02")
    assert details["phase"] == "RUNNING"
    assert details["workerResult"] is None                                   # no emergency_result
    assert not (Path(claim["matchRoot"]) / "answers" / "BUG-02" / "submit.json").exists()
    assert [r["key"] for r in plan["deadlineFlushRequests"]] == ["BUG-02"]
    c.plan_tasks()
    flush_events = [e for e in c.events_since(0)["events"] if e["type"] == "DEADLINE_FLUSH_REQUESTED"]
    assert len(flush_events) == 1 and flush_events[0]["attemptId"] == details["data"]["attempt_id"]


def test_1b_flush_timeout_is_recorded_and_phase_kept(tmp_path):
    c = coordinator(tmp_path, [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100, "deadline_epoch": 1500}])
    started_claim(c, "BUG-02", "w1")
    c.register_projection({"status": "RUNNING", "server_epoch": 1450, "remaining_seconds": 1550})
    c.plan_tasks()
    c.register_projection({"status": "RUNNING", "server_epoch": 1510, "remaining_seconds": 1490})
    plan = c.plan_tasks()
    assert c.task_details("BUG-02")["phase"] == "RUNNING"
    overdue = {r["key"]: r["reasons"] for r in plan["overdueWorkers"]}
    assert "FLUSH_TIMEOUT" in overdue["BUG-02"]


# ---- 2. the final result of the right attempt is accepted; late/stale ones go to audit --------------
def test_2_final_result_after_flush_request_is_accepted(tmp_path):
    c = coordinator(tmp_path, [{"key": "BUG-02", "type": "BUG", "status": "OPEN", "points": 100, "deadline_epoch": 1500}])
    claim = started_claim(c, "BUG-02", "w1")
    c.register_projection({"status": "RUNNING", "server_epoch": 1390, "remaining_seconds": 1610})
    c.plan_tasks()
    attempt = claim["taskEnvelope"]["attempt_id"]
    out = c.record_worker_result("BUG-02", result("BUG-02", "w1", attempt_id=attempt))
    assert out["phase"] == "READY_TO_INTEGRATE"


def test_2b_late_and_stale_results_are_audited_not_rejected(tmp_path):
    c = coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100}])
    started_claim(c, "CR-01", "w1")
    stale = c.record_worker_result("CR-01", result("CR-01", "w1", attempt_id="old-attempt"))
    assert stale["auditOnly"] and stale["reason"] == "stale attempt"
    assert c.task_details("CR-01")["phase"] == "RUNNING"
    c.reconcile_worker("CR-01", confirmed_stopped=True, reason="closed", requeue=False)
    late = c.record_worker_result("CR-01", result("CR-01", "w1"))
    assert late["auditOnly"] and c.task_details("CR-01")["phase"] == "CLOSED"
    assert len(c.task_details("CR-01")["data"]["late_results"]) == 2


# ---- 3. PARTIAL passes the gate under policy; DONE with a non-PASS row is blocked, never downgraded ----
def _guard_repo(tmp_path, answer):
    repo = make_repo(tmp_path, with_origin=True)
    path = repo / "source" / "answers" / "TASK-01" / "submit.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(answer), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "answer")
    git(repo, "push", "-q", "origin", "main")
    return repo, git(repo, "rev-parse", "HEAD")


def _validate(repo, sha, answer, allow_partial):
    return PreSubmissionGitGuard(repo).validate_pre_submission(
        target_commit_sha=sha, assigned_branch_ref="main", backlog_item_key="TASK-01", answer_payload=answer,
        match_root=repo / "source", checkout_root=repo, require_remote=True, raise_on_error=True,
        required_test_ids=["TC-01", "TC-02"], allow_partial=allow_partial)


def test_3_partial_passes_gate_and_done_with_failing_row_is_blocked(tmp_path):
    partial = {"status": "PARTIAL", "testResults": [{"id": "TC-01", "result": "PASS"}, {"id": "TC-02", "result": "NOT_RUN"}]}
    repo, sha = _guard_repo(tmp_path / "p", partial)
    assert _validate(repo, sha, partial, allow_partial=True).valid
    with pytest.raises(SubmissionGateError):
        _validate(repo, sha, partial, allow_partial=False)
    done = {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS"}, {"id": "TC-02", "result": "FAIL"}]}
    repo, sha = _guard_repo(tmp_path / "d", done)
    for flag in (True, False):
        with pytest.raises(SubmissionGateError, match="DONE answer needs every test case PASS"):
            _validate(repo, sha, dict(done), allow_partial=flag)


# ---- 4. a PARTIAL with a failing test is recorded honestly and is not queued (BUG/CR/PI) -------------
@pytest.mark.parametrize("key,kind", [("BUG-01", "BUG"), ("CR-01", "CR"), ("PI-01", "PI")])
def test_4_failing_partial_recorded_not_verified_not_queued(tmp_path, kind, key):
    c = coordinator(tmp_path, [{"key": key, "type": kind, "status": "OPEN", "points": 100}])
    claim = started_claim(c, key, "w1", {"minimumVerificationScenarios": [{"id": "TC-01"}, {"id": "TC-02"}]})
    write_answer(claim, key, {"status": "PARTIAL", "testResults": [{"id": "TC-01", "result": "PASS"},
                                                                  {"id": "TC-02", "result": "FAIL"}]})
    tests = [{"command": "vitest run a", "passed": True, "exit_code": 0},
             {"command": "playwright tc-02", "passed": False, "exit_code": 1}]
    out = c.record_worker_result(key, result(key, "w1", status="PARTIAL", test_results=tests))
    res = out["workerResult"]
    assert res["mvs_passed"] is False and res["verification_status"] == "RECORDED"
    assert res["test_results"][1]["passed"] is False                        # kept, not moved away
    assert out["decision"]["action"] != policy.SUBMIT
    assert key not in [r["key"] for r in c.plan_tasks()["readyToIntegrate"]]
    with pytest.raises(NativeCoordinationError, match="Policy decision"):
        c.begin_integration(key)


def test_4b_moving_a_failing_row_to_diagnostics_does_not_buy_verified(tmp_path):
    c = coordinator(tmp_path, [{"key": "BUG-09", "type": "BUG", "status": "OPEN", "points": 80}])
    claim = started_claim(c, "BUG-09", "w1")
    write_answer(claim, "BUG-09", {"status": "PARTIAL", "testResults": []})
    out = c.record_worker_result("BUG-09", result("BUG-09", "w1", status="PARTIAL", diagnostic_test_results=[
        {"id": "BUG-09-TC-01", "command": "playwright", "passed": False, "exit_code": 0}]))
    assert out["workerResult"]["verification_status"] != "VERIFIED"


def test_4c_verified_only_when_mvs_passed(tmp_path):
    c = coordinator(tmp_path, [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    claim = started_claim(c, "TASK-01", "w1", {"minimumVerificationScenarios": [{"id": "TC-01"}]})
    write_answer(claim, "TASK-01", {"status": "DONE", "testResults": [{"id": "TC-01", "result": "PASS"}]})
    out = c.record_worker_result("TASK-01", result("TASK-01", "w1"))
    assert out["workerResult"]["verification_status"] == "VERIFIED"
    assert out["decision"]["action"] == policy.SUBMIT


# ---- 5. BLOCKED goes to NEEDS_ATTENTION and is never submitted ---------------------------------------
def test_5_blocked_needs_attention(tmp_path):
    c = coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100}])
    started_claim(c, "CR-01", "w1")
    assert c.record_worker_result("CR-01", result("CR-01", "w1", status="BLOCKED"))["phase"] == "NEEDS_ATTENTION"
    assert c.decide("CR-01")["action"] in (policy.REWORK, policy.CLOSE)


# ---- 6. CONTINUE_WORK keeps a running worker; REWORK is a bounded new attempt -------------------------
def test_6_continue_and_bounded_rework(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_MAX_REWORK", "1")
    c = coordinator(tmp_path, [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    claim = started_claim(c, "TASK-01", "w1")
    assert c.decide("TASK-01")["action"] == policy.CONTINUE_WORK
    assert c.task_details("TASK-01")["phase"] == "RUNNING"
    write_answer(claim, "TASK-01", {"status": "PARTIAL", "testResults": []})
    out = c.record_worker_result("TASK-01", result("TASK-01", "w1", status="PARTIAL"))
    assert out["decision"]["action"] == policy.REWORK
    c.reconcile_worker("TASK-01", confirmed_stopped=True, reason="rework", requeue=True)
    second = c.claim_task("TASK-01", "w2")
    assert second["taskEnvelope"]["attempt_id"] != claim["taskEnvelope"]["attempt_id"]
    assert second["taskEnvelope"]["prior_attempts"][0]["result"]["status"] == "PARTIAL"
    write_answer(second, "TASK-01", {"status": "PARTIAL", "testResults": []})
    out = c.record_worker_result("TASK-01", result("TASK-01", "w2", status="PARTIAL"))
    assert out["decision"]["action"] == policy.SUBMIT                      # no rework left: TASK PARTIAL is submitted
    with pytest.raises(NativeCoordinationError, match="REWORK limit"):
        c.reconcile_worker("TASK-01", confirmed_stopped=True, reason="again", requeue=True)


# ---- 7. one priority for ready sort, PI gate and dispatch --------------------------------------------
def test_7_critical_deadline_item_and_pi_are_ordered_consistently(tmp_path):
    c = coordinator(tmp_path, [{"key": "CR-07", "type": "CR", "status": "OPEN", "points": 100, "deadline_epoch": 1150},
                               {"key": "PI-01", "type": "PI", "status": "OPEN", "points": 90},
                               {"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    for i, key in enumerate(("CR-07", "PI-01", "TASK-01")):
        started_claim(c, key, f"w{i}")
        c.record_worker_result(key, result(key, f"w{i}"))
    plan = c.plan_tasks()
    assert [r["key"] for r in plan["readyToIntegrate"]] == ["CR-07", "PI-01", "TASK-01"]
    with pytest.raises(NativeCoordinationError, match="PI-first"):
        c.begin_integration("TASK-01")                                     # PI + ordinary item: PI first
    assert c.begin_integration("CR-07")["phase"] == "INTEGRATING"          # critical deadline passes the PI gate


def test_7b_dispatch_puts_pi_first_among_queued_items(tmp_path):
    c = coordinator(tmp_path, [{"key": "BUG-09", "type": "BUG", "status": "OPEN", "points": 80, "deadline_epoch": 1170},
                               {"key": "PI-02", "type": "PI", "status": "OPEN", "points": 90}])
    assert c.plan_tasks()["selected"][0]["key"] == "PI-02"


# ---- 8. unknown Submit outcome keeps the lock and is reconciled by replay ----------------------------
def test_8_ttl_does_not_release_lock_and_reconciliation_is_bounded(tmp_path):
    c = coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100},
                               {"key": "CR-02", "type": "CR", "status": "OPEN", "points": 100}], max_reconcile_attempts=2)
    for i, key in enumerate(("CR-01", "CR-02")):
        started_claim(c, key, f"w{i}")
        c.record_worker_result(key, result(key, f"w{i}"))
    c.begin_integration("CR-01")
    c.mark_submit_in_flight("CR-01")
    with c._db() as db:
        meta = dict(c._meta(db, "integration_submit_inflight"))
        meta["since"] = time.monotonic() - 121.0
        c._set_meta(db, "integration_submit_inflight", meta)
    plan = c.plan_tasks()
    assert plan["integrationOwner"] == "CR-01"
    assert plan["reconciliationRequired"][0]["key"] == "CR-01"
    with pytest.raises(NativeCoordinationError):
        c.begin_integration("CR-02")
    assert c.note_reconciliation_attempt("CR-01")["reconciliationRequired"]
    assert c.note_reconciliation_attempt("CR-01")["reconciliationRequired"]
    gave_up = c.note_reconciliation_attempt("CR-01")
    assert gave_up["gaveUp"] and c.task_details("CR-01")["phase"] == "NEEDS_ATTENTION"
    assert c.begin_integration("CR-02")["phase"] == "INTEGRATING"


# ---- MCP server fixtures -------------------------------------------------------------------------
def build_server(tmp_path, key, kind, answer_status, worker_tests=None):
    from runner.mcp_server import MCPHelperServer
    repo = make_repo(tmp_path, with_origin=True)
    c = coordinator(tmp_path, [{"key": key, "type": kind, "status": "OPEN", "points": 100}], remaining=2400, repo=repo)
    claim = started_claim(c, key, "w1")
    write_answer(claim, key, {"status": answer_status, "testResults": []})
    c.record_worker_result(key, result(key, "w1", status=answer_status,
                                       **({"test_results": worker_tests} if worker_tests is not None else {})))
    server = MCPHelperServer()
    server.workspace_root, server.checkout_root, server.match_root = tmp_path, repo, repo / "source"
    server.match_uuid, server.player_session_id, server.assigned_branch = "match-uuid", "session", "main"
    server.native = c
    server.adapter = MagicMock()
    server.adapter.api_base_url = "http://fake-gm"
    server.adapter.server_epoch = 1000
    server.adapter.server_clock_monotonic = time.monotonic()
    server.adapter.fetch_public_match.return_value = {
        "status": "RUNNING", "remainingSeconds": 2400, "items": [{"key": key, "type": kind, "status": "OPEN", "points": 100}]}
    server.git_guard = MagicMock()
    server.journal = SubmissionJournal(tmp_path / "journal.sqlite3")
    server.telemetry = MagicMock()
    return server, repo


def commit_answer(repo, key, answer):
    path = repo / "source" / "answers" / key / "submit.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(answer), encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", f"answer {key}")
    return git(repo, "rev-parse", "HEAD")


def test_3b_submit_work_passes_allow_partial_only_for_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_MAX_REWORK", "0")                                # TASK PARTIAL: no rework left -> SUBMIT
    server, repo = build_server(tmp_path, "TASK-01", "TASK", "PARTIAL")
    answer = {"status": "PARTIAL", "testResults": [{"id": "TC-01", "result": "NOT_RUN"}]}
    sha = commit_answer(repo, "TASK-01", answer)
    server.adapter.submit_work.return_value = {"submission": {"id": "sub-1"}}
    server.submit_work("cid-1", "TASK-01", sha, answer)
    assert server.git_guard.validate_pre_submission.call_args.kwargs["allow_partial"] is True
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


def test_5b_submit_work_refuses_blocked(tmp_path):
    server, repo = build_server(tmp_path, "TASK-01", "TASK", "DONE")
    with pytest.raises(ValueError, match="BLOCKED is not a submission path"):
        server.submit_work("cid-1", "TASK-01", git(repo, "rev-parse", "HEAD"), {"status": "BLOCKED"})
    server.adapter.submit_work.assert_not_called()


def test_4d_submit_work_refuses_partial_while_rework_is_still_possible(tmp_path):
    server, repo = build_server(tmp_path, "BUG-01", "BUG", "DONE")
    with pytest.raises(ValueError, match="policy decision"):
        server.submit_work("cid-1", "BUG-01", git(repo, "rev-parse", "HEAD"), {"status": "PARTIAL"})
    server.adapter.submit_work.assert_not_called()


def test_8b_unknown_outcome_is_replayed_with_identical_body(tmp_path):
    server, repo = build_server(tmp_path, "TASK-01", "TASK", "DONE")
    answer = {"status": "DONE", "testResults": []}
    sha = commit_answer(repo, "TASK-01", answer)

    class Timeout(Exception):
        status_code = None
    server.adapter.submit_work.side_effect = Timeout("read timeout")
    with pytest.raises(Timeout):
        server.submit_work("cid-1", "TASK-01", sha, answer)
    first_body = server.adapter.submit_work.call_args.kwargs["serialized_body_bytes"]
    with server.native._db() as db:                                           # outcome unknown for > TTL
        meta = dict(server.native._meta(db, "integration_submit_inflight"))
        meta["since"] = time.monotonic() - 121.0
        server.native._set_meta(db, "integration_submit_inflight", meta)
    server.native.plan_tasks()
    server.adapter.submit_work.side_effect = None
    server.adapter.submit_work.return_value = {"submission": {"id": "sub-1"}}
    server.submit_work("cid-2", "TASK-01", sha, {"status": "DONE", "changed": True})   # a different body is ignored
    replay = server.adapter.submit_work.call_args.kwargs
    assert replay["serialized_body_bytes"] == first_body and replay["idempotency_key"] == "cid-1"
    assert server.native.task_details("TASK-01")["phase"] == "SUBMITTED"


# ---- 9. Git failures stop the submit; a rebase moves the snapshot SHA explicitly ---------------------
def test_9_push_failure_stops_before_journal(tmp_path):
    server, repo = build_server(tmp_path, "TASK-01", "TASK", "DONE")
    answer = {"status": "DONE", "testResults": []}
    sha = commit_answer(repo, "TASK-01", answer)
    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(RuntimeError, match="git pull --rebase failed|git push failed"):
        server.submit_work("cid-1", "TASK-01", sha, answer)
    assert server.journal.get_entry("match-uuid", "TASK-01") is None
    server.adapter.submit_work.assert_not_called()
    with server.native._db() as db:
        assert server.native._meta(db, "integration_owner") is None


def test_9b_rebase_that_moves_the_commit_submits_the_new_remote_sha(tmp_path):
    server, repo = build_server(tmp_path, "TASK-01", "TASK", "DONE")
    other = tmp_path / "other"
    subprocess.run(["git", "clone", "-q", "-b", "main", str(tmp_path / "origin.git"), str(other)], check=True)
    git(other, "config", "user.email", "o@o"); git(other, "config", "user.name", "O")
    (other / "source" / "other.txt").write_text("x\n")
    git(other, "add", "."); git(other, "commit", "-q", "-m", "teammate"); git(other, "push", "-q", "origin", "main")
    answer = {"status": "DONE", "testResults": []}
    old_sha = commit_answer(repo, "TASK-01", answer)
    server.adapter.submit_work.return_value = {"submission": {"id": "sub-1"}}
    server.submit_work("cid-1", "TASK-01", old_sha, answer)
    sent = server.adapter.submit_work.call_args.kwargs["git_revision"]
    assert sent != old_sha
    assert sent == git(repo, "ls-remote", "origin", "refs/heads/main").split()[0]


# ---- 10. parity mismatch names the field and code point; no normalization ---------------------------
def test_10_parity_diff_reports_field_and_code_point():
    nfc = {"summary": unicodedata.normalize("NFC", "Quỹ"), "n": 1}
    nfd = {"summary": unicodedata.normalize("NFD", "Quỹ"), "n": 1}
    diffs = answer_field_differences(nfc, nfd)
    assert diffs and diffs[0].startswith("$.summary: first difference at char")
    assert answer_field_differences({"a": 1}, {"a": 1}) == []


# ---- 11. checkpoint never changes phase; result-available event is not duplicated --------------------
def test_11_checkpoint_keeps_phase_and_events_are_idempotent(tmp_path):
    c = coordinator(tmp_path, [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    claim = started_claim(c, "TASK-01", "w1")
    match = Path(claim["matchRoot"])
    (match / "worker_checkpoint.json").write_text(json.dumps({"version": 1, "attempt_id": claim["taskEnvelope"]["attempt_id"]}))
    c.plan_tasks()
    assert c.task_details("TASK-01")["phase"] == "RUNNING"
    (match / "worker_result.json").write_text("{}")
    first = c.plan_tasks()
    c.plan_tasks()
    assert [r["key"] for r in first["workerResultsAvailable"]] == ["TASK-01"]
    available = [e for e in c.events_since(0)["events"] if e["type"] == "WORKER_RESULT_AVAILABLE"]
    assert len(available) == 1


# ---- 12. wait_for_update returns early on a native event ---------------------------------------------
def test_12_wait_for_update_returns_on_event_and_advances_cursor(tmp_path):
    from runner.mcp_server import MCPHelperServer
    c = coordinator(tmp_path, [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    claim = started_claim(c, "TASK-01", "w1")
    (Path(claim["matchRoot"]) / "worker_result.json").write_text("{}")
    server = MCPHelperServer()
    server.native = c
    started = time.time()
    out = server.wait_for_update(10000)
    assert out["status"] == "EVENT" and time.time() - started < 5
    assert any(e["type"] == "WORKER_RESULT_AVAILABLE" for e in out["events"])
    again = server.wait_for_update(300, out["eventCursor"])
    assert again["status"] == "TIMEOUT"


# ---- 13. effective runtime config is checked before the match ----------------------------------------
def test_13_effective_worker_config_mismatch_stops(monkeypatch):
    from runner.mcp_server import check_effective_worker_config
    cfg = {"max_workers": 4, "normal_workers": 3, "pi_burst_slots": 2, "source": "mcp_server default"}
    monkeypatch.setenv("MTM_EXPECTED_WORKER_CONFIG", "16/15/8")
    with pytest.raises(RuntimeError, match="differs"):
        check_effective_worker_config(cfg)
    monkeypatch.delenv("MTM_EXPECTED_WORKER_CONFIG")
    monkeypatch.setenv("MTM_ENV", "production")
    with pytest.raises(RuntimeError, match="not the"):
        check_effective_worker_config(cfg)
    check_effective_worker_config({**cfg, "source": "env"})


# ---- 14. CLOSED is terminal --------------------------------------------------------------------------
def test_14_closed_item_decides_close(tmp_path):
    c = coordinator(tmp_path, [{"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100}])
    started_claim(c, "CR-01", "w1")
    c.record_worker_result("CR-01", result("CR-01", "w1"))
    c.register_projection({"status": "RUNNING", "server_epoch": 1001, "remaining_seconds": 1999,
                           "items": [{"key": "CR-01", "status": "CLOSED"}]})
    c.plan_tasks()
    assert c.task_details("CR-01")["phase"] == "CLOSED"
    assert c.decide("CR-01")["action"] == policy.CLOSE


# ---- explicit flush (S0 option 3) --------------------------------------------------------------------
def test_explicit_flush_requires_stop_and_worker_partial(tmp_path):
    c = coordinator(tmp_path, [{"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 50}])
    claim = started_claim(c, "TASK-01", "w1")
    with pytest.raises(NativeCoordinationError):
        c.flush_snapshot("TASK-01", confirmed_stopped=False, reason="x")
    with pytest.raises(NativeCoordinationError, match="PARTIAL"):
        c.flush_snapshot("TASK-01", confirmed_stopped=True, reason="deadline")
    write_answer(claim, "TASK-01", {"status": "PARTIAL", "testResults": []})
    out = c.flush_snapshot("TASK-01", confirmed_stopped=True, reason="deadline")
    assert out["workerResult"]["verification_status"] == "UNVERIFIED" and out["workerResult"]["snapshot_revision"]
    late = c.record_worker_result("TASK-01", result("TASK-01", "w1", attempt_id=claim["taskEnvelope"]["attempt_id"]))
    assert late["auditOnly"]                                                   # the fenced attempt cannot overwrite


# ---- policy unit tests -------------------------------------------------------------------------------
def test_policy_submit_budget_uses_measured_p95(monkeypatch):
    monkeypatch.setenv("MTM_SUBMIT_P95_DEFAULT_SECONDS", "60")
    monkeypatch.setenv("MTM_SUBMIT_SAFETY_MARGIN_SECONDS", "30")
    assert policy.submit_budget([]) == 90
    assert policy.submit_budget([10, 20, 30, 40]) == 70


def test_policy_every_ordinary_item_follows_the_same_partial_rule(monkeypatch):
    monkeypatch.delenv("MTM_PI_PARTIAL_SUBMIT", raising=False)
    for kind in ("TASK", "BUG", "CR", "HEAVY"):
        d = policy.decide(item_type=kind, phase="READY_TO_INTEGRATE", item_open=True, time_left=50,
                          result={"status": "PARTIAL"}, answer_status="PARTIAL", rework_count=0, budget=90)
        assert d.must_finalize and d.action == policy.SUBMIT, kind
    d = policy.decide(item_type="PI", phase="READY_TO_INTEGRATE", item_open=True, time_left=50,
                      result={"status": "PARTIAL"}, answer_status="PARTIAL", rework_count=0, budget=90)
    assert d.action == policy.CLOSE
    monkeypatch.setenv("MTM_PI_PARTIAL_SUBMIT", "1")
    d = policy.decide(item_type="PI", phase="READY_TO_INTEGRATE", item_open=True, time_left=50,
                      result={"status": "PARTIAL"}, answer_status="PARTIAL", rework_count=0, budget=90)
    assert d.action == policy.SUBMIT


def test_policy_hold_for_review_is_time_bounded():
    kwargs = dict(item_type="CR", phase="READY_TO_INTEGRATE", item_open=True, time_left=1500,
                  result={"status": "DONE", "mvs_passed": True, "verification_status": "EVIDENCE_ONLY"},
                  answer_status="DONE", rework_count=0, budget=90)
    assert policy.decide(**kwargs, ready_age=5).action == policy.HOLD_FOR_REVIEW
    assert policy.decide(**kwargs, ready_age=600).action == policy.SUBMIT
    assert policy.decide(**{**kwargs, "item_type": "PI"}, ready_age=5).action == policy.SUBMIT
