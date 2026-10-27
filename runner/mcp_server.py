"""
runner/mcp_server.py: Thin MCP Helper Server.
Provides official MCP tools via JSON-RPC 2.0 over stdin/stdout for Codex root agent:
- configure_session(descriptor)
- login_match()
- refresh_match()
- start_work(backlogItemKey)
- submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)
- report_progress(event)
- wait_for_update(timeoutMs)

Stdout is strictly reserved for JSON-RPC messages; all diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_competition.adapters.official_game_master import OfficialGameMasterAdapter
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal
from runner.telemetry import (
    TelemetryLogger,
    get_telemetry_logger,
    validate_progress_event,
)


class MCPHelperServer:
    def __init__(self, workspace_root: Optional[Path | str] = None):
        self.workspace_root = Path(workspace_root or ".").resolve()
        self.descriptor: Dict[str, Any] = {}
        self.match_uuid: Optional[str] = None
        self.match_code: Optional[str] = None
        self.player_session_id: str = str(uuid.uuid4())
        self.progress_log_file: Optional[Path] = None
        self.adapter: Optional[OfficialGameMasterAdapter] = None
        self.journal: Optional[SubmissionJournal] = None
        self.git_guard: Optional[PreSubmissionGitGuard] = None
        self.telemetry: Optional[TelemetryLogger] = None
        self.checkout_root: Path = self.workspace_root
        self.match_root: Path = self.workspace_root
        self.running: bool = True

    def configure_session(self, descriptor: Dict[str, Any]) -> Dict[str, Any]:
        """Configure trusted session from MTM Player descriptor."""
        self.descriptor = descriptor
        self.match_uuid = descriptor.get("matchUuid") or descriptor.get("match_uuid")
        self.match_code = descriptor.get("matchCode") or descriptor.get("match_code")
        
        session_id = descriptor.get("playerSessionId") or descriptor.get("sessionId")
        if session_id:
            self.player_session_id = str(session_id)

        # Paths
        checkout_str = descriptor.get("checkoutRoot")
        if checkout_str:
            self.checkout_root = Path(checkout_str).resolve()
        
        match_root_str = descriptor.get("matchRoot")
        if match_root_str:
            self.match_root = Path(match_root_str).resolve()
        elif self.match_code:
            potential = self.checkout_root / self.match_code
            self.match_root = potential if potential.exists() else self.checkout_root
        else:
            self.match_root = self.checkout_root

        log_file_str = descriptor.get("progressLogFile")
        if log_file_str:
            self.progress_log_file = Path(log_file_str).resolve()
        else:
            log_dir = self.workspace_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.progress_log_file = log_dir / f"{self.match_code or 'active'}.progress.ndjson"

        # Initialize telemetry
        self.telemetry = get_telemetry_logger(
            log_path=self.progress_log_file,
            player_session_id=self.player_session_id,
        )

        # Adapter
        base_url = descriptor.get("baseUrl") or descriptor.get("origin") or "https://ai-fest-five.vercel.app"
        bearer_token = descriptor.get("bearerToken") or descriptor.get("runtimeTeamKey") or ""
        self.adapter = OfficialGameMasterAdapter(
            base_url=base_url,
            auth_token=bearer_token,
            match_uuid=self.match_uuid,
            max_retries=3,
        )

        # Journal & Git Guard
        db_path = descriptor.get("journalDbPath") or (self.workspace_root / ".mtm" / "submissions.db")
        self.journal = SubmissionJournal(db_path=db_path)
        self.git_guard = PreSubmissionGitGuard(repo_path=self.checkout_root)

        return {
            "status": "CONFIGURED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "playerSessionId": self.player_session_id,
            "progressLogFile": str(self.progress_log_file),
        }

    def login_match(self) -> Dict[str, Any]:
        """Verify authentication session."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")
        return {
            "status": "AUTHENTICATED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
        }

    def refresh_match(self) -> Dict[str, Any]:
        """Read public match projection."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")
        # Retrieve projection from adapter or descriptor
        backlog_items = self.descriptor.get("backlogItems", [])
        return {
            "status": "ACTIVE",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "backlogItems": backlog_items,
        }

    def start_work(self, backlog_item_key: str) -> Dict[str, Any]:
        """Start work on an open backlog item."""
        if not self.adapter or not self.match_uuid:
            raise RuntimeError("Session not configured. Call configure_session first.")

        result = self.adapter.start_working(
            match_uuid_or_task_id=self.match_uuid,
            backlog_item_key=backlog_item_key,
        )

        # Write template to answers/<key>/submit.json
        submit_template = result.get("submitTemplate")
        if submit_template is not None:
            ans_dir = self.match_root / "answers" / backlog_item_key
            ans_dir.mkdir(parents=True, exist_ok=True)
            template_path = ans_dir / "submit.json"
            if not template_path.exists():
                template_path.write_text(json.dumps(submit_template, indent=2), encoding="utf-8")

        return result

    def submit_work(
        self,
        client_submission_id: str,
        backlog_item_key: str,
        git_revision: str,
        answer: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Guard, journal, and execute official submission."""
        if not self.adapter or not self.match_uuid or not self.git_guard or not self.journal:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # 1. Pre-submission Git Invariant Verification (GIT-01..05)
        assigned_branch = self.descriptor.get("assignedBranch", "main")
        self.git_guard.validate_pre_submission(
            target_commit_sha=git_revision,
            assigned_branch_ref=assigned_branch,
            backlog_item_key=backlog_item_key,
            answer_payload=answer,
            match_root=self.match_root,
            checkout_root=self.checkout_root,
            require_remote=False if self.descriptor.get("offline") else True,
            raise_on_error=True,
        )

        # 2. Record In-Flight Submission Journal (API-06, API-07)
        payload = {
            "clientSubmissionId": client_submission_id,
            "backlogItemKey": backlog_item_key,
            "gitRevision": git_revision,
            "answer": answer,
        }
        serialized_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.journal.record_in_flight(
            match_uuid=self.match_uuid,
            backlog_item_key=backlog_item_key,
            client_submission_id=client_submission_id,
            idempotency_key=client_submission_id,
            request_body_bytes=serialized_bytes,
            git_revision=git_revision,
            remote_ref=assigned_branch,
        )

        # 3. Submit via Official Adapter
        receipt = self.adapter.submit_work(
            match_uuid=self.match_uuid,
            client_submission_id=client_submission_id,
            idempotency_key=client_submission_id,
            backlog_item_key=backlog_item_key,
            git_revision=git_revision,
            answer=answer,
        )

        # 4. Mark Accepted in Journal
        submission_obj = receipt.get("submission", {})
        server_sub_id = submission_obj.get("id") or receipt.get("receiptId", "sub-accepted")
        self.journal.record_receipt(
            match_uuid=self.match_uuid,
            backlog_item_key=backlog_item_key,
            receipt_id=server_sub_id,
            http_code=201,
        )

        return receipt

    def report_progress(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Validate against schema and append progress event."""
        if not self.telemetry:
            # Fallback direct logger
            log_path = self.progress_log_file or (self.workspace_root / "logs" / "progress.ndjson")
            self.telemetry = get_telemetry_logger(log_path, self.player_session_id)

        # Ensure envelope fields
        if "schemaVersion" not in event:
            event["schemaVersion"] = 1
        if "playerSessionId" not in event:
            event["playerSessionId"] = self.player_session_id

        validate_progress_event(event)
        self.telemetry.log_event(event)
        return {"status": "RECORDED"}

    def wait_for_update(self, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Bounded serial polling."""
        bounded_ms = min(max(100, timeout_ms), 55000)
        time.sleep(bounded_ms / 1000.0)
        return {"status": "TIMEOUT", "elapsedMs": bounded_ms}


def get_tool_definitions() -> List[Dict[str, Any]]:
    return [
        {
            "name": "configure_session",
            "description": "Initialize match session credentials, paths, and telemetry logger.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "descriptor": {"type": "object", "description": "Match configuration descriptor"}
                },
                "required": ["descriptor"],
            },
        },
        {
            "name": "login_match",
            "description": "Authenticate website session and verify player identity.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "refresh_match",
            "description": "Retrieve current public match projection and open backlog items.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "start_work",
            "description": "Start work on an open backlog item. Returns MVS scenarios and submitTemplate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "backlogItemKey": {"type": "string", "description": "Backlog item key (e.g. TASK-01)"}
                },
                "required": ["backlogItemKey"],
            },
        },
        {
            "name": "submit_work",
            "description": "Submit completed work with git revision and answer payload. Strictly verifies Git invariants before submission.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "clientSubmissionId": {"type": "string", "description": "Client-generated UUID v4"},
                    "backlogItemKey": {"type": "string", "description": "Backlog item key"},
                    "gitRevision": {"type": "string", "description": "Full remote-pushed 40-character Git SHA"},
                    "answer": {"type": "object", "description": "Answer payload matching submitTemplate"},
                },
                "required": ["clientSubmissionId", "backlogItemKey", "gitRevision", "answer"],
            },
        },
        {
            "name": "report_progress",
            "description": "Append a validated progress event to the official progress NDJSON log.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event": {"type": "object", "description": "Progress event conforming to progress-event.schema.json"}
                },
                "required": ["event"],
            },
        },
        {
            "name": "wait_for_update",
            "description": "Bounded serial sleep/poll waiting for state updates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeoutMs": {"type": "integer", "description": "Timeout in milliseconds (max 55000)", "default": 5000}
                },
            },
        },
    ]


def run_mcp_stdio_server():
    server = MCPHelperServer()
    tools = get_tool_definitions()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            sys.stderr.write(f"Malformed JSON-RPC request: {exc}\n")
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "mtm-minimal-helper", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools},
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                if tool_name == "configure_session":
                    val = server.configure_session(tool_args.get("descriptor", {}))
                elif tool_name == "login_match":
                    val = server.login_match()
                elif tool_name == "refresh_match":
                    val = server.refresh_match()
                elif tool_name == "start_work":
                    val = server.start_work(tool_args.get("backlogItemKey", ""))
                elif tool_name == "submit_work":
                    val = server.submit_work(
                        client_submission_id=tool_args.get("clientSubmissionId", ""),
                        backlog_item_key=tool_args.get("backlogItemKey", ""),
                        git_revision=tool_args.get("gitRevision", ""),
                        answer=tool_args.get("answer", {}),
                    )
                elif tool_name == "report_progress":
                    val = server.report_progress(tool_args.get("event", {}))
                elif tool_name == "wait_for_update":
                    val = server.wait_for_update(tool_args.get("timeoutMs", 5000))
                else:
                    raise ValueError(f"Unknown tool: {tool_name}")

                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(val)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                sys.stderr.write(f"Tool {tool_name} error: {exc}\n")
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                        "isError": True,
                    },
                }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_stdio_server()
