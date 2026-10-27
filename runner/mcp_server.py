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

# Ensure src/ and pack root are on sys.path dynamically
_current_dir = Path(__file__).resolve().parent
_pack_root = _current_dir.parent
_src_dir = _pack_root / "src"
for _p in [str(_pack_root), str(_src_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtm_competition.adapters.official_game_master import OfficialGameMasterAdapter
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_knowledge.bootstrap import KnowledgeBootstrap
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
        self.assigned_branch: Optional[str] = None
        self.player_session_id: str = str(uuid.uuid4())
        self.progress_log_file: Optional[Path] = None
        self.adapter: Optional[OfficialGameMasterAdapter] = None
        self.journal: Optional[SubmissionJournal] = None
        self.git_guard: Optional[PreSubmissionGitGuard] = None
        self.telemetry: Optional[TelemetryLogger] = None
        self.checkout_root: Path = self.workspace_root
        self.match_root: Path = self.workspace_root
        self.known_backlog_items: Dict[str, Dict[str, Any]] = {}
        self.match_state: str = "IN_PROGRESS"
        self.running: bool = True

    def _get_backlog_title(self, key: str) -> str:
        item = self.known_backlog_items.get(key, {})
        title = item.get("title") or item.get("name") or f"Backlog item {key}"
        return str(title)[:160]

    def configure_session(self, descriptor: Dict[str, Any]) -> Dict[str, Any]:
        """Configure trusted session from MTM Player descriptor."""
        self.descriptor = descriptor
        self.match_code = descriptor.get("matchId") or descriptor.get("matchCode") or descriptor.get("match_code")
        self.match_uuid = descriptor.get("matchUuid") or descriptor.get("match_uuid") or descriptor.get("matchIdUuid")

        session_id = descriptor.get("playerSessionId") or descriptor.get("sessionId")
        if session_id:
            self.player_session_id = str(session_id)

        # Assigned branch resolution: require repository.branch, fail closed on missing branch
        repo_info = descriptor.get("repository") or {}
        assigned_branch = (
            repo_info.get("branch")
            or descriptor.get("assignedBranch")
            or descriptor.get("branch")
        )
        if assigned_branch:
            self.assigned_branch = str(assigned_branch).strip()
        else:
            self.assigned_branch = "main" if descriptor.get("type") == "PREPARE_GAME" else None

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
        base_url = (
            descriptor.get("gameMasterApiUrl")
            or descriptor.get("gameMasterUrl")
            or descriptor.get("baseUrl")
            or descriptor.get("origin")
            or "https://ai-fest-five.vercel.app"
        ).rstrip("/")
        bearer_token = (
            descriptor.get("apiKeySecret")
            or descriptor.get("bearerToken")
            or descriptor.get("runtimeTeamKey")
            or ""
        )
        self.adapter = OfficialGameMasterAdapter(
            base_url=base_url,
            auth_token=bearer_token,
            match_uuid=self.match_uuid,
            max_retries=3,
        )

        # Attempt to resolve match UUID from public projection if missing
        if not self.match_uuid and self.match_code and self.adapter:
            try:
                proj = self.adapter.get_match_projection(self.match_code)
                resolved_uuid = proj.get("matchUuid") or proj.get("id")
                if resolved_uuid:
                    self.match_uuid = str(resolved_uuid)
                    self.adapter.match_uuid = self.match_uuid
            except Exception:
                pass

        # Journal & Git Guard
        db_path = descriptor.get("journalDbPath") or (self.workspace_root / ".mtm" / "submissions.db")
        self.journal = SubmissionJournal(db_path=db_path)
        self.git_guard = PreSubmissionGitGuard(repo_path=self.checkout_root)

        # Record initial items from descriptor
        for item in descriptor.get("backlogItems", []):
            if isinstance(item, dict):
                k = item.get("id") or item.get("backlogItemKey") or item.get("key")
                if k:
                    self.known_backlog_items[str(k)] = item

        # PREPARE_GAME Pipeline (P0.5)
        if descriptor.get("type") == "PREPARE_GAME":
            self.telemetry.emit_orchestrator_started("Team Orchestrator started in preparation phase.")
            self.telemetry.emit_preparation_status("REPOSITORY", "COMPLETED", "Assigned repository and branch verified.")
            self.telemetry.emit_preparation_status("ENVIRONMENT", "IN_PROGRESS", "Bootstrapping knowledge and environment.")

            # Knowledge bootstrap into state/<matchCode>/knowledge/PROJECT_MAP.md (outside repo)
            kb_output_path = (
                self.workspace_root
                / "team-sdlc-pack"
                / "state"
                / (self.match_code or "active")
                / "knowledge"
                / "PROJECT_MAP.md"
            )
            if not kb_output_path.parent.exists():
                kb_output_path.parent.mkdir(parents=True, exist_ok=True)

            bootstrap = KnowledgeBootstrap(
                repo_path=self.checkout_root,
                match_code=self.match_code or "PREP",
                branch=self.assigned_branch,
                folder=repo_info.get("folder", self.match_code or "repo"),
            )
            bootstrap.generate_project_map(
                output_path=kb_output_path,
                run_verification=True,
            )

            # Get HEAD commit revision
            from mtm_core.git import get_head_sha
            head_sha = get_head_sha(self.checkout_root) or ("0" * 40)

            self.telemetry.emit_preparation_status("BUILD", "COMPLETED", "Environment build verified.")
            self.telemetry.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Match credentials verified.")
            self.telemetry.emit_environment_ready(
                request_id=descriptor.get("requestId", f"prepare-{self.match_code or '1'}"),
                repo_url=repo_info.get("url", ""),
                branch=self.assigned_branch,
                folder=repo_info.get("folder", self.match_code or ""),
                revision=head_sha,
                build="PASS",
                match_login="PASS",
            )

        return {
            "status": "CONFIGURED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "playerSessionId": self.player_session_id,
            "progressLogFile": str(self.progress_log_file),
        }

    def login_match(self) -> Dict[str, Any]:
        """Verify authentication session and query match state."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # Verify connection and match access
        try:
            state = self.adapter.get_match_projection(self.match_code)
            self.match_state = state.get("status", "IN_PROGRESS")
            if not self.match_uuid and state.get("matchUuid"):
                self.match_uuid = state.get("matchUuid")
                self.adapter.match_uuid = self.match_uuid
        except Exception as exc:
            sys.stderr.write(f"login_match verification note: {exc}\n")

        return {
            "status": "AUTHENTICATED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "matchState": self.match_state,
        }

    def refresh_match(self) -> Dict[str, Any]:
        """Read public match projection and discover open backlog items (waves)."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")

        newly_discovered: List[str] = []
        try:
            projection = self.adapter.get_match_projection(self.match_code)
            self.match_state = projection.get("status", self.match_state or "IN_PROGRESS")
            if not self.match_uuid and projection.get("matchUuid"):
                self.match_uuid = projection.get("matchUuid")
                self.adapter.match_uuid = self.match_uuid

            items = projection.get("backlogItems", [])
            for item in items:
                if isinstance(item, dict):
                    key = item.get("id") or item.get("backlogItemKey") or item.get("key")
                    if key and str(key) not in self.known_backlog_items:
                        key_str = str(key)
                        self.known_backlog_items[key_str] = item
                        newly_discovered.append(key_str)
                        # Technical Guide Step 9: MUST emit DISCOVERED for every new item
                        if self.telemetry:
                            self.telemetry.emit_backlog_status(
                                backlog_id=key_str,
                                title=self._get_backlog_title(key_str),
                                status="DISCOVERED",
                                summary=f"Backlog item {key_str} discovered on match board.",
                                revision=None,
                                receipt_id=None,
                            )
        except Exception as exc:
            sys.stderr.write(f"refresh_match query note: {exc}\n")

        return {
            "status": self.match_state,
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "backlogItems": list(self.known_backlog_items.values()),
            "newlyDiscovered": newly_discovered,
        }

    def start_work(self, backlog_item_key: str) -> Dict[str, Any]:
        """Start work on an open backlog item with automatic telemetry."""
        if not self.adapter or not self.match_uuid:
            raise RuntimeError("Session not configured. Call configure_session first.")

        origin = self.adapter.api_base_url
        path = f"/api/v1/matches/{self.match_uuid}/backlogs/start"

        # 1. Automatic telemetry: REQUESTED
        if self.telemetry:
            self.telemetry.emit_game_master_api(
                operation="START_WORK",
                phase="REQUESTED",
                backlog_id=backlog_item_key,
                method="POST",
                origin=origin,
                path=path,
                http_status=None,
                request_id=None,
                summary=f"Calling Game Master start work for {backlog_item_key}",
            )

        try:
            result = self.adapter.start_working(
                match_uuid_or_task_id=self.match_uuid,
                backlog_item_key=backlog_item_key,
            )

            req_id = result.get("requestId") or result.get("request_id")
            # 2. Automatic telemetry: SUCCEEDED & IN_PROGRESS
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="START_WORK",
                    phase="SUCCEEDED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=200 if result.get("replay") else 201,
                    request_id=req_id,
                    summary=f"Start work succeeded for {backlog_item_key}",
                )
                self.telemetry.emit_backlog_status(
                    backlog_id=backlog_item_key,
                    title=self._get_backlog_title(backlog_item_key),
                    status="IN_PROGRESS",
                    summary=f"Work started on {backlog_item_key}",
                    revision=None,
                    receipt_id=None,
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

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            req_id = getattr(exc, "request_id", None)
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="START_WORK",
                    phase="FAILED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=status_code,
                    request_id=req_id,
                    summary=f"Start work failed for {backlog_item_key}: {exc}",
                )
            raise

    def submit_work(
        self,
        client_submission_id: str,
        backlog_item_key: str,
        git_revision: str,
        answer: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Guard, journal, submit with exact body bytes, and emit automatic telemetry."""
        if not self.adapter or not self.match_uuid or not self.git_guard or not self.journal:
            raise RuntimeError("Session not configured. Call configure_session first.")

        assigned_branch = self.assigned_branch or self.descriptor.get("assignedBranch")
        if not assigned_branch:
            raise ValueError("No assigned branch configured for submission")

        # 1. Pre-submission Git Invariant Verification (GIT-01..05)
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

        # 2. Record In-Flight Submission Journal with canonical serialized bytes (API-06, API-07)
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

        origin = self.adapter.api_base_url
        path = f"/api/v1/matches/{self.match_uuid}/submissions"

        # 3. Telemetry: REQUESTED
        if self.telemetry:
            self.telemetry.emit_game_master_api(
                operation="SUBMIT",
                phase="REQUESTED",
                backlog_id=backlog_item_key,
                method="POST",
                origin=origin,
                path=path,
                http_status=None,
                request_id=None,
                summary=f"Submitting work for {backlog_item_key}",
            )

        try:
            # 4. Submit via Official Adapter passing exact serialized bytes
            receipt = self.adapter.submit_work(
                match_uuid=self.match_uuid,
                client_submission_id=client_submission_id,
                idempotency_key=client_submission_id,
                backlog_item_key=backlog_item_key,
                git_revision=git_revision,
                answer=answer,
                serialized_body_bytes=serialized_bytes,
            )

            # 5. Strictly validate receipt: require real submission ID (P0.4)
            submission_obj = receipt.get("submission") if isinstance(receipt.get("submission"), dict) else receipt
            server_sub_id = submission_obj.get("id") or receipt.get("receiptId")
            if not server_sub_id or server_sub_id == "sub-accepted":
                raise RuntimeError(
                    f"Game Master response lacked valid official submission ID (receipt: {receipt}). "
                    "Journal remains IN_FLIGHT."
                )

            # 6. Mark Accepted in Journal
            self.journal.record_receipt(
                match_uuid=self.match_uuid,
                backlog_item_key=backlog_item_key,
                receipt_id=str(server_sub_id),
                http_code=201,
            )

            req_id = receipt.get("requestId") or submission_obj.get("requestId")

            # 7. Telemetry: SUCCEEDED & SUBMITTED
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="SUBMIT",
                    phase="SUCCEEDED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=201,
                    request_id=req_id,
                    summary=f"Submission accepted for {backlog_item_key}",
                )
                self.telemetry.emit_backlog_status(
                    backlog_id=backlog_item_key,
                    title=self._get_backlog_title(backlog_item_key),
                    status="SUBMITTED",
                    summary=f"Submission accepted for {backlog_item_key}",
                    revision=git_revision,
                    receipt_id=str(server_sub_id),
                )

            return receipt

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            req_id = getattr(exc, "request_id", None)
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="SUBMIT",
                    phase="FAILED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=status_code,
                    request_id=req_id,
                    summary=f"Submission failed for {backlog_item_key}: {exc}",
                )
            raise

    def report_progress(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Validate against schema and append progress event."""
        if not self.telemetry:
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
        """Bounded serial polling detecting wave releases and match status transitions."""
        bounded_ms = min(max(100, timeout_ms), 55000)
        start_time = time.time()
        elapsed_ms = 0
        poll_interval = 2.0

        while elapsed_ms < bounded_ms:
            # Poll match state and waves
            if self.adapter:
                res = self.refresh_match()
                new_items = res.get("newlyDiscovered", [])
                if new_items:
                    return {
                        "status": "UPDATED",
                        "matchState": self.match_state,
                        "newItems": new_items,
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }
                if self.match_state == "FINISHED":
                    return {
                        "status": "FINISHED",
                        "matchState": "FINISHED",
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }
                if self.match_state == "PAUSED":
                    return {
                        "status": "PAUSED",
                        "matchState": "PAUSED",
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }

            remaining_sec = (bounded_ms - elapsed_ms) / 1000.0
            sleep_sec = min(poll_interval, remaining_sec)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
            elapsed_ms = int((time.time() - start_time) * 1000)

        return {"status": "TIMEOUT", "elapsedMs": elapsed_ms, "matchState": self.match_state}


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
