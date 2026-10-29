"""Persistent stdio fallback for the exact same guarded MCP helper.

The native root may launch this Python helper (never another AI CLI) when its
provider has not exposed MCP tools. Credentials remain in process memory.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner.mcp_server import MCPHelperServer, get_tool_definitions


class ControlBridge:
    def __init__(self, server: MCPHelperServer | None = None):
        self.server = server or MCPHelperServer()
        self._secrets: set[str] = set()

    def call(self, request: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise ValueError("Request must be a JSON object")
        operation = request.get("operation")
        args = request.get("arguments", {})
        if not isinstance(args, dict):
            raise ValueError("arguments must be an object")
        if operation == "health":
            return {"status": "READY", "tools": [t["name"] for t in get_tool_definitions()],
                    "credentials": "MEMORY_ONLY", "transport": "LOCAL_STDIO"}
        definition = next((t for t in get_tool_definitions() if t["name"] == operation), None)
        if definition is None:
            raise ValueError("Unknown guarded operation")
        import jsonschema
        jsonschema.validate(args, definition["inputSchema"])
        if operation == "configure_session":
            descriptor = args["descriptor"]
            for key in ("password", "teamPassword", "apiKeySecret", "bearerToken", "runtimeTeamKey"):
                if isinstance(descriptor.get(key), str) and descriptor[key]:
                    self._secrets.add(descriptor[key])
            return self.server.configure_session(descriptor)
        names = {"backlogItemKey": "backlog_item_key", "workerId": "worker_id",
                 "estimatedSeconds": "estimated_seconds", "workerResult": "worker_result",
                 "clientSubmissionId": "client_submission_id", "gitRevision": "git_revision",
                 "timeoutMs": "timeout_ms", "confirmedStopped": "confirmed_stopped"}
        return getattr(self.server, operation)(**{names.get(k, k): v for k, v in args.items()})

    def reply(self, request: dict[str, Any]) -> str:
        try:
            response = {"ok": True, "result": self.call(request)}
        except Exception as exc:
            response = {"ok": False, "error": str(exc)}
        serialized = json.dumps(response, ensure_ascii=False)
        for secret in sorted(self._secrets, key=len, reverse=True):
            serialized = serialized.replace(secret, "[REDACTED]")
        return serialized


def main() -> None:
    bridge = ControlBridge()
    # Unified exec closes noninteractive stdin. Launch with tty=true; turn echo
    # off before accepting a credential-bearing descriptor on this private PTY.
    if sys.stdin.isatty() and sys.platform != "win32":
        import termios
        settings = termios.tcgetattr(sys.stdin.fileno())
        settings[3] &= ~termios.ECHO
        termios.tcsetattr(sys.stdin.fileno(), termios.TCSANOW, settings)
    print(json.dumps({"status": "BRIDGE_READY", "transport": "LOCAL_STDIO", "credentials": "MEMORY_ONLY"}), flush=True)
    for line in sys.stdin:
        try:
            if len(line) > 1048576:
                raise ValueError("Request exceeds bounded stdio limit")
            request = json.loads(line)
            result = bridge.reply(request)
        except Exception as exc:
            result = json.dumps({"ok": False, "error": str(exc)})
        print(result, flush=True)


if __name__ == "__main__":
    main()
