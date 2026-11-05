"""Server-side deadline watchdog, stop grace and honest-PARTIAL submission (lessons from match RLL3DYW5)."""
import time

import pytest

from tests.test_dispatch_finalize import build, item, submitted_answer, worker_writes


def test_watchdog_requests_stop_then_submits_after_grace_without_root(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AUTO_FINALIZE_SLACK", "150")
    monkeypatch.setenv("MTM_STOP_GRACE_SECONDS", "0")
    s = build(tmp_path, [item("TASK-03", remainingSec=140)])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",), commit=False, result_file=False)
    first = s._watchdog_tick(now=time.time() + 10)
    assert first == [{"backlogItemKey": "TASK-03", "status": "STOP_REQUIRED"}]
    assert s.adapter.calls == []
    second = s._watchdog_tick(now=time.time() + 20)
    assert second[0]["status"] == "SUBMITTED"
    assert submitted_answer(s)["status"] == "PARTIAL"
    assert s.native.stop_state("TASK-03")["source"] == "deadline_grace"


def test_watchdog_waits_for_grace_before_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AUTO_FINALIZE_SLACK", "150")
    monkeypatch.setenv("MTM_STOP_GRACE_SECONDS", "600")
    s = build(tmp_path, [item("TASK-03", remainingSec=140)])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, evidence=("check.txt",), commit=False, result_file=False)
    s._watchdog_tick(now=time.time() + 10)
    again = s._watchdog_tick(now=time.time() + 20)
    assert again == [{"backlogItemKey": "TASK-03", "status": "STOP_PENDING"}]
    assert s.adapter.calls == []


def test_watchdog_ignores_items_outside_slack(tmp_path, monkeypatch):
    monkeypatch.setenv("MTM_AUTO_FINALIZE_SLACK", "150")
    s = build(tmp_path, [item("TASK-03", remainingSec=900)])
    entry = s.dispatch_ready()["spawn"][0]
    worker_writes(entry, commit=False, result_file=False)
    assert s._watchdog_tick() == []


def test_stop_worker_event_surfaces_to_root(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    s.dispatch_ready()
    s.native.request_stop("TASK-01", "test")
    events = s.native.poll_events()
    assert any(e["type"] == "stop_worker" and e["backlogItemKey"] == "TASK-01" for e in events)
    assert not any(e["type"] == "stop_worker" for e in s.native.poll_events())  # reported once


def test_never_spawned_requeue_does_not_burn_attempts(tmp_path):
    s = build(tmp_path, [item("TASK-01")])
    s.dispatch_ready()
    before = s.native.task_details("TASK-01")["data"].get("attempt_number", 1)
    s.reconcile_worker("TASK-01", True, "no native worker was created", requeue=True, never_spawned=True)
    d = s.native.task_details("TASK-01")["data"]
    assert d.get("attempt_number", 1) == before and not d.get("prior_attempts")


def test_dispatch_default_uses_four_normal_one_reserved_in_tool_text():
    from runner.mcp_server import get_tool_definitions
    text = next(t["description"] for t in get_tool_definitions() if t["name"] == "dispatch_ready")
    assert "4 normal" in text and "3 normal" not in text
