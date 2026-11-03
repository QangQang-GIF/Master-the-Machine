import json
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from mtm_competition.adapters.company_tools import CompanyToolsClient, categorize_tool


def test_categorize_tool():
    assert categorize_tool("query_knowledge_base", "Search company standards and KB docs") == "kb"
    assert categorize_tool("get_company_policy", "Policy documents") == "kb"
    assert categorize_tool("qa_test_data_store", "Retrieve official QA test fixtures") == "qa_data"
    assert categorize_tool("fetch_fixtures", "Get fixtures for test execution") == "qa_data"
    assert categorize_tool("production_logs", "Query production observability traces") == "logs"
    assert categorize_tool("custom_inventory_calc", "Calculate warehouse inventory") == "other"


def test_company_tools_discovery(tmp_path):
    mock_tools_response = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                {
                    "name": "internal_kb_search",
                    "description": "Company knowledge base for accounting standards",
                    "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}}
                },
                {
                    "name": "official_qa_store",
                    "description": "QA data store containing test fixtures for backlog items",
                    "inputSchema": {"type": "object", "properties": {"item_id": {"type": "string"}}}
                },
                {
                    "name": "prod_trace_logs",
                    "description": "Production observability logs for incident investigation",
                    "inputSchema": {"type": "object", "properties": {"timeframe": {"type": "string"}}}
                }
            ]
        }
    }

    client = CompanyToolsClient(
        base_url="https://masterthemachine.tech",
        match_id="9X4WQHE4",
        api_key="secret-team-key-123",
        state_dir=tmp_path
    )

    with patch.object(client, "_send_rpc") as mock_rpc:
        mock_rpc.return_value = mock_tools_response
        tools = client.discover_tools()

        assert len(tools) == 3
        kb_tool = next(t for t in tools if t["name"] == "internal_kb_search")
        assert kb_tool["category"] == "kb"

        qa_tool = next(t for t in tools if t["name"] == "official_qa_store")
        assert qa_tool["category"] == "qa_data"

        logs_tool = next(t for t in tools if t["name"] == "prod_trace_logs")
        assert logs_tool["category"] == "logs"

        # Check company-tools.json was persisted
        persisted_file = tmp_path / "company-tools.json"
        assert persisted_file.exists()
        persisted_data = json.loads(persisted_file.read_text(encoding="utf-8"))
        assert len(persisted_data["tools"]) == 3
        assert persisted_data["categories"]["kb"] == ["internal_kb_search"]
        assert persisted_data["categories"]["qa_data"] == ["official_qa_store"]
        assert persisted_data["categories"]["logs"] == ["prod_trace_logs"]


def test_company_tools_call_and_audit(tmp_path):
    client = CompanyToolsClient(
        base_url="https://masterthemachine.tech",
        match_id="9X4WQHE4",
        api_key="secret-team-key-123",
        state_dir=tmp_path
    )

    mock_call_response = {
        "jsonrpc": "2.0",
        "id": 2,
        "result": {
            "content": [
                {"type": "text", "text": json.dumps({"fixtureId": "fixture-cr-01-official", "records": [1, 2, 3]})}
            ]
        }
    }

    with patch.object(client, "_send_rpc") as mock_rpc:
        mock_rpc.return_value = mock_call_response
        res = client.call_tool("official_qa_store", {"item_id": "CR-01"})

        assert res["fixtureId"] == "fixture-cr-01-official"
        assert res["records"] == [1, 2, 3]

        # Audit log verification
        audit_file = tmp_path / "company-tool-calls.jsonl"
        assert audit_file.exists()
        lines = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) == 1
        assert lines[0]["tool"] == "official_qa_store"
        assert lines[0]["arguments"] == {"item_id": "CR-01"}
        assert "sha256" in lines[0]
        assert "duration_ms" in lines[0]

        # Test caching: 2nd call should hit cache without RPC
        mock_rpc.reset_mock()
        res2 = client.call_tool("official_qa_store", {"item_id": "CR-01"})
        assert res2 == res
        mock_rpc.assert_not_called()


def test_company_tools_error_handling(tmp_path):
    client = CompanyToolsClient(
        base_url="https://masterthemachine.tech",
        match_id="9X4WQHE4",
        api_key="secret-team-key-123",
        state_dir=tmp_path
    )

    with patch.object(client, "_send_rpc") as mock_rpc:
        # Simulate business error inside tool
        mock_rpc.return_value = {
            "jsonrpc": "2.0",
            "id": 2,
            "result": {
                "isError": True,
                "error": {"code": "ITEM_NOT_FOUND", "message": "Backlog item not found in QA store"}
            }
        }
        with pytest.raises(RuntimeError, match="Backlog item not found"):
            client.call_tool("official_qa_store", {"item_id": "UNKNOWN"})
