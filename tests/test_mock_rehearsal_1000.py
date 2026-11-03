"""1000/1000 Mock Tournament Rehearsal Test (Pre-Match Simulation).

Simulates a complete 40-minute competition with 10 brand-new backlog items across 4 waves,
dynamic ROI scheduling, Company Tools MCP bridge, authoritative QA data store,
fail-closed submission gating, and 1000/1000 perfect score verification.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any, Dict, List
import pytest

from mtm_core.contracts import TaskEnvelope, WorkerResult, WorkerStatus, TaskState
from mtm_core.git import git_init, get_head_sha, run_git
from mtm_competition.tasks.store import TaskStore
from mtm_competition.scheduling.scheduler import TournamentScheduler
from mtm_competition.integration.git_guard import GitGuard, PreSubmissionGitGuard
from mtm_competition.integration.submission_guard import SubmissionGuard, SubmissionGateError
from mtm_competition.adapters.company_tools import CompanyToolsClient, categorize_tool


class MockMatchMCPEndpoint:
    """Simulates Match MCP HTTP endpoint providing company tools."""

    def __init__(self):
        self.fixtures = {
            "CR-01": {"invoices": [{"id": 1, "currency": "USD", "amount": 100.0, "tax_rate": 0.1}]},
            "BUG-02": {"transactions": [{"tx_id": "TX-99", "status": "PENDING", "fee": 2.5}]},
            "CR-02": {"subscriptions": [{"tier": "ENTERPRISE", "seats": 50, "discount": 0.15}]},
            "BUG-03": {"accounts": [{"acc_no": "AC-101", "balance": 1500000.0, "currency": "VND"}]},
            "CR-03": {"webhooks": [{"endpoint": "https://api.partner.com/events", "secret": "whsec_live"}]},
        }
        self.kb_docs = {
            "KB-TAX-02": "# KB-TAX-02: Rounding rules for international currency and tax brackets.",
            "KB-INV-01": "# KB-INV-01: Invoicing idempotency and ledger reconciliation rules.",
        }
        self.prod_logs = {
            "PI-01": [
                {"timestamp": "2026-11-03T10:14:00Z", "level": "ERROR", "msg": "Pool connection leak in redis_session_mgr: pool exhausted (max 100)"},
                {"timestamp": "2026-11-03T10:14:05Z", "level": "FATAL", "msg": "HTTP 503 Service Unavailable: upstream service timed out"},
            ]
        }

    def list_tools(self) -> List[Dict[str, Any]]:
        return [
            {
                "name": "corporate_kb_search",
                "description": "Knowledge Base standards and official company policy documents",
                "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
            },
            {
                "name": "test_dataset_store",
                "description": "QA test data store containing official test datasets and seed fixtures",
                "inputSchema": {"type": "object", "properties": {"backlog_item_key": {"type": "string"}}},
            },
            {
                "name": "observability_traces",
                "description": "Production log observability query and error trace inspector",
                "inputSchema": {"type": "object", "properties": {"incident_id": {"type": "string"}}},
            },
        ]

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name == "test_dataset_store":
            key = arguments.get("backlog_item_key", "")
            if key in self.fixtures:
                return {"content": [{"type": "text", "text": json.dumps(self.fixtures[key])}]}
            return {"content": [{"type": "text", "text": "{}"}]}
        elif name == "corporate_kb_search":
            query = arguments.get("query", "")
            for doc_id, text in self.kb_docs.items():
                if doc_id in query or query in doc_id:
                    return {"content": [{"type": "text", "text": text}]}
            return {"content": [{"type": "text", "text": "Document not found"}]}
        elif name == "observability_traces":
            inc = arguments.get("incident_id", "")
            logs = self.prod_logs.get(inc, [])
            return {"content": [{"type": "text", "text": json.dumps(logs)}]}
        raise ValueError(f"Unknown company tool: {name}")


def test_1000_point_tournament_rehearsal(tmp_path: Path):
    """Rehearse complete 10-item match to verify 1000/1000 score potential."""
    remote_repo = tmp_path / "remote.git"
    run_git(tmp_path, ["init", "--bare", str(remote_repo)])

    repo = tmp_path / "customer_repo"
    git_init(repo)
    run_git(repo, ["remote", "add", "origin", str(remote_repo)])

    # Setup base repository
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "index.py").write_text("# Production app codebase\n", encoding="utf-8")
    run_git(repo, ["add", "-A"])
    run_git(repo, ["commit", "-m", "Initial baseline commit"])
    run_git(repo, ["push", "origin", "main"])
    head_sha = get_head_sha(repo)

    # 1. Initialize Match MCP Endpoint & Company Tools Client
    mcp_endpoint = MockMatchMCPEndpoint()
    tools_client = CompanyToolsClient(
        base_url="https://masterthemachine.tech",
        match_id="MOCK-MATCH-1000",
        api_key="secret-key-1000",
        state_dir=tmp_path / "state",
    )
    tools_client._send_rpc = lambda method, params=None: (
        {"jsonrpc": "2.0", "result": {"tools": mcp_endpoint.list_tools()}}
        if method == "tools/list"
        else {"jsonrpc": "2.0", "result": mcp_endpoint.call_tool(params["name"], params.get("arguments", {}))}
    )

    discovered = tools_client.discover_tools()
    assert len(discovered) == 3

    # Verify Company Tools categorization
    qa_tool = next(t for t in discovered if t["category"] == "qa_data")
    kb_tool = next(t for t in discovered if t["category"] == "kb")
    log_tool = next(t for t in discovered if t["category"] == "logs")

    assert qa_tool["name"] == "test_dataset_store"
    assert kb_tool["name"] == "corporate_kb_search"
    assert log_tool["name"] == "observability_traces"

    # 2. Setup Task Store, Scheduler, and SubmissionGuard
    db_file = tmp_path / "state.db"
    store = TaskStore(db_file)
    scheduler = TournamentScheduler(store, concurrency_limit=2)
    sub_guard = SubmissionGuard(store, strict_git_gate=True, require_remote=True)

    # 3. Define 10 Competition Items across 4 Waves (Total 1000 Points)
    match_start = 1000.0
    items = [
        # Wave 1 (t=0)
        {"id": "TASK-01", "type": "TASK", "points": 100, "wave": 1, "release_t": 0, "deadline": 2400, "requires_fixture": False},
        {"id": "BUG-01", "type": "BUG", "points": 100, "wave": 1, "release_t": 0, "deadline": 2400, "requires_fixture": False},
        {"id": "CR-01", "type": "CR", "points": 140, "wave": 1, "release_t": 0, "deadline": 2400, "requires_fixture": True},
        # Wave 2 (t=8m = 480s)
        {"id": "PI-01", "type": "PI", "points": 150, "wave": 2, "release_t": 480, "deadline": 1200, "requires_fixture": False, "requires_logs": True},
        {"id": "BUG-02", "type": "BUG", "points": 80, "wave": 2, "release_t": 480, "deadline": 2400, "requires_fixture": True},
        # Wave 3 (t=12m = 720s)
        {"id": "CR-02", "type": "CR", "points": 120, "wave": 3, "release_t": 720, "deadline": 2400, "requires_fixture": True},
        {"id": "TASK-02", "type": "TASK", "points": 60, "wave": 3, "release_t": 720, "deadline": 2400, "requires_fixture": False},
        # Wave 4 (t=20m = 1200s)
        {"id": "BUG-03", "type": "BUG", "points": 100, "wave": 4, "release_t": 1200, "deadline": 2400, "requires_fixture": True},
        {"id": "CR-03", "type": "CR", "points": 100, "wave": 4, "release_t": 1200, "deadline": 2400, "requires_fixture": True},
        {"id": "TASK-03", "type": "TASK", "points": 50, "wave": 4, "release_t": 1200, "deadline": 2400, "requires_fixture": False},
    ]

    total_possible_score = sum(it["points"] for it in items)
    assert total_possible_score == 1000

    awarded_points = {}
    def mock_gm_submit(**kwargs):
        task_id = kwargs.get("backlog_item_key") or kwargs.get("task_id")
        ans = kwargs.get("answer", {})
        if ans.get("status") == "DONE":
            item_spec = next(i for i in items if i["id"] == task_id)
            awarded = item_spec["points"]
            awarded_points[task_id] = awarded
            return {"status": "PASSED", "points": awarded, "submission_id": f"sub_{task_id}"}
        return {"status": "PARTIAL", "points": 20, "submission_id": f"sub_{task_id}"}

    simulated_clock = match_start
    released_ids = set()
    completed_ids = set()

    # Time progression simulation
    while len(completed_ids) < len(items):
        elapsed_match = simulated_clock - match_start

        # Release waves into task store
        for it in items:
            if it["id"] not in released_ids and elapsed_match >= it["release_t"]:
                env = TaskEnvelope(
                    task_id=it["id"],
                    task_type=it["type"],
                    title=f"Resolve {it['id']}",
                    description=f"Specification for {it['id']}",
                    repository_path=str(repo),
                    points=it["points"],
                    deadline_epoch=match_start + it["deadline"],
                    evidence_required=[f"{it['id'].lower()}-evidence.txt"] if not it.get("requires_fixture") else [f"{it['id'].lower()}-test-data.json"],
                    metadata={
                        "company_tools": discovered,
                        "submitTemplate": {"status": "", "testResults": []},
                    },
                )
                store.add_task(env)
                released_ids.add(it["id"])

        # Select next task from scheduler
        next_task_dict = scheduler.select_next_task()
        if not next_task_dict:
            simulated_clock += 60.0
            continue

        task_id = next_task_dict["task_id"]
        scheduler.assign_task(task_id, worker_id="worker_1")
        claimed_raw = store.get_task(task_id)
        claimed_env = TaskEnvelope.from_dict(claimed_raw) if isinstance(claimed_raw, dict) else claimed_raw
        it = next(i for i in items if i["id"] == task_id)

        # Worker Execution
        ans_dir = repo / "answers" / task_id
        ev_dir = ans_dir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)

        ev_paths = []
        if it.get("requires_fixture"):
            # Call Company Tools Bridge for authoritative QA fixture
            resp = tools_client.call_tool(qa_tool["name"], {"backlog_item_key": task_id})
            if isinstance(resp, (dict, list)):
                fixture_raw = json.dumps(resp).encode("utf-8")
            else:
                fixture_raw = str(resp).encode("utf-8")
            fixture_sha = hashlib.sha256(fixture_raw).hexdigest()

            fixture_path = ev_dir / f"{task_id.lower()}-test-data.json"
            fixture_path.write_bytes(fixture_raw)
            
            # Write official sidecar
            sidecar_path = fixture_path.with_name(fixture_path.name + ".source.json")
            sidecar_path.write_text(json.dumps({
                "tool": qa_tool["name"],
                "arguments": {"backlog_item_key": task_id},
                "sha256": fixture_sha,
                "fetched_at": "2026-11-03T10:15:00Z",
            }, indent=2), encoding="utf-8")
            
            ev_paths.append(f"{task_id.lower()}-test-data.json")

        if it.get("requires_logs"):
            # Call Company Tools Bridge for production incident logs
            resp = tools_client.call_tool(log_tool["name"], {"incident_id": task_id})
            if isinstance(resp, (dict, list)):
                log_raw = json.dumps(resp).encode("utf-8")
            else:
                log_raw = str(resp).encode("utf-8")
            (ev_dir / f"{task_id.lower()}-incident-traces.json").write_bytes(log_raw)

        # Generate primary evidence file
        prim_ev = f"{task_id.lower()}-evidence.txt" if not it.get("requires_fixture") else f"{task_id.lower()}-test-data.json"
        if not it.get("requires_fixture"):
            (ev_dir / prim_ev).write_text(f"Executed test plan for {task_id}: PASS\n", encoding="utf-8")
            ev_paths.append(prim_ev)

        # Worker fix in codebase
        (repo / "src" / f"{task_id.lower()}.py").write_text(f"# Solution for {task_id}\n", encoding="utf-8")

        # Create submit.json matching template
        answer_payload = {
            "status": "DONE",
            "evidenceFiles": [f"evidence/{p}" for p in ev_paths],
            "testResults": [
                {
                    "id": f"{task_id}-TC-01",
                    "result": "PASS",
                    "evidence": [f"evidence/{prim_ev}"],
                    "command": f"pytest tests/test_{task_id.lower()}.py",
                }
            ],
        }
        (ans_dir / "submit.json").write_text(json.dumps(answer_payload, indent=2), encoding="utf-8")

        # Commit to Git and push to remote origin
        run_git(repo, ["add", "-A"])
        run_git(repo, ["commit", "-m", f"Resolve {task_id} with verified evidence"])
        run_git(repo, ["push", "origin", "main"])
        cur_sha = get_head_sha(repo)

        worker_result = WorkerResult(
            task_id=task_id,
            status=WorkerStatus.READY_FOR_REVIEW,
            changed_files=[f"src/{task_id.lower()}.py"],
            test_results=[{"command": f"pytest tests/test_{task_id.lower()}.py", "passed": True}],
            evidence_paths=ev_paths,
        )

        # Verified Git Revision
        rev = GitGuard(repo).verify_head(cur_sha, verify_pushed=True)

        # Execute guarded submission
        sub_res = sub_guard.execute_guarded_submission(
            envelope=claimed_env,
            worker_result=worker_result,
            revision=rev,
            game_master_submit_fn=mock_gm_submit,
            current_time=simulated_clock,
            answer_payload=answer_payload,
        )

        assert sub_res.success is True
        assert sub_res.status == "PASSED"
        assert sub_res.points_awarded == it["points"]
        scheduler.complete_task(task_id, TaskState.PASSED)
        completed_ids.add(task_id)
        simulated_clock += 60.0

    # 4. Final Verification: 10/10 Submitted & 1000/1000 Points Awarded!
    assert len(completed_ids) == 10
    total_score = sum(awarded_points.values())
    assert total_score == 1000, f"Expected 1000/1000, achieved: {total_score}"
    assert len(awarded_points) == 10
