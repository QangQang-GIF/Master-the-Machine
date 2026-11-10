"""collect_logs: every matching entry is recovered although search_logs returns at most 20 newest per call."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from mtm_competition.adapters.log_collector import collect_all  # noqa: E402


def make_log(n=755):
    entries = []
    for i in range(n):
        day, rest = divmod(i, 300)
        hour, minute = divmod(rest, 60)
        entries.append({"ts": f"2025-10-{13 + day:02d}T{hour % 24:02d}:{minute:02d}:{i % 60:02d}.{i % 1000:03d}Z",
                        "service": "rule-engine", "message": "rule.applied",
                        "context": {"seq": 3_100_000 + i, "txnId": f"TX-{i % 200:05d}", "amountMinor": -(i * 7)}})
    entries.append({"ts": "2025-11-16T11:30:00Z", "service": "deploy", "message": "deploy.completed",
                    "context": {"release": "2025.11.6", "seq": 3_103_476}})
    return entries


def fake_search(log, page=20):
    calls = []

    def search(query, service):
        calls.append((query, service))
        words = query.split()
        rows = [e for e in log if (not service or e["service"] == service)
                and all(w in json.dumps(e) for w in words)]
        rows.sort(key=lambda e: e["context"]["seq"], reverse=True)       # newest first
        return {"total": len(rows), "returned": min(page, len(rows)), "entries": rows[:page]}
    return search, calls


def test_recovers_every_entry_in_seq_order():
    log = make_log()
    search, calls = fake_search(log)
    out = collect_all(search, "rule.applied", "rule-engine")
    assert out["complete"] and out["total"] == out["unique"] == 755
    seqs = [e["context"]["seq"] for e in out["entries"]]
    assert seqs == sorted(seqs) and len(set(seqs)) == 755
    assert out["calls"] == len(calls) < 600


def test_small_result_needs_one_call():
    search, calls = fake_search(make_log())
    out = collect_all(search, "deploy.completed", "deploy")
    assert out["complete"] and out["unique"] == 1 and len(calls) == 1


def test_budget_exhaustion_is_reported_incomplete():
    search, _ = fake_search(make_log())
    out = collect_all(search, "rule.applied", "rule-engine", max_calls=5)
    assert out["complete"] is False and out["unique"] < out["total"]


def test_identical_timestamps_beyond_page_are_flagged():
    log = [{"ts": "2025-10-13T00:00:00Z", "service": "s", "message": "m", "context": {"seq": i}} for i in range(30)]
    search, _ = fake_search(log)
    out = collect_all(search, "m", "s")
    assert out["complete"] is False and out["incompletePartitions"]


def test_mcp_tool_writes_file_inside_allowed_folder(tmp_path):
    from runner.mcp_server import MCPHelperServer
    log = make_log(120)
    search, _ = fake_search(log)
    server = MCPHelperServer()
    server.workspace_root, server.match_code = tmp_path, "M1"
    server.checkout_root = tmp_path / "matches" / "M1"
    server.call_company_tool = lambda name, args: search(args.get("query", ""), args.get("service", ""))
    out_file = tmp_path / "matches" / "workers" / "PI-02" / "answers" / "PI-02" / "evidence" / "logs.json"
    res = server.collect_logs("rule.applied", "rule-engine", str(out_file))
    assert res["complete"] and res["unique"] == 120 and Path(res["path"]) == out_file
    assert len(json.loads(out_file.read_text(encoding="utf-8"))["entries"]) == 120
    default = server.collect_logs("deploy.completed", "deploy")
    assert Path(default["path"]).is_relative_to(tmp_path / "team-sdlc-pack" / "state" / "M1" / "logs")
    with pytest.raises(ValueError):
        server.collect_logs("x", "", "/etc/passwd")
