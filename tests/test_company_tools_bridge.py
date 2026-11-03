import json
import pytest
from pathlib import Path
from unittest.mock import patch
from runner.mcp_server import MCPHelperServer, get_tool_definitions


def test_tool_definitions_include_company_tools():
    tools = get_tool_definitions()
    tool_names = {t["name"] for t in tools}
    assert "list_company_tools" in tool_names
    assert "call_company_tool" in tool_names
    assert "fetch_fixture" in tool_names


def test_mcp_helper_server_fetch_fixture_no_synthetic(tmp_path):
    server = MCPHelperServer(workspace_root=tmp_path)
    server.match_root = tmp_path / "matches" / "TEST1"
    server.match_root.mkdir(parents=True, exist_ok=True)

    # Without client, fetch_fixture should cleanly report UNAVAILABLE, NEVER synthetic!
    res = server.fetch_fixture("CR-01")
    assert res["status"] == "UNAVAILABLE"

    # Verify no fake file was generated under answers/CR-01
    fake_path = server.match_root / "answers" / "CR-01" / "evidence" / "cr-01-test-data.json"
    assert not fake_path.exists()


def test_mcp_helper_server_fetch_fixture_with_company_tools(tmp_path):
    server = MCPHelperServer(workspace_root=tmp_path)
    server.match_root = tmp_path / "matches" / "TEST1"
    server.match_root.mkdir(parents=True, exist_ok=True)

    mock_fixture_data = {
        "fixtureId": "fixture-official-456",
        "backlogItemKey": "CR-01",
        "invoices": [{"number": "INV-100", "totalMinor": 5000}]
    }

    class MockCompanyToolsClient:
        def discover_tools(self):
            return [
                {
                    "name": "company_qa_store",
                    "description": "QA fixture store",
                    "category": "qa_data"
                }
            ]

        def call_tool(self, name, arguments):
            assert name == "company_qa_store"
            return mock_fixture_data

    server.company_tools_client = MockCompanyToolsClient()

    res = server.fetch_fixture("CR-01")
    assert res["status"] == "FETCHED"
    assert res["tool"] == "company_qa_store"

    target_file = server.match_root / res["path"]
    assert target_file.exists()
    content = json.loads(target_file.read_text(encoding="utf-8"))
    assert content["fixtureId"] == "fixture-official-456"

    # Sidecar verification
    sidecar_file = server.match_root / res["sidecar"]
    assert sidecar_file.exists()
    sidecar_data = json.loads(sidecar_file.read_text(encoding="utf-8"))
    assert sidecar_data["official"] is True
    assert sidecar_data["sha256"] == res["sha256"]
