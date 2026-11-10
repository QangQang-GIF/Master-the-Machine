"""Root receives compact summaries; the full payload is saved for the worker."""
import json
from types import SimpleNamespace

from runner.mcp_server import compact_root_output

BIG = "x" * 20000


def server(tmp_path):
    return SimpleNamespace(workspace_root=tmp_path, match_code="M1")


def test_start_work_is_saved_and_summarised(tmp_path):
    val = {"files": {"challenge/BUG-01.md": BIG, "challenge/BUG-01-submit.json": "{}"},
           "submitTemplate": {"testResults": [{"id": "BUG-01-TC-01"}, {"id": "BUG-01-TC-02"}]},
           "companyContext": {"kb": [{"a": BIG}], "logs": []}, "requestId": "r1"}
    out = compact_root_output(server(tmp_path), "start_work", {"backlogItemKey": "BUG-01"}, val)
    assert len(json.dumps(out)) < 2000
    assert out["submitTemplateTestIds"] == ["BUG-01-TC-01", "BUG-01-TC-02"]
    assert out["files"] == ["challenge/BUG-01-submit.json", "challenge/BUG-01.md"]
    saved = json.loads(open(out["startDetailsPath"], encoding="utf-8").read())
    assert saved == val


def test_claim_task_keeps_spawn_fields_and_points_to_full_envelope(tmp_path):
    env = {"task_id": "CR-01", "task_type": "CR", "title": "t", "worker_id": "cr-01", "match_root": "/m",
           "repository_path": "/r", "time_budget_seconds": 900, "model": "gpt-6.1-sol", "reasoning_effort": "medium",
           "agent_profile": "team_feature_worker", "lane": "normal", "selected_skills": ["mtm-cr-worker"],
           "description": BIG, "metadata": {"start_details": {"files": {"a": BIG}}, "company_context": {"kb": [BIG]},
                                            "project_runtime_path": "/p/project_runtime.py", "match_id": "M1",
                                            "worker_id": "cr-01", "worker_result_path": "/m/worker_result.json",
                                            "company_tools": [{"name": "kb_search", "description": BIG}]}}
    val = {"taskEnvelope": env, "worktreeRoot": "/w", "matchRoot": "/m", "workerId": "cr-01"}
    out = compact_root_output(server(tmp_path), "claim_task", {"workerId": "cr-01"}, val)
    assert len(json.dumps(out)) < 3000
    summary = out["taskEnvelope"]
    for k in ("task_id", "model", "reasoning_effort", "agent_profile", "time_budget_seconds", "selected_skills"):
        assert summary[k] == env[k]
    assert summary["metadata"]["project_runtime_path"] == "/p/project_runtime.py"
    assert summary["metadata"]["company_tools"] == ["kb_search"]
    # The worker reads a slim envelope: delivered files are listed (they are in its worktree) and KB results
    # are separate files. The unmodified envelope is kept next to it for audit.
    slim = json.loads(open(summary["full_envelope_path"], encoding="utf-8").read())
    assert slim["metadata"]["start_details"]["delivered_files"] == ["a"]
    assert "files" not in slim["metadata"]["start_details"] and "company_context" not in slim["metadata"]
    assert len(json.dumps(slim)) < len(json.dumps(env)) - 30000
    kb_file = slim["metadata"]["company_context_files"][0]["path"]
    assert BIG in open(kb_file, encoding="utf-8").read()
    raw = summary["full_envelope_path"].replace("-envelope.json", "-envelope-raw.json")
    assert json.loads(open(raw, encoding="utf-8").read()) == env


def test_plan_tasks_drops_bulky_fields_only(tmp_path):
    val = {"availableSlots": 2, "selected": [{"key": "BUG-01", "type": "BUG", "deadlineEpoch": 5, "descriptionMd": BIG,
                                              "start_details": {"files": {"a": BIG}}, "slackSeconds": 100}]}
    out = compact_root_output(server(tmp_path), "plan_tasks", {}, val)
    # Paths, epochs and bulky fields are dropped; slackSeconds already says how much time is left.
    assert out["selected"] == [{"key": "BUG-01", "type": "BUG", "slackSeconds": 100}]
    assert out["availableSlots"] == 2


def test_flag_off_and_other_tools_are_untouched(tmp_path, monkeypatch):
    val = {"files": {"a": BIG}}
    assert compact_root_output(server(tmp_path), "submit_work", {}, val) is val
    monkeypatch.setenv("MTM_COMPACT_ROOT_OUTPUT", "0")
    assert compact_root_output(server(tmp_path), "start_work", {"backlogItemKey": "B-1"}, val) is val
