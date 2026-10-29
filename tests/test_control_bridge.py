import json
from unittest.mock import Mock

from runner.control import ControlBridge


def test_fallback_uses_same_guard_and_keeps_one_server():
    server = Mock()
    server.configure_session.return_value = {"status": "CONFIGURED"}
    server.submit_work.return_value = {"receiptId": "official-receipt"}
    bridge = ControlBridge(server)
    assert json.loads(bridge.reply({"operation": "configure_session", "arguments": {"descriptor": {"apiKeySecret": "test-private"}}}))["ok"]
    request = {"operation": "submit_work", "arguments": {"clientSubmissionId": "client-1", "backlogItemKey": "TASK-01", "gitRevision": "a" * 40, "answer": {}}}
    assert json.loads(bridge.reply(request))["result"]["receiptId"] == "official-receipt"
    server.submit_work.assert_called_once_with(client_submission_id="client-1", backlog_item_key="TASK-01", git_revision="a" * 40, answer={})


def test_no_arbitrary_methods_or_missing_required_arguments():
    server = Mock()
    bridge = ControlBridge(server)
    assert not json.loads(bridge.reply({"operation": "__getattribute__"}))["ok"]
    assert not json.loads(bridge.reply({"operation": "claim_task", "arguments": {}}))["ok"]
    server.claim_task.assert_not_called()


def test_guard_error_and_credentials_are_not_echoed():
    server = Mock()
    server.configure_session.side_effect = ValueError("authentication rejected fake-private-key")
    output = ControlBridge(server).reply({"operation": "configure_session", "arguments": {"descriptor": {"password": "fake-private-key"}}})
    assert "fake-private-key" not in output
    assert "authentication rejected" in output
    assert not json.loads(output)["ok"]


def test_health_requires_no_descriptor_or_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = ControlBridge().call({"operation": "health"})
    assert "configure_session" in result["tools"] and "submit_work" in result["tools"]
    assert list(tmp_path.iterdir()) == []
