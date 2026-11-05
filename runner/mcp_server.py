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

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src/ and pack root are on sys.path dynamically
_current_dir = Path(__file__).resolve().parent
_pack_root = _current_dir.parent
_src_dir = _pack_root / "src"
for _p in [str(_pack_root), str(_src_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtm.constants import DEFAULT_GAME_MASTER_ORIGIN
from mtm_competition.adapters.base import validate_match_projection_schema
from mtm_competition.adapters.company_tools import CompanyToolsClient, categorize_tool
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    build_match_page_url,
)
from mtm_competition.integration.git_guard import PreSubmissionGitGuard, SubmissionGateError
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.scheduling.native import NativeTaskCoordinator
from mtm_knowledge.bootstrap import KnowledgeBootstrap, detect_tech_stack
from runner.telemetry import (
    TelemetryLogger,
    get_telemetry_logger,
    validate_progress_event,
)


_SESSION_MEMORY: Dict[tuple[str, str], Dict[str, Any]] = {}


def _lane_settings_from_env() -> Dict[str, Any]:
    """Lane mode (reserved PI/Heavy slots) is enabled only when MTM_NATIVE_RESERVED_WORKERS > 0."""
    def _int(name: str) -> Optional[int]:
        raw = os.environ.get(name, "").strip()
        try:
            return int(raw) if raw else None
        except ValueError:
            return None
    reserved = _int("MTM_NATIVE_RESERVED_WORKERS")
    settings: Dict[str, Any] = {}
    if reserved and reserved > 0:
        settings["reserved_workers"] = reserved
        settings["allow_reserved_borrow"] = False  # V2: PI reserve is never borrowable (env ignored)
    # V2 feature flags (Plan section 15): both must be on; default off keeps V1 ordering.
    if os.environ.get("MTM_ORCH_V2_ENABLED", "0") == "1" and os.environ.get("MTM_V2_ADAPTIVE_SCHEDULER", "0") == "1":
        settings["adaptive"] = True
    if os.environ.get("MTM_ORCH_V2_ENABLED", "0") == "1" and os.environ.get("MTM_V2_DEADLINE_CONTROL", "0") == "1":
        settings["deadline_control"] = True
    if os.environ.get("MTM_ORCH_V2_ENABLED", "0") == "1" and os.environ.get("MTM_V2_MODEL_ROUTING", "0") == "1":
        settings["model_routing"] = True
    keys = [k for k in os.environ.get("MTM_HEAVY_KEYS", "").split(",") if k.strip()]
    if keys:
        settings["heavy_keys"] = keys
    raw_diff = os.environ.get("MTM_HEAVY_MIN_DIFFICULTY", "").strip()
    if raw_diff:
        try:
            settings["heavy_min_difficulty"] = float(raw_diff)
        except ValueError:
            pass
    heavy_max = _int("MTM_HEAVY_MAX_SECONDS")
    if heavy_max and heavy_max > 0:
        settings["heavy_max_seconds"] = heavy_max
    return settings


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 1e12 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def normalize_native_projection(projection: Dict[str, Any], adapter: Any) -> Dict[str, Any]:
    """Translate public board aliases without assuming duration or reveal schedules."""
    match = projection.get("match") or {}
    status = projection.get("status") or match.get("status") or "UNKNOWN"
    server = next((_epoch(projection[k]) for k in ("server_epoch", "serverNow", "serverTime") if projection.get(k) is not None), None)
    if server is None and getattr(adapter, "server_epoch", None) is not None:
        server = adapter.server_epoch + max(0, time.monotonic() - adapter.server_clock_monotonic)
    remaining = next((source[k] for source in (projection, match) for k in ("remaining_seconds", "remainingSec", "remainingSeconds") if source.get(k) is not None), None)
    elapsed = next((source[k] for source in (projection, match) for k in ("elapsedSec", "elapsedSeconds", "elapsed_seconds") if source.get(k) is not None), None)
    items = []
    for item in projection.get("backlogItems", projection.get("backlog", [])):
        key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
        if not key:
            continue
        deadline = next((_epoch(item[k]) for k in ("deadline_epoch", "deadlineEpoch", "deadlineAt", "closesAt", "closeAt", "dueAt") if item.get(k) is not None), None)
        # Explicit server countdown is pause-adjusted and preferable to wall-clock dates.
        left = next((item[k] for k in ("remainingSec", "remainingSeconds", "deadlineRemainingSec") if item.get(k) is not None), None)
        if left is not None and server is not None:
            deadline = server + max(0, float(left))
        # Official reveal/deadline counters exclude PAUSED time; a host wall-clock
        # reveal timestamp does not. Public board uses these elapsed counters.
        if (server is not None and elapsed is not None
                and item.get("deadlineMinutes") is not None
                and item.get("revealedElapsedSec") is not None):
            deadline = server + float(item["deadlineMinutes"]) * 60 - (float(elapsed) - float(item["revealedElapsedSec"]))
        items.append({**item, "key": str(key), "type": item.get("type") or item.get("taskType") or str(key).split("-")[0],
                      "status": item.get("status") or item.get("state") or "VISIBLE", "deadline_epoch": deadline,
                      "description": item.get("description") or item.get("descriptionMd") or ""})
    return {"status": status, "server_epoch": server, "remaining_seconds": remaining, "items": items}


def announced_test_ids(details: Dict[str, Any]) -> List[str]:
    """Inventory explicit public scenario IDs, independent of a worker's answer.

    Structured rows (``id`` / ``testCaseId`` / ``scenarioId``) always count. In free text, a fully
    qualified id (``BUG-01-TC-01``) counts anywhere, while a bare id (``TC-01``) counts only when it is
    the declared subject of a line (heading, bullet or table row). A bare id mentioned mid-sentence
    ("same conditions as TC-01") is a cross-reference to a qualified row, not a new requirement.
    """
    ids: set[str] = set()
    qualified = re.compile(r"\b(?:[A-Z0-9]+-)+(?:TC|MVS|SCENARIO|TEST)-\d+(?:-[A-Za-z0-9]+)*\b")
    declared_bare = re.compile(r"^[ \t]*(?:#{1,6}[ \t]*|[|][ \t`]*|[-*+][ \t`]*)?\**`?((?:TC|MVS|SCENARIO|TEST)-\d+(?:-[A-Za-z0-9]+)*)\b",
                               re.MULTILINE)

    def walk(value: Any, rows: bool = False) -> None:
        if isinstance(value, list):
            for row in value:
                walk(row, True)
        elif isinstance(value, dict):
            if rows:
                identifier = value.get("id") or value.get("testCaseId") or value.get("scenarioId")
                if isinstance(identifier, str) and identifier.strip():
                    ids.add(identifier.strip())
            for name in ("testResults", "scenarios", "testCases", "cases", "tests", "minimumVerificationScenarios", "mvs"):
                if name in value:
                    walk(value[name], True)
        elif isinstance(value, str):
            ids.update(m.group(0) for m in qualified.finditer(value))
            ids.update(declared_bare.findall(value))
    walk(details.get("submitTemplate", {}))
    walk(details.get("minimumVerificationScenarios", details.get("mvs")), True)
    # A bare alias of a qualified id that is also present is the same test (BUG-01-TC-01 vs TC-01).
    return sorted(i for i in ids if not any(o != i and o.endswith("-" + i) for o in ids))


def resolve_stored_session_credentials(workspace_root: Path, match_code: Optional[str]) -> Dict[str, Any]:
    """Retain credentials only in this MCP process, scoped to the exact match/workspace.

    Recovery receives a fresh trusted Player descriptor; never scan host private
    directories or write competition credentials to disk.
    """
    return dict(_SESSION_MEMORY.get((str(workspace_root.resolve()), str(match_code)), {}))


def save_stored_session_credentials(workspace_root: Path, match_code: str, data: Dict[str, Any]) -> None:
    """Compatibility name for memory-only retention; nothing is persisted."""
    if match_code:
        _SESSION_MEMORY[(str(workspace_root.resolve()), str(match_code))] = dict(data)


class MCPHelperServer:
    def __init__(self, workspace_root: Optional[Path | str] = None):
        raw_root = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        if raw_root.name.lower() == "team-sdlc-pack":
            raw_root = raw_root.parent
        self.workspace_root = raw_root
        self.descriptor: Dict[str, Any] = {}
        self.match_uuid: Optional[str] = None
        self.match_code: Optional[str] = None
        self.assigned_branch: Optional[str] = None
        self.player_session_id: str = str(uuid.uuid4())
        self.progress_log_file: Optional[Path] = None
        self.bearer_token: str = ""
        self.api_url: str = ""
        self.adapter: Optional[OfficialGameMasterAdapter] = None
        self.session_cookies: Dict[str, str] = {}
        self.journal: Optional[SubmissionJournal] = None
        self.git_guard: Optional[PreSubmissionGitGuard] = None
        self.telemetry: Optional[TelemetryLogger] = None
        self.checkout_root: Path = self.workspace_root
        self.match_root: Path = self.workspace_root
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.match_page_url: Optional[str] = None
        self.allowed_match_origins: Optional[List[str]] = None
        self.repo_descriptor: Dict[str, Any] = {}
        self.current_wave: int = 1
        self.known_backlog_items: Dict[str, Dict[str, Any]] = {}
        self.match_state: str = "IN_PROGRESS"
        self.running: bool = True
        self.native: Optional[NativeTaskCoordinator] = None
        self.latest_projection: Dict[str, Any] = {}
        self.project_runtime = None
        self.company_tools_client: Optional[CompanyToolsClient] = None
        self.company_tools: List[Dict[str, Any]] = []

    def _init_company_tools(self) -> None:
        """Initialize CompanyToolsClient and discover tools from Match MCP if credentials are present."""
        match_id = self.match_code or self.match_uuid
        token = self.bearer_token or self.descriptor.get("apiKeySecret") or self.descriptor.get("bearerToken")
        if not match_id or not token:
            return

        base_url = (
            self.match_page_url
            or self.descriptor.get("gameMasterApiUrl")
            or self.descriptor.get("baseUrl")
            or "https://masterthemachine.tech"
        )
        state_dir = self.workspace_root / "team-sdlc-pack" / "state" / str(match_id)

        try:
            if not self.company_tools_client:
                self.company_tools_client = CompanyToolsClient(
                    base_url=base_url,
                    match_id=str(match_id),
                    api_key=str(token),
                    timeout=15.0,
                    state_dir=state_dir,
                )
            self.company_tools = self.company_tools_client.discover_tools()
        except Exception as exc:
            sys.stderr.write(f"Warning: company tools discovery: {exc}\n")

    def list_company_tools(self) -> List[Dict[str, Any]]:
        """List discovered tools from the official Match MCP server."""
        if not self.company_tools_client:
            self._init_company_tools()
        if self.company_tools_client:
            try:
                self.company_tools = self.company_tools_client.discover_tools()
            except Exception as exc:
                sys.stderr.write(f"Warning: refreshing company tools: {exc}\n")
        return self.company_tools

    def call_company_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Invoke an authoritative company tool via Match MCP server."""
        if not self.company_tools_client:
            self._init_company_tools()
        if not self.company_tools_client:
            raise RuntimeError("Company tools bridge not initialized (missing match credentials or match ID).")
        return self.company_tools_client.call_tool(name, arguments)

    def fetch_fixture(
        self,
        backlog_item_key: str,
        tool_name: Optional[str] = None,
        arguments: Optional[Dict[str, Any]] = None,
        write_to_disk: bool = True,
    ) -> Dict[str, Any]:
        """
        Fetch authoritative QA test data fixture from Match MCP and save with source metadata.
        NEVER generates synthetic fixtures!
        """
        if not self.company_tools_client:
            self._init_company_tools()
        if not self.company_tools_client:
            return {"status": "UNAVAILABLE", "reason": "No company tools client connection"}

        tools = self.list_company_tools()
        target_tool = tool_name
        if not target_tool:
            qa_tools = [t for t in tools if t.get("category") == "qa_data"]
            if qa_tools:
                target_tool = qa_tools[0]["name"]
            else:
                any_qa = [
                    t for t in tools
                    if any(k in t.get("name", "").lower() for k in ["qa", "fixture", "store", "test_data"])
                ]
                if any_qa:
                    target_tool = any_qa[0]["name"]

        if not target_tool:
            return {"status": "UNAVAILABLE", "reason": "No QA test data tool found in company tools list"}

        tool_meta = next((t for t in tools if isinstance(t, dict) and t.get("name") == target_tool), {})
        schema_props = ((tool_meta.get("inputSchema") or {}).get("properties") or {}) if isinstance(tool_meta, dict) else {}

        candidate_args: List[Dict[str, Any]] = []
        if arguments:
            candidate_args.append(dict(arguments))
        else:
            if "backlogItemKey" in schema_props and "item_id" not in schema_props:
                candidate_args.append({"backlogItemKey": backlog_item_key})
            elif "item_id" in schema_props and "backlogItemKey" not in schema_props:
                candidate_args.append({"item_id": backlog_item_key})
            elif "key" in schema_props:
                candidate_args.append({"key": backlog_item_key})
            candidate_args.extend([
                {"backlogItemKey": backlog_item_key},
                {"backlogItemKey": backlog_item_key, "item_id": backlog_item_key},
                {"item_id": backlog_item_key},
                {"key": backlog_item_key},
            ])

        raw_data = None
        used_args: Dict[str, Any] = candidate_args[0] if candidate_args else {"backlogItemKey": backlog_item_key}
        last_err: Optional[Exception] = None
        seen_arg_keys = set()
        for cand in candidate_args:
            sig = json.dumps(cand, sort_keys=True)
            if sig in seen_arg_keys:
                continue
            seen_arg_keys.add(sig)
            try:
                res_data = self.company_tools_client.call_tool(target_tool, cand)
                if res_data:
                    raw_data = res_data
                    used_args = cand
                    break
            except Exception as exc:
                last_err = exc
                continue

        if raw_data is None and last_err is not None:
            return {"status": "ERROR", "reason": f"Tool '{target_tool}' call failed: {last_err}"}

        if not raw_data:
            return {"status": "UNAVAILABLE", "reason": f"Tool '{target_tool}' returned empty payload"}

        fixture_filename = f"{backlog_item_key.lower()}-test-data.json"
        rel_fixture_path = f"answers/{backlog_item_key}/evidence/{fixture_filename}"
        rel_sidecar_path = f"answers/{backlog_item_key}/evidence/{fixture_filename}.source.json"

        raw_json_str = json.dumps(raw_data, indent=2, ensure_ascii=False) if isinstance(raw_data, (dict, list)) else str(raw_data)
        if "{{" in raw_json_str:
            return {"status": "INVALID", "reason": "Fetched fixture contains unrendered template placeholders '{{'"}

        file_sha = hashlib.sha256(raw_json_str.encode("utf-8")).hexdigest()
        sidecar_data = {
            "tool": target_tool,
            "arguments": used_args,
            "sha256": file_sha,
            "fetchedAt": datetime.now(timezone.utc).isoformat(),
            "backlogItemKey": backlog_item_key,
            "official": True,
        }
        sidecar_json_str = json.dumps(sidecar_data, indent=2)

        if write_to_disk:
            evidence_dir = Path(self.match_root) / "answers" / backlog_item_key / "evidence"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            fixture_path = evidence_dir / fixture_filename
            sidecar_path = evidence_dir / f"{fixture_filename}.source.json"
            fixture_path.write_text(raw_json_str, encoding="utf-8")
            sidecar_path.write_text(sidecar_json_str, encoding="utf-8")

        return {
            "status": "FETCHED",
            "path": rel_fixture_path,
            "sidecar": rel_sidecar_path,
            "sha256": file_sha,
            "tool": target_tool,
            "content": raw_json_str,
            "sidecar_content": sidecar_json_str,
        }

    def prefetch_company_context(self, backlog_item_key: str, description_text: str = "") -> Dict[str, Any]:
        """Auto-prefetch relevant KB standards and production logs from Match MCP for a task."""
        context: Dict[str, Any] = {"kb": [], "logs": []}
        if not self.company_tools_client:
            self._init_company_tools()
        if not self.company_tools_client:
            return context

        tools = self.list_company_tools()
        kb_tools = [t for t in tools if isinstance(t, dict) and t.get("category") == "kb"]
        log_tools = [t for t in tools if isinstance(t, dict) and t.get("category") == "logs"]

        if kb_tools:
            kb_tool_name = kb_tools[0]["name"]
            kb_props = ((kb_tools[0].get("inputSchema") or {}).get("properties") or {})
            queries: List[str] = []
            for kb_id in re.findall(r"\bKB-\d+\b", description_text or "", flags=re.IGNORECASE):
                q = kb_id.upper()
                if q not in queries:
                    queries.append(q)
            desc_lower = f"{backlog_item_key} {description_text}".lower()
            for kw in ("currency", "invoice", "csv", "import", "aging", "contact", "overdue", "heatmap", "export", "position"):
                if kw in desc_lower and kw not in queries:
                    queries.append(kw)
            if not queries:
                queries.append(backlog_item_key)

            param_key = "query"
            for candidate_key in ("query", "keyword", "search", "id", "topic", "term"):
                if candidate_key in kb_props:
                    param_key = candidate_key
                    break

            for q in queries[:3]:
                try:
                    res = self.company_tools_client.call_tool(kb_tool_name, {param_key: q})
                    if res:
                        context["kb"].append({"query": q, "tool": kb_tool_name, "result": res})
                except Exception:
                    continue

        if log_tools and (backlog_item_key.upper().startswith("PI-") or "incident" in (description_text or "").lower()):
            log_tool_name = log_tools[0]["name"]
            for log_args in ({"backlogItemKey": backlog_item_key}, {"query": backlog_item_key}, {}):
                try:
                    res = self.company_tools_client.call_tool(log_tool_name, log_args)
                    if res:
                        context["logs"].append({"tool": log_tool_name, "arguments": log_args, "result": res})
                        break
                except Exception:
                    continue

        return context

    def _get_backlog_title(self, key: str) -> str:
        item = self.known_backlog_items.get(key, {})
        title = item.get("title") or item.get("name") or f"Backlog item {key}"
        return str(title)[:160]

    def configure_session(self, descriptor: Dict[str, Any]) -> Dict[str, Any]:
        """Configure trusted session from MTM Player descriptor."""
        if os.environ.get("MTM_ENV") == "production":
            from mtm_core.config.loader import verify_skill_checksums
            verify_skill_checksums(_pack_root)
        incoming = descriptor.get("matchId") or descriptor.get("matchCode") or descriptor.get("match_code")
        if self.match_code and incoming and self.match_code != str(incoming).strip():
            raise ValueError("A new match requires a fresh MCP session; do not reuse credentials or leases")
        merged = dict(self.descriptor)
        merged.update(descriptor)
        self.descriptor = merged

        new_code = descriptor.get("matchId") or descriptor.get("matchCode") or descriptor.get("match_code")
        if new_code:
            self.match_code = str(new_code).strip()

        new_uuid = descriptor.get("matchUuid") or descriptor.get("match_uuid") or descriptor.get("matchIdUuid")
        if new_uuid:
            self.match_uuid = str(new_uuid).strip()

        session_id = descriptor.get("playerSessionId") or descriptor.get("sessionId")
        if session_id:
            self.player_session_id = str(session_id)

        # Store session credentials and match page URL from descriptor (retaining prior if omitted)
        if descriptor.get("username") or descriptor.get("teamLoginAccount"):
            self.username = descriptor.get("username") or descriptor.get("teamLoginAccount")
        if descriptor.get("password") or descriptor.get("teamPassword"):
            self.password = descriptor.get("password") or descriptor.get("teamPassword")
        if descriptor.get("matchPageUrl") or descriptor.get("match_page_url"):
            self.match_page_url = descriptor.get("matchPageUrl") or descriptor.get("match_page_url")
        if descriptor.get("allowedMatchOrigins") or descriptor.get("allowed_origins") or descriptor.get("allowedOrigins"):
            self.allowed_match_origins = descriptor.get("allowedMatchOrigins") or descriptor.get("allowed_origins") or descriptor.get("allowedOrigins")

        # Fallback to stored session credentials if username/password/cookies/matchUuid are missing
        if not (self.username and self.password) or not self.match_uuid or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                if not self.bearer_token and stored.get("apiKeySecret") and descriptor.get("type") != "PREPARE_GAME":
                    self.bearer_token = stored["apiKeySecret"]

        # Repository descriptor: retain from prior PREPARE_GAME if START_GAME omits repository
        if descriptor.get("repository"):
            self.repo_descriptor = descriptor["repository"]
        repo_info = self.repo_descriptor

        # Assigned branch resolution: require repository.branch, retain verified branch if omitted
        assigned_branch = (
            repo_info.get("branch")
            or descriptor.get("assignedBranch")
            or descriptor.get("branch")
        )
        if assigned_branch:
            self.assigned_branch = str(assigned_branch).strip()
        elif not self.assigned_branch:
            self.assigned_branch = "main" if descriptor.get("type") == "PREPARE_GAME" else None

        # Workspace root from descriptor: update from teamWorkspace or teamSdlcPackWorkspace
        team_ws_str = descriptor.get("teamWorkspace") or descriptor.get("team_workspace")
        if team_ws_str:
            ws_path = Path(team_ws_str).resolve()
            if ws_path.name.lower() == "team-sdlc-pack":
                ws_path = ws_path.parent
            self.workspace_root = ws_path
        elif descriptor.get("teamSdlcPackWorkspace"):
            pack_ws = Path(descriptor["teamSdlcPackWorkspace"]).resolve()
            if pack_ws.name.lower() == "team-sdlc-pack":
                self.workspace_root = pack_ws.parent

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
        elif not self.progress_log_file:
            log_dir = self.workspace_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.progress_log_file = log_dir / f"{self.match_code or 'active'}.progress.ndjson"

        # Initialize telemetry
        self.telemetry = get_telemetry_logger(
            log_path=self.progress_log_file,
            player_session_id=self.player_session_id,
        )

        # Adapter & Bearer Token Management
        is_prepare = (descriptor.get("type") == "PREPARE_GAME")
        new_token = (
            descriptor.get("apiKeySecret")
            or descriptor.get("bearerToken")
            or descriptor.get("runtimeTeamKey")
            or ""
        )
        if not is_prepare and new_token:
            self.bearer_token = new_token
        elif is_prepare and not self.bearer_token:
            self.bearer_token = ""

        base_from_page = ""
        if self.match_page_url:
            parsed_u = urllib.parse.urlparse(self.match_page_url)
            if parsed_u.scheme and parsed_u.netloc:
                base_from_page = f"{parsed_u.scheme}://{parsed_u.netloc}"

        new_api_url = (
            descriptor.get("gameMasterApiUrl")
            or descriptor.get("gameMasterUrl")
            or descriptor.get("baseUrl")
            or base_from_page
            or descriptor.get("origin")
            or ""
        ).rstrip("/")
        if new_api_url:
            self.api_url = new_api_url
        elif not self.api_url:
            self.api_url = DEFAULT_GAME_MASTER_ORIGIN

        # Update existing adapter instance WITHOUT replacing it (preserving cookies)
        if self.adapter is not None:
            if self.bearer_token:
                self.adapter.auth_token = self.bearer_token
                self.adapter.api_key = self.bearer_token
            if self.api_url:
                self.adapter.base_url = self.api_url
                self.adapter.api_base_url = self.api_url
            if self.match_uuid:
                self.adapter.match_uuid = self.match_uuid
        else:
            self.adapter = OfficialGameMasterAdapter(
                base_url=self.api_url,
                auth_token=self.bearer_token,
                match_uuid=self.match_uuid,
                max_retries=1,
            )
            if self.session_cookies:
                self.adapter.session_cookies.update(self.session_cookies)

        # Auto-login to establish session cookies and resolve match UUID if credentials exist
        if (not self.session_cookies or not (self.adapter and self.adapter.session_cookies)) and self.username and self.password and self.adapter:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: auto-login in configure_session: {e}\n")

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
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.journal is None or str(getattr(self.journal, "db_path", "")) != str(db_path):
            if self.journal is not None:
                try:
                    self.journal.close()
                except Exception:
                    pass
            self.journal = SubmissionJournal(db_path=db_path)
        self.git_guard = PreSubmissionGitGuard(repo_path=self.checkout_root)

        if descriptor.get("type") in ("START_GAME", "RESUME_GAME") and (
            descriptor.get("nativeScheduling") or os.environ.get("MTM_NATIVE_COORDINATION") == "1"
        ):
            folder = self.repo_descriptor.get("folder") or self.match_root.relative_to(self.checkout_root).as_posix()
            self.native = NativeTaskCoordinator(
                state_dir=self.workspace_root / "team-sdlc-pack" / "state" / str(self.match_code) / "native",
                checkout_root=self.checkout_root, match_root=self.match_root,
                repository_folder=folder, match_id=str(self.match_code),
                max_workers=int(os.environ.get("MTM_NATIVE_MAX_WORKERS", "4")),
                normal_workers=int(os.environ.get("MTM_NATIVE_NORMAL_WORKERS", "3")),
                **_lane_settings_from_env(),
            )
            self._reconcile_native_receipts()
            self._start_deadline_watchdog()

        # Record initial items from descriptor
        for item in descriptor.get("backlogItems", []):
            if isinstance(item, dict):
                k = item.get("id") or item.get("backlogItemKey") or item.get("key")
                if k:
                    self.known_backlog_items[str(k)] = item

        # PREPARE_GAME Pipeline (R1 & R5)
        if descriptor.get("type") == "PREPARE_GAME":
            is_offline = bool(descriptor.get("offline"))
            # Player already owns ORCHESTRATOR_STARTED. Retain compatibility for
            # standalone tests without duplicating an existing session milestone.
            existing_started = False
            if self.progress_log_file and self.progress_log_file.exists():
                for line in self.progress_log_file.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                        if event.get("type") == "ORCHESTRATOR_STARTED" and event.get("playerSessionId") == self.player_session_id:
                            existing_started = True
                            break
                    except ValueError:
                        continue
            if not existing_started:
                self.telemetry.emit_orchestrator_started("Team Orchestrator started in preparation phase.")

            # 1. Genuine Git Invariants Verification (R2)
            try:
                from mtm_core.git import verify_assigned_repository
                repo_res = verify_assigned_repository(self.checkout_root, repo_info or descriptor)
                head_sha = repo_res.get("head_sha", "0" * 40)
                self.telemetry.emit_preparation_status("GIT_AUTH", "COMPLETED", "Assigned repository access and origin verified.")
                self.telemetry.emit_preparation_status("REPOSITORY", "COMPLETED", "Assigned repository and branch verified.")
            except Exception as exc:
                self.telemetry.emit_preparation_status("REPOSITORY", "FAILED", f"Git repository verification failed: {exc}")
                raise RuntimeError(f"PREPARE_GAME failed: Git verification failed: {exc}") from exc

            self.telemetry.emit_preparation_status("ENVIRONMENT", "IN_PROGRESS", "Bootstrapping knowledge and verifying environment doctor.")

            # 2. Knowledge bootstrap into state/<matchCode>/knowledge/PROJECT_MAP.md (outside repo)
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

            # Resolve 3-tier layout: checkoutRoot -> matchRoot -> appRoot (R5)
            app_root = (self.match_root / "src") if (self.match_root / "src" / "package.json").exists() else self.match_root
            from runner.project_runtime import ProjectRuntime
            self.project_runtime = ProjectRuntime(self.match_root, str(self.match_code or "prepare"))
            project_env = self.project_runtime.env

            bootstrap = KnowledgeBootstrap(
                repo_path=self.checkout_root,
                match_code=self.match_code or "PREP",
                branch=self.assigned_branch,
                folder=repo_info.get("folder", self.match_code or "repo"),
                checkout_root=self.checkout_root,
                match_root=self.match_root,
                app_root=app_root,
            )

            # 3. Locate and execute genuine environment doctor or build command (R6)
            tech = detect_tech_stack(app_root, match_root=self.match_root)
            doc_file = None
            if sys.platform.startswith("win"):
                doctor_candidates = [
                    self.match_root / "env-require" / "doctor.ps1",
                    self.match_root / "doctor.ps1",
                    self.checkout_root / "env-require" / "doctor.ps1",
                    self.checkout_root / "doctor.ps1",
                    app_root / "env-require" / "doctor.ps1",
                    app_root / "doctor.ps1",
                    self.match_root / "env-require" / "doctor.sh",
                    self.match_root / "doctor.sh",
                    self.checkout_root / "env-require" / "doctor.sh",
                    self.checkout_root / "doctor.sh",
                    app_root / "env-require" / "doctor.sh",
                    app_root / "doctor.sh",
                ]
            else:
                doctor_candidates = [
                    self.match_root / "env-require" / "doctor.sh",
                    self.match_root / "doctor.sh",
                    self.checkout_root / "env-require" / "doctor.sh",
                    self.checkout_root / "doctor.sh",
                    app_root / "env-require" / "doctor.sh",
                    app_root / "doctor.sh",
                    self.match_root / "env-require" / "doctor.ps1",
                    self.match_root / "doctor.ps1",
                    self.checkout_root / "env-require" / "doctor.ps1",
                    self.checkout_root / "doctor.ps1",
                    app_root / "env-require" / "doctor.ps1",
                    app_root / "doctor.ps1",
                ]

            for cand in doctor_candidates:
                if cand.exists():
                    doc_file = cand
                    break

            build_pass = False
            build_exit_code = 1
            fail_reason = ""

            if doc_file:
                cwd_run = doc_file.parent.parent if doc_file.parent.name == "env-require" else doc_file.parent
                if not cwd_run.resolve().is_relative_to(self.match_root):
                    cwd_run = self.match_root
                if doc_file.suffix == ".ps1":
                    if sys.platform.startswith("win"):
                        cmd_run = ["powershell.exe", "-NoProfile", "-File", str(doc_file)]
                    else:
                        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
                        if pwsh_bin:
                            cmd_run = [pwsh_bin, "-NoProfile", "-File", str(doc_file)]
                        else:
                            sh_companion = doc_file.with_suffix(".sh")
                            if sh_companion.exists():
                                doc_file = sh_companion
                                cmd_run = ["bash", str(doc_file)]
                            else:
                                raise RuntimeError("PowerShell doctor requires pwsh or a documented shell companion; never run .ps1 with bash")
                else:
                    cmd_run = ["bash" if not sys.platform.startswith("win") else "sh", str(doc_file)]
                try:
                    res = self.project_runtime.run(cmd_run, cwd=cwd_run, timeout=120)
                    build_exit_code = res.returncode
                    doc_output = (res.stdout or "") + "\n" + (res.stderr or "")
                    has_ready = "Environment status: READY" in doc_output
                    if res.returncode == 0 and has_ready:
                        build_pass = True
                    else:
                        fail_reason = (
                            f"Doctor script exited with code {res.returncode}; "
                            f"'Environment status: READY' in output: {has_ready}"
                        )
                except Exception as exc:
                    build_pass = False
                    build_exit_code = 1
                    fail_reason = f"Execution error running doctor script: {exc}"
            else:
                v_cmd = tech.get("Build command") or tech.get("Typecheck command")
                if v_cmd and not v_cmd.startswith("default "):
                    try:
                        res = subprocess.run(
                            v_cmd,
                            cwd=str(app_root),
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=60,
                            check=False,
                            env=project_env,
                        )
                        build_exit_code = res.returncode
                        if res.returncode == 0:
                            build_pass = True
                        else:
                            fail_reason = f"Build command '{v_cmd}' exited with code {res.returncode}"
                    except Exception as exc:
                        build_pass = False
                        build_exit_code = 1
                        fail_reason = f"Execution error running build command: {exc}"
                else:
                    build_pass = False
                    build_exit_code = 1
                    fail_reason = "No doctor script (doctor.ps1/doctor.sh) or valid build command found in repository"

            # Record verified status in PROJECT_MAP.md
            if build_pass and doc_file and self.project_runtime.build_command():
                actual_build = self.project_runtime.run(self.project_runtime.build_command(), cwd=app_root, timeout=300)
                build_pass = actual_build.ok
                build_exit_code = actual_build.returncode
                if not build_pass:
                    fail_reason = f"Declared app build failed with exit {build_exit_code}: {actual_build.stderr[-500:]}"
            bootstrap.generate_project_map(
                output_path=kb_output_path,
                verification_result={"passed": build_pass, "exit_code": build_exit_code},
                run_verification=False,
            )

            if not build_pass:
                self.telemetry.emit_preparation_status("BUILD", "FAILED", f"Environment build verification failed: {fail_reason}")
                raise RuntimeError(f"PREPARE_GAME failed: Environment verification failed: {fail_reason}")

            self.telemetry.emit_preparation_status("BUILD", "COMPLETED", "Environment build verified.")
            self.telemetry.emit_preparation_status("ENVIRONMENT", "COMPLETED", "Project toolchain and doctor checks verified.")

            # 4. Genuine Session Match Authentication (R1)
            username = self.username or descriptor.get("username")
            password = self.password or descriptor.get("password")
            match_page_url = self.match_page_url or descriptor.get("matchPageUrl")
            allowed_origins = self.allowed_match_origins or descriptor.get("allowedMatchOrigins") or descriptor.get("allowedOrigins")

            if is_offline:
                self.telemetry.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Match credentials verified (offline test mode).")
            else:
                if not username or not password or not match_page_url or not self.adapter:
                    missing = [f for f, v in [("username", username), ("password", password), ("matchPageUrl", match_page_url)] if not v]
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Missing required PREPARE_GAME fields: {', '.join(missing)}")
                    raise RuntimeError(f"PREPARE_GAME failed: Missing required authentication fields: {', '.join(missing)}")

                try:
                    target_match_url = build_match_page_url(match_page_url, self.match_code) if self.match_code else match_page_url
                    auth_res = self.adapter.login_session(
                        username=username,
                        password=password,
                        match_page_url=target_match_url,
                        allowed_origins=allowed_origins,
                        match_id=self.match_code,
                    )
                    if auth_res.get("status") != "AUTHENTICATED":
                        raise RuntimeError(f"login_session returned non-authenticated status: {auth_res.get('status')}")

                    cookies_returned = auth_res.get("cookies") or {}
                    self.session_cookies.update(cookies_returned)

                    proj = auth_res.get("projection") or {}
                    if self.match_code:
                        proj_code = proj.get("matchCode")
                        proj_uuid = proj.get("matchUuid") or proj.get("id")
                        code_match = bool(proj_code and str(proj_code).upper() == str(self.match_code).upper())
                        uuid_match = bool(proj_uuid and str(proj_uuid).lower() == str(self.match_code).lower())
                        if not (code_match or uuid_match):
                            raise RuntimeError(
                                f"Match assignment mismatch: expected {self.match_code}, got {proj_code or proj_uuid}"
                            )

                    if proj.get("matchUuid"):
                        self.match_uuid = str(proj["matchUuid"])
                        self.adapter.match_uuid = self.match_uuid
                    if proj.get("matchCode") and not self.match_code:
                        self.match_code = str(proj["matchCode"])
                except Exception as exc:
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Match session login verification failed: {exc}")
                    raise RuntimeError(f"PREPARE_GAME failed: Match session login verification failed: {exc}") from exc

                self.telemetry.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Match credentials verified.")

            # 5. Emit ENVIRONMENT_READY only when all 3 gates have genuinely passed!
            repo_url_val = repo_info.get("url", "")
            if repo_url_val and not repo_url_val.startswith("https://"):
                clean_name = Path(repo_url_val).name
                if not clean_name.endswith(".git"):
                    clean_name = f"{clean_name}.git"
                repo_url_val = f"https://git.mtm.local/{clean_name}"

            self.telemetry.emit_environment_ready(
                request_id=descriptor.get("requestId", f"prepare-{self.match_code or '1'}"),
                repo_url=repo_url_val,
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

        # Ensure credentials resolved if missing
        if not (self.username and self.password) or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                    self.adapter.match_uuid = self.match_uuid
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                    self.adapter.session_cookies.update(stored["cookies"])

        # Ensure session authentication with Game Master
        if (not self.session_cookies or not self.adapter.session_cookies) and self.username and self.password:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: login_session in login_match: {e}\n")

        # Actually verify connection and match access against Game Master (fail closed on failure)
        has_credentials = bool(self.bearer_token or self.descriptor.get("apiKeySecret") or self.descriptor.get("gameMasterApiUrl") or self.descriptor.get("baseUrl") or self.session_cookies)
        is_offline = bool(self.descriptor.get("offline") or not has_credentials)

        try:
            state = self.adapter.get_match_projection(self.match_code or self.match_uuid)
            self.match_state = state.get("status", "IN_PROGRESS")
            if not self.match_uuid and state.get("matchUuid"):
                self.match_uuid = state.get("matchUuid")
                self.adapter.match_uuid = self.match_uuid
        except Exception as exc:
            if not is_offline:
                if self.telemetry:
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Match login failed: {exc}")
                raise RuntimeError(f"Match authentication failed for {self.match_code}: {exc}") from exc
            else:
                sys.stderr.write(f"Offline fallback for login_match: {exc}\n")

        return {
            "status": "AUTHENTICATED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "matchState": self.match_state,
        }

    def refresh_match(self) -> Dict[str, Any]:
        """Read public match projection, validate schema, and track wave releases (waves 1..3)."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # Ensure credentials resolved if missing
        if not (self.username and self.password) or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                    self.adapter.match_uuid = self.match_uuid
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                    self.adapter.session_cookies.update(stored["cookies"])

        # Ensure session authentication with Game Master
        if (not self.session_cookies or not self.adapter.session_cookies) and self.username and self.password:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: login_session in refresh_match: {e}\n")

        newly_discovered: List[str] = []
        has_credentials = bool(self.bearer_token or self.descriptor.get("apiKeySecret") or self.descriptor.get("gameMasterApiUrl") or self.descriptor.get("baseUrl") or self.match_page_url or self.session_cookies)
        is_offline = bool(self.descriptor.get("offline") or not has_credentials)

        try:
            projection = self.adapter.get_match_projection(self.match_code or self.match_uuid)
            projection = validate_match_projection_schema(projection)
        except Exception as exc:
            if not is_offline:
                raise RuntimeError(f"Failed to refresh match projection for {self.match_code}: {exc}") from exc
            else:
                sys.stderr.write(f"Offline fallback for refresh_match: {exc}\n")
                projection = {
                    "matchUuid": self.match_uuid or "offline-match-uuid",
                    "matchCode": self.match_code or "offline-match-code",
                    "status": self.match_state or "IN_PROGRESS",
                    "wave": self.current_wave,
                    "backlogItems": [],
                }
                projection = validate_match_projection_schema(projection)

        self.match_state = projection.get("status", self.match_state or "IN_PROGRESS")
        if not self.match_uuid and projection.get("matchUuid"):
            self.match_uuid = projection.get("matchUuid")
            self.adapter.match_uuid = self.match_uuid

        self.latest_projection = projection
        if self.native:
            self.native.register_projection(normalize_native_projection(projection, self.adapter))
            self._reconcile_native_receipts()

        # Release schedules belong to the server; never infer them from elapsed time.
        proj_wave = projection.get("wave")
        if proj_wave is not None:
            try:
                self.current_wave = max(self.current_wave, int(proj_wave))
            except (ValueError, TypeError):
                pass

        items = projection.get("backlogItems", [])
        for item in items:
            if isinstance(item, dict):
                item_wave = item.get("wave")
                if item_wave is not None:
                    try:
                        self.current_wave = max(self.current_wave, int(item_wave))
                    except (ValueError, TypeError):
                        pass

                # Always prioritize clean business key (BUG-01, CR-01) over internal server UUID
                key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId") or item.get("id")
                if key and str(key) not in self.known_backlog_items:
                    key_str = str(key)
                    self.known_backlog_items[key_str] = item
                    # Also alias by server UUID so lookups by UUID resolve to the same item
                    if item.get("id") and str(item.get("id")) != key_str:
                        self.known_backlog_items[str(item.get("id"))] = item
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
                elif key:
                    # Status/deadline/grading transitions must replace stale discovery data.
                    key_str = str(key)
                    self.known_backlog_items[key_str] = item
                    if item.get("id") and str(item["id"]) != key_str:
                        self.known_backlog_items[str(item["id"])] = item

        return {
            "status": self.match_state,
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "wave": self.current_wave,
            "backlogItems": list({str(i.get("key") or i.get("backlogItemKey") or i.get("backlogId") or i.get("id")): i for i in self.known_backlog_items.values()}.values()),
            "newlyDiscovered": newly_discovered,
            "scheduling": normalize_native_projection(projection, self.adapter),
        }

    def start_work(self, backlog_item_key: str) -> Dict[str, Any]:
        """Start work on an open backlog item with automatic telemetry."""
        if not self.adapter or not self.match_uuid:
            raise RuntimeError("Session not configured. Call configure_session first.")

        if self.native:
            self.refresh_match()
            if backlog_item_key not in [t["key"] for t in self.native.plan_tasks()["selected"]]:
                raise ValueError("Item is not eligible under current server state/capacity/deadline")

        # Translate UUID to clean business key if needed
        if backlog_item_key in self.known_backlog_items:
            item = self.known_backlog_items[backlog_item_key]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                backlog_item_key = str(clean_key)

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

            # Release documents can be pushed after the preparation clone, while
            # Start Work returns only on-start assets. Read only paths the public
            # board has announced for this item; do not checkout a fetched tree.
            public_item = self.known_backlog_items.get(backlog_item_key, {})
            public_paths = []
            for field in ("releaseFiles", "onStartFiles"):
                if isinstance(public_item.get(field), list):
                    public_paths.extend(p for p in public_item[field] if isinstance(p, str))
            delivered_raw = result.get("files")
            if isinstance(delivered_raw, list):
                delivered = {}
                for f_item in delivered_raw:
                    if isinstance(f_item, str):
                        p = Path(self.match_root) / f_item
                        delivered[f_item] = p.read_text(encoding="utf-8") if p.exists() else ""
                result["files"] = delivered
            elif isinstance(delivered_raw, dict):
                delivered = delivered_raw
            else:
                delivered = {}
                result["files"] = delivered

            if public_paths:
                from runner.announced_inputs import resolve_announced_files
                unavailable = []
                result["files"] = resolve_announced_files(
                    self.checkout_root, self.match_root, self.assigned_branch,
                    backlog_item_key, [*public_paths, *delivered.keys()], delivered,
                    missing_paths=unavailable)
                if unavailable:
                    result["unavailableAnnouncedFiles"] = unavailable

            # Ensure challenge/<key>.md exists and has rich, resolved description
            challenge_rel = f"challenge/{backlog_item_key}.md"
            existing_desc = (result.get("files") or {}).get(challenge_rel, "")
            is_unresolved = not existing_desc or "Description\n$" in existing_desc or re.search(r'Description\s*\n\s*\$[0-9a-fA-F]+', existing_desc)
            if is_unresolved or challenge_rel not in (result.get("files") or {}):
                title = public_item.get("title") or public_item.get("name") or backlog_item_key
                desc = public_item.get("descriptionMd") or public_item.get("description") or f"# {backlog_item_key}: {title}\n\nTask details from Game Master."
                mvs = result.get("minimumVerificationScenarios") or ""
                start_instr = result.get("startInstruction") or public_item.get("startInstruction") or ""
                synth_content = f"# {backlog_item_key}: {title}\n\n## Description\n{desc}\n"
                if start_instr:
                    synth_content += f"\n## Instructions\n{start_instr}\n"
                if mvs:
                    synth_content += f"\n## Verification Scenarios\n{mvs}\n"
                result.setdefault("files", {})[challenge_rel] = synth_content
                try:
                    ch_path = Path(self.match_root) / challenge_rel
                    ch_path.parent.mkdir(parents=True, exist_ok=True)
                    ch_path.write_text(synth_content, encoding='utf-8')
                except Exception:
                    pass
                if "unavailableAnnouncedFiles" in result and challenge_rel in result["unavailableAnnouncedFiles"]:
                    result["unavailableAnnouncedFiles"].remove(challenge_rel)

            # Attempt official QA test fixture & company context fetching via company tools (no synthetic generation!)
            try:
                fixture_res = self.fetch_fixture(backlog_item_key, write_to_disk=not bool(self.native))
                if fixture_res.get("status") == "FETCHED" and fixture_res.get("content"):
                    result.setdefault("files", {})[fixture_res["path"]] = fixture_res["content"]
                    if fixture_res.get("sidecar") and fixture_res.get("sidecar_content"):
                        result["files"][fixture_res["sidecar"]] = fixture_res["sidecar_content"]
                desc_for_kb = (result.get("files") or {}).get(challenge_rel, "")
                company_ctx = self.prefetch_company_context(backlog_item_key, desc_for_kb)
                if company_ctx.get("kb") or company_ctx.get("logs"):
                    result["companyContext"] = company_ctx
            except Exception:
                pass

            if self.native:
                self.native.register_started(backlog_item_key, result)

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
            if submit_template is not None and not self.native:
                ans_dir = self.match_root / "answers" / backlog_item_key
                ans_dir.mkdir(parents=True, exist_ok=True)
                template_path = ans_dir / "submit.json"
                if not template_path.exists():
                    template_path.write_text(json.dumps(submit_template, indent=2), encoding="utf-8")

            # Write any challenge files delivered with start_work
            delivered_files = result.get("files")
            if isinstance(delivered_files, dict):
                for rel_path, file_content in delivered_files.items():
                    if self.native and str(rel_path).startswith("answers/"):
                        continue
                    try:
                        target_file = self.match_root / rel_path
                        from mtm_competition.integration.git_guard import is_forbidden_path
                        if not target_file.resolve().is_relative_to(self.match_root.resolve()) or is_forbidden_path(rel_path)[0]:
                            raise ValueError("Delivered path escapes match workspace or is immutable")
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        if isinstance(file_content, str):
                            target_file.write_text(file_content, encoding="utf-8")
                        elif isinstance(file_content, bytes):
                            target_file.write_bytes(file_content)
                    except Exception as file_err:
                        sys.stderr.write(f"Warning: could not write delivered file {rel_path}: {file_err}\n")

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

        # Translate UUID to clean business key if needed
        if backlog_item_key in self.known_backlog_items:
            item = self.known_backlog_items[backlog_item_key]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                backlog_item_key = str(clean_key)

        assigned_branch = self.assigned_branch or self.descriptor.get("assignedBranch")
        if not assigned_branch:
            raise ValueError("No assigned branch configured for submission")

        required_test_ids: List[str] = []
        if self.native:
            self.refresh_match()
            self.native.assert_can_submit(backlog_item_key)
            # Rechecking an existing lock is idempotent; another item cannot push in between.
            self.native.begin_integration(backlog_item_key)
            # V2 (P1): push+submit are about to run; the lock must not be auto-reclaimed meanwhile.
            self.native.set_submit_inflight(backlog_item_key, True)
            details = self.native.task_details(backlog_item_key)["data"].get("start_details", {})
            required_test_ids = announced_test_ids(details)

        # Ensure local commits are pushed to remote origin before pre-submission verification
        if self.checkout_root:
            try:
                # Auto-commit any pending evidence or answer files on disk before push
                status_res = subprocess.run(["git", "-C", str(self.checkout_root), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
                if status_res.stdout.strip():
                    # V2 (P1): stage only this item's answers/evidence, never the whole checkout.
                    item_dir = Path(self.match_root) / "answers" / backlog_item_key if self.match_root else None
                    if item_dir is not None and item_dir.exists():
                        subprocess.run(["git", "-C", str(self.checkout_root), "add", "--", str(item_dir)], capture_output=True, timeout=15)
                    subprocess.run(["git", "-C", str(self.checkout_root), "commit", "-m", f"Auto-capture evidence for {backlog_item_key}"], capture_output=True, timeout=15)
                    rev_res = subprocess.run(["git", "-C", str(self.checkout_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
                    if rev_res.returncode == 0 and rev_res.stdout.strip():
                        git_revision = rev_res.stdout.strip()

                if not self.descriptor.get("offline"):
                    # Push first; only rebase when the remote moved (saves a network round trip).
                    pushed = subprocess.run(
                        ["git", "-C", str(self.checkout_root), "push", "origin", assigned_branch],
                        capture_output=True, text=True, timeout=20
                    )
                    if pushed.returncode != 0:
                        subprocess.run(
                            ["git", "-C", str(self.checkout_root), "pull", "--rebase", "origin", assigned_branch],
                            capture_output=True, text=True, timeout=30
                        )
                        retry_push = subprocess.run(
                            ["git", "-C", str(self.checkout_root), "push", "origin", assigned_branch],
                            capture_output=True, text=True, timeout=30
                        )
                        if retry_push.returncode != 0:
                            # V2 (P1): a failed push is never ignored; submitting an unpushed SHA is invalid.
                            raise RuntimeError(f"git push failed for {backlog_item_key}: {(retry_push.stderr or retry_push.stdout).strip()[:300]}")
                        # A rebase rewrites the commit; submit the SHA that was actually pushed.
                        rev_res = subprocess.run(["git", "-C", str(self.checkout_root), "rev-parse", "HEAD"],
                                                 capture_output=True, text=True, timeout=10)
                        if rev_res.returncode == 0 and rev_res.stdout.strip():
                            git_revision = rev_res.stdout.strip()
            except Exception as e:
                sys.stderr.write(f"Notice: Auto-commit/push before submit: {e}\n")
                if str(e).startswith("git push failed"):
                    raise

        # 1. Pre-submission Git Invariant Verification (GIT-01..05) with Self-Healing Retry
        # Honest PARTIAL is a valid, scored submission (Game Master grades partial work). The answer must
        # itself declare PARTIAL; a DONE claim stays strictly gated and nothing is ever upgraded here.
        declares_partial = isinstance(answer, dict) and str(answer.get("status", "")).upper() == "PARTIAL"
        try:
            self.git_guard.validate_pre_submission(
                target_commit_sha=git_revision,
                assigned_branch_ref=assigned_branch,
                backlog_item_key=backlog_item_key,
                answer_payload=answer,
                match_root=self.match_root,
                checkout_root=self.checkout_root,
                require_remote=False if self.descriptor.get("offline") else True,
                raise_on_error=True,
                required_test_ids=required_test_ids,
                allow_partial=declares_partial,
            )
        except Exception as gate_err:
            # V2 (P0): the submission gate is fail-closed. No auto-heal, no forced PARTIAL,
            # no blanket `git add -A`, and no second validation whose result is ignored.
            sys.stderr.write(f"Submission gate rejected {backlog_item_key}; not submitting: {gate_err}\n")
            raise

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

            if self.native:
                self.native.mark_submitted(backlog_item_key, str(server_sub_id))

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
            err_code = getattr(exc, "error_code", None) or getattr(exc, "code", None)
            exc_str = str(exc).lower()
            err_code_str = str(err_code or "").lower()

            is_already_submitted = (status_code == 409 and ("already_submitted" in err_code_str or "already submitted" in exc_str))

            # Terminal rejection: 409 [CLOSED / NOT ALLOWED / EXPIRED], 400, 404, 410, 422
            is_terminal_rejection = (
                (status_code == 409 and not is_already_submitted and any(k in exc_str for k in ("closed", "not allowed", "deadline", "expired")))
                or (status_code in (400, 404, 410, 422))
            )

            if is_terminal_rejection and self.native:
                try:
                    # 1. Reconcile and free worker immediately, transitioning directly to CLOSED
                    self.native.reconcile_worker(
                        backlog_item_key,
                        confirmed_stopped=True,
                        reason=f"GM_TERMINAL_REJECTED_{status_code}: {exc}",
                        requeue=False,
                    )
                except Exception as rec_err:
                    sys.stderr.write(f"Warning: reconcile_worker failed: {rec_err}\n")

                try:
                    # 2. Release integration owner lock immediately
                    self.native.end_integration(backlog_item_key, success=False)
                except Exception:
                    pass

                # 3. Emit dashboard telemetry status as CLOSED
                if self.telemetry:
                    self.telemetry.emit_backlog_status(
                        backlog_id=backlog_item_key,
                        title=self._get_backlog_title(backlog_item_key),
                        status="CLOSED",
                        summary=f"Item closed/rejected by Game Master ({status_code}): {exc}",
                    )
            elif self.native and not is_already_submitted:
                try:
                    self.native.end_integration(backlog_item_key, success=False)
                except Exception:
                    pass

            # Distinguish deterministic 4xx rejection (excluding 429) from unknown outcome (timeouts, 5xx)
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                if is_already_submitted:
                    # R5: 409 ALREADY_SUBMITTED retains in-flight lock, requiring reconciliation against server records
                    self.journal.record_already_submitted(
                        match_uuid=self.match_uuid,
                        backlog_item_key=backlog_item_key,
                        http_code=status_code,
                        error_code=err_code or "ALREADY_SUBMITTED",
                        error_message=str(exc),
                    )
                else:
                    self.journal.record_rejection(
                        match_uuid=self.match_uuid,
                        backlog_item_key=backlog_item_key,
                        http_code=status_code,
                        error_code=err_code,
                        error_message=str(exc),
                    )

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

        # Translate UUID in backlogId if known
        raw_bid = event.get("backlogId")
        if raw_bid and str(raw_bid) in self.known_backlog_items:
            item = self.known_backlog_items[str(raw_bid)]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                event["backlogId"] = str(clean_key)

        # Ensure envelope fields
        if "schemaVersion" not in event:
            event["schemaVersion"] = 1
        if "playerSessionId" not in event:
            event["playerSessionId"] = self.player_session_id

        validate_progress_event(event)
        self.telemetry.log_event(event)
        return {"status": "RECORDED"}

    def _native_coordinator(self) -> NativeTaskCoordinator:
        if not self.native:
            raise RuntimeError("Native scheduling requires START_GAME with MTM_NATIVE_COORDINATION=1")
        return self.native

    def _reconcile_native_receipts(self) -> None:
        if not self.native or not self.journal or not self.match_uuid:
            return
        # Bridge the durable receipt/coordinator crash window; never resend accepted work.
        rows = self.journal.conn.execute(
            "SELECT backlog_item_key,receipt_id FROM submission_journal WHERE match_uuid=? AND state='ACCEPTED' AND receipt_id IS NOT NULL",
            (self.match_uuid,),
        ).fetchall()
        for row in rows:
            try:
                phase = self.native.task_details(row["backlog_item_key"])["phase"]
            except ValueError:
                continue
            if phase in ("INTEGRATING", "INTEGRATED", "SUBMITTED"):
                self.native.mark_submitted(row["backlog_item_key"], row["receipt_id"])

    def plan_tasks(self) -> Dict[str, Any]:
        self.refresh_match()
        return self._native_coordinator().plan_tasks()

    def dispatch_ready(self, estimated_seconds: int = 420) -> Dict[str, Any]:
        """Start Work + claim every item the planner selects, in one call.

        The root only has to spawn one native worker per returned envelope. Failures
        on one item are reported in ``errors`` and never block the others.
        """
        native = self._native_coordinator()
        self.refresh_match()
        plan = native.plan_tasks()
        spawn: List[Dict[str, Any]] = []
        errors: List[Dict[str, Any]] = []
        for task in plan.get("selected", []):
            key = task["key"]
            try:
                details = native.task_details(key)
                if not details.get("started"):
                    self.start_work(key)
                attempt = int(details["data"].get("attempt_number", 1))
                worker_id = f"w-{key}-{attempt}"
                estimate = int(task.get("estimated_seconds") or estimated_seconds)
                claim = self.claim_task(key, worker_id, estimate)
                envelope = claim.get("taskEnvelope", {})
                spawn.append({"backlogItemKey": key, "workerId": worker_id,
                              "lane": envelope.get("lane"), "heavy": envelope.get("heavy", False),
                              "worktreeRoot": claim.get("worktreeRoot"), "matchRoot": claim.get("matchRoot"),
                              "taskEnvelope": envelope})
            except Exception as exc:
                errors.append({"backlogItemKey": key, "reason": str(exc)[:300]})
        after = native.plan_tasks()
        return {"matchStatus": after.get("matchStatus"), "spawn": spawn, "errors": errors,
                "availableSlots": after.get("availableSlots"), "lanes": after.get("lanes"),
                "nextDeadlineSlack": after.get("nextDeadlineSlack"),
                "active": after.get("active", []), "readyToIntegrate": after.get("readyToIntegrate", []),
                "overdueWorkers": after.get("overdueWorkers", []),
                "instruction": "Spawn one native worker per spawn[] entry now (fork_turns='none'), then wait_for_update."}

    # ------------------------------------------------------------------ finalize_item

    @staticmethod
    def _git_run(cwd: Path, *args: str, timeout: int = 30) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=timeout)

    @staticmethod
    def normalize_submit_answer(answer: Dict[str, Any], answers_dir: Path, force_partial: bool = False) -> Dict[str, Any]:
        """Make submit.json honest against what is on disk; never invent evidence.

        Evidence references to missing/empty files are dropped, the affected tests become
        NOT_RUN, and the item is PARTIAL unless every test still passes.
        """
        answer = json.loads(json.dumps(answer or {}))

        def present(rel: Any) -> bool:
            if not isinstance(rel, str) or not rel or rel.startswith("/") or ".." in Path(rel).parts:
                return False
            target = answers_dir / rel
            return target.is_file() and target.stat().st_size > 0

        not_run: List[str] = []
        if isinstance(answer.get("evidenceFiles"), list):
            answer["evidenceFiles"] = [f for f in answer["evidenceFiles"] if present(f)]
        all_pass = True
        for test in answer.get("testResults") or []:
            if not isinstance(test, dict):
                continue
            refs = test.get("evidence")
            if isinstance(refs, list):
                kept = [r for r in refs if present(r)]
                if len(kept) != len(refs):
                    test["evidence"] = kept
                    if str(test.get("result", "")).upper() == "PASS" or not kept:
                        test["result"] = "NOT_RUN"
            if str(test.get("result", "")).upper() != "PASS":
                all_pass = False
                not_run.append(str(test.get("id", "")))
        claimed = str(answer.get("status", "")).upper()
        answer["status"] = "DONE" if (all_pass and not force_partial and claimed in ("DONE", "")) else "PARTIAL"
        answer["_notRun"] = not_run
        return answer

    def _snapshot_worktree(self, worktree: Path, folder: str, key: str) -> Optional[str]:
        """Commit the worker's current deliverables (tracked edits, src/, answers/<key>/)."""
        paths = [f"{folder}/answers/{key}", f"{folder}/src"]
        control = [f":(exclude){folder}/{n}" for n in self.WORKER_CONTROL_FILES]
        self._git_run(worktree, "add", "-u", "--", folder, *control)
        for p in paths:
            if (worktree / p).exists():
                self._git_run(worktree, "add", "-A", "--", p,
                              f":(exclude){folder}/src/node_modules", f":(exclude){folder}/src/.next")
        staged = self._git_run(worktree, "diff", "--cached", "--name-only")
        if staged.stdout.strip():
            self._git_run(worktree, "-c", "user.email=mtm-root@local", "-c", "user.name=MTM Root",
                          "commit", "-m", f"{key}: finalize snapshot")
        head = self._git_run(worktree, "rev-parse", "HEAD")
        return head.stdout.strip() if head.returncode == 0 else None

    def _stop_worker_session(self, worker_match: Path) -> None:
        """Reap a background evidence server left by `project_runtime.py session-start`."""
        try:
            from runner.web_session import stop_recorded_session
            stop_recorded_session(worker_match)
        except Exception:
            pass

    _finalize_lock = threading.RLock()
    _watchdog_started = False

    def _done_first_send_back(self, key: str, details: Dict[str, Any], *, answer_status: str, not_run: List[str],
                              worker_head: Optional[str], force_partial: bool,
                              gate_error: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """DONE-first: while there is real time left, an item that is not DONE (or that the local gate
        rejected) goes back to a worker that continues from this attempt's commit and fixes exactly
        the listed gaps. Only the deadline/match-end auto-finalize (force_partial) submits PARTIAL.
        This applies to PI as well: a fast wrong PI scores 0, a slower correct one still scores.

        MTM_DONE_FIRST=0 disables; MTM_DONE_FIRST_RETRIES caps attempts (default 3);
        MTM_FIX_MIN_SECONDS is the minimum working time a new attempt needs (default 300).
        """
        if force_partial or os.environ.get("MTM_DONE_FIRST", "1") == "0":
            return None
        native = self._native_coordinator()
        if gate_error is None and answer_status == "DONE":
            return None  # DONE goes straight in
        # PI is a speed race, but even the slowest correct PI earns 60% while a wrong one earns 0 and an item
        # is accepted only once (CWQNKAPC: PI-01 submitted fast, scored 0/160). So PI is held to DONE too.
        try:
            cap = int(os.environ.get("MTM_DONE_FIRST_RETRIES", "3"))
            min_window = float(os.environ.get("MTM_FIX_MIN_SECONDS", str(native.FIX_MIN_SECONDS)))
        except ValueError:
            cap, min_window = 3, float(native.FIX_MIN_SECONDS)
        window = native.fix_window_seconds(key)
        if native.fix_attempts(key) >= cap or window is None or window < min_window:
            return None
        reason = (f"gate rejected: {gate_error}" if gate_error
                  else f"answer is {answer_status}, not DONE; tests not passing: {', '.join(not_run) or 'see submit.json'}")
        res = native.send_back_for_fix(key, reason=reason[:600], resume_from=worker_head, not_run=not_run,
                                       gate_error=gate_error[:600] if gate_error else None)
        return {"backlogItemKey": key, "status": "SENT_BACK_FOR_DONE", "fixAttempt": res.get("fixAttempt"),
                "windowSeconds": round(window, 1), "reason": reason[:600], "stopWorker": True,
                "instruction": ("Nothing was submitted. Make sure that worker is stopped, then call dispatch_ready: "
                                "a new worker continues from this attempt's commit with taskEnvelope.fix_request "
                                "and works until check_done passes. Do not submit this item yourself.")}

    WORKER_CONTROL_FILES = ("worker_result.json", ".mtm_stopped.json")

    def _drop_control_files(self, folder: str, key: str) -> None:
        """Keep per-run worker control files out of the shared checkout.

        A worker may commit its own worker_result.json; once cherry-picked, every later worktree would
        inherit it and look 'finished' at birth (CWQNKAPC: STALE-INHERITED-WORKER-RESULT).
        """
        tracked = [f"{folder}/{n}" for n in self.WORKER_CONTROL_FILES
                   if self._git_run(self.checkout_root, "ls-files", "--error-unmatch", "--", f"{folder}/{n}").returncode == 0]
        if tracked:
            self._git_run(self.checkout_root, "rm", "-q", "-f", "--", *tracked)
            self._git_run(self.checkout_root, "-c", "user.email=mtm-root@local", "-c", "user.name=MTM Root",
                          "commit", "-q", "-m", f"{key}: drop worker control files")

    def _pending_worker_commits(self, all_commits: List[str], worker_head: str) -> List[str]:
        """Worker commits whose patch is not yet on the checkout HEAD (oldest first)."""
        res = self._git_run(self.checkout_root, "cherry", "HEAD", worker_head)
        if res.returncode != 0:
            return list(all_commits)
        pending = {line.split()[1] for line in res.stdout.splitlines() if line.startswith("+ ") and len(line.split()) > 1}
        return [c for c in all_commits if c in pending]

    def finalize_item(self, backlog_item_key: str, force_partial: bool = False,
                      confirmed_stopped: bool = False) -> Dict[str, Any]:
        """Serialized entry point: Root and the deadline watchdog never finalize concurrently."""
        with self._finalize_lock:
            return self._finalize_item_impl(backlog_item_key, force_partial, confirmed_stopped)

    # ---- V2: server-side deadline watchdog (does not depend on Root's turn) ----
    def _watchdog_enabled(self) -> bool:
        if os.environ.get("MTM_DEADLINE_WATCHDOG", "1") == "0":
            return False
        return "PYTEST_CURRENT_TEST" not in os.environ

    def _start_deadline_watchdog(self) -> None:
        if self._watchdog_started or not self._watchdog_enabled():
            return
        self._watchdog_started = True
        self._wd_state: Dict[str, Dict[str, Any]] = {}

        def loop() -> None:
            while True:
                time.sleep(3.0)
                try:
                    self._watchdog_tick()
                except Exception as exc:  # never let the watchdog die
                    sys.stderr.write(f"Deadline watchdog error: {exc}\n")
        threading.Thread(target=loop, name="mtm-deadline-watchdog", daemon=True).start()

    def _watchdog_tick(self, now: Optional[float] = None) -> List[Dict[str, Any]]:
        """Finalize items inside the deadline/match-end slack by code. One pass; returns what it did.

        1st pass on a RUNNING worker only requests a stop (Root is told via a `stop_worker` event). After
        MTM_STOP_GRACE_SECONDS the snapshot proceeds anyway with an honest PARTIAL: losing the item is worse
        than a late write, and any later worker writes are ignored. Items are retried at most 4 times.
        """
        if not self.native or self.match_state in ("FINISHED", "PAUSED"):
            return []
        if not hasattr(self, "_wd_state"):
            self._wd_state = {}
        t = time.time() if now is None else now
        fslack, mslack = self._auto_finalize_settings()
        done: List[Dict[str, Any]] = []
        for cand in self.native.deadline_candidates(fslack, mslack):
            key = cand["key"]
            st = self._wd_state.setdefault(key, {"fails": 0, "last": 0.0})
            if st["fails"] >= 4 or t - st["last"] < 3.0:
                continue
            st["last"] = t
            try:
                res = self.finalize_item(key, force_partial=True)
            except Exception as exc:
                st["fails"] += 1
                done.append({"backlogItemKey": key, "status": "FAILED", "reason": str(exc)[:200]})
                continue
            if res.get("status") not in ("STOP_REQUIRED", "STOP_PENDING"):
                st["fails"] = 99  # finished (submitted / already submitted)
            done.append({"backlogItemKey": key, "status": res.get("status")})
        return done

    def _finalize_item_impl(self, backlog_item_key: str, force_partial: bool = False,
                            confirmed_stopped: bool = False) -> Dict[str, Any]:
        """Snapshot worker -> honest submit.json -> cherry-pick -> push -> submit, in one call.

        Used when a worker finishes (worker_result.json written) and, with force_partial,
        automatically before a deadline: whatever is committed is submitted, missing
        captures become NOT_RUN. An unresolved IN_FLIGHT journal entry is replayed exactly.
        """
        native = self._native_coordinator()
        key = backlog_item_key
        deadline_forced = force_partial  # only the deadline/match-end path may submit a non-DONE answer early
        details = native.task_details(key)
        phase = details["phase"]
        if phase == "SUBMITTED":
            return {"backlogItemKey": key, "status": "ALREADY_SUBMITTED", "receiptId": details.get("receiptId")}
        if self.journal and self.match_uuid:
            pending = self.journal.get_in_flight(self.match_uuid, key)
            if pending and pending.get("request_body_bytes"):
                body = json.loads(bytes(pending["request_body_bytes"]).decode("utf-8"))
                receipt = self.submit_work(body["clientSubmissionId"], key, body["gitRevision"], body["answer"])
                return {"backlogItemKey": key, "status": "SUBMITTED", "replayed": True, "receipt": receipt}
        worktree_raw = details.get("worktreeRoot")
        if not worktree_raw:
            raise ValueError(f"{key} has no worker worktree to finalize (phase {phase})")
        worktree = Path(worktree_raw)
        folder = native.repository_folder
        worker_match = worktree / folder
        was_running = phase == "RUNNING"
        if was_running:
            # V2 (P1): never snapshot/integrate a worker that may still be writing.
            stop = native.stop_state(key)
            if confirmed_stopped:
                native.confirm_stopped(key, "root_attested")
            elif stop and not stop.get("confirmed"):
                ack = worker_match / ".mtm_stopped.json"
                try:
                    acked = ack.is_file() and json.loads(ack.read_text(encoding="utf-8")).get("instructionId") == stop.get("instructionId")
                except (OSError, ValueError):
                    acked = False
                if acked:
                    native.confirm_stopped(key, "worker_ack")
            if force_partial and stop and stop.get("requested") and not stop.get("confirmed"):
                grace = float(os.environ.get("MTM_STOP_GRACE_SECONDS", "12"))
                if time.time() - float(stop.get("requestedAt") or time.time()) >= grace:
                    native.confirm_stopped(key, "deadline_grace")
            if not native.is_quiesced(key):
                req = native.request_stop(key, "finalize requested" + (" (deadline)" if force_partial else ""))
                return {"backlogItemKey": key, "status": "STOP_PENDING" if req.get("alreadyRequested") else "STOP_REQUIRED",
                        "stopWorker": True, "control": req.get("instruction"),
                        "instruction": ("Stop this native worker now (deliver `control`), then call finalize_item again with "
                                        "confirmedStopped=true once it has ended, or let the worker write .mtm_stopped.json with "
                                        "this instructionId. Nothing was snapshotted or submitted.")}
        answers_dir = worker_match / "answers" / key
        answers_dir.mkdir(parents=True, exist_ok=True)
        submit_path = answers_dir / "submit.json"
        start_details = details["data"].get("start_details", {})
        if submit_path.is_file():
            try:
                raw_answer = json.loads(submit_path.read_text(encoding="utf-8"))
            except ValueError:
                raw_answer = dict(start_details.get("submitTemplate") or {})
                force_partial = True
        else:
            raw_answer = dict(start_details.get("submitTemplate") or {"schemaVersion": 1, "backlogId": key})
            force_partial = True
        answer = self.normalize_submit_answer(raw_answer, answers_dir, force_partial)
        not_run = answer.pop("_notRun", [])
        submit_path.write_text(json.dumps(answer, indent=2), encoding="utf-8")
        self._stop_worker_session(worker_match)
        worker_head = self._snapshot_worktree(worktree, folder, key)

        if phase in ("RUNNING",):
            result_file = worker_match / "worker_result.json"
            recorded = False
            if result_file.is_file() and not force_partial:
                try:
                    native.record_worker_result(key, json.loads(result_file.read_text(encoding="utf-8")))
                    recorded = True
                except Exception:
                    recorded = False
            if not recorded:
                evidence_dir = answers_dir / "evidence"
                evidence = sorted(f"answers/{key}/evidence/{p.name}" for p in evidence_dir.glob("*")
                                  if p.is_file() and p.stat().st_size > 0) if evidence_dir.is_dir() else []
                native.record_worker_result(key, {
                    "task_id": key, "worker_id": details.get("workerId"), "status": "PARTIAL",
                    "skill_ids": ["mtm-finalize-item"], "changed_files": [f"answers/{key}/submit.json"],
                    "test_results": [], "evidence_paths": evidence, "elapsed_seconds": 0,
                    "note": "finalize_item snapshot" + (" (forced before deadline)" if force_partial else "")})
        elif phase == "NEEDS_ATTENTION":
            raise ValueError(f"{key} worker failed without deliverables; reconcile_worker before retry")

        sent_back = self._done_first_send_back(key, details, answer_status=str(answer.get("status", "")).upper(),
                                               not_run=not_run, worker_head=worker_head, force_partial=deadline_forced)
        if sent_back:
            return sent_back

        self.refresh_match()
        native.begin_integration(key)
        try:
            base = self._git_run(self.checkout_root, "merge-base", "HEAD", worker_head or "HEAD").stdout.strip()
            all_commits = [c for c in self._git_run(self.checkout_root, "rev-list", "--reverse", f"{base}..{worker_head}").stdout.split() if c]
            if not all_commits:
                raise ValueError(f"{key} worker produced no commit to integrate")
            # Idempotent retry: a previous finalize may have cherry-picked these commits and then been
            # rejected by the gate. `git cherry` marks already-applied patches with "-"; never pick twice.
            commits = self._pending_worker_commits(all_commits, worker_head)
            if commits:
                picked = self._git_run(self.checkout_root, "-c", "user.email=mtm-root@local", "-c", "user.name=MTM Root",
                                       "cherry-pick", "--allow-empty", *commits, timeout=60)
                if picked.returncode != 0:
                    self._git_run(self.checkout_root, "cherry-pick", "--abort")
                    raise ValueError(f"cherry-pick conflict for {key}: {(picked.stderr or picked.stdout).strip()[:300]}")
            self._drop_control_files(folder, key)
        except Exception:
            native.end_integration(key, success=False)
            raise
        sha = self._git_run(self.checkout_root, "rev-parse", "HEAD").stdout.strip()
        try:
            receipt = self.submit_work(str(uuid.uuid4()), key, sha, answer)
        except SubmissionGateError as gate_err:
            # V2 (P0): fail-closed. A gate rejection must not leave the global integration lock held,
            # otherwise one unsubmittable item blocks every other item until timeout.
            native.end_integration(key, success=False)
            # DONE-first: a local gate rejection is not the end of the item. Nothing reached Game Master,
            # so a worker can fix the reported gap and the item is submitted later.
            sent_back = self._done_first_send_back(key, details, answer_status=str(answer.get("status", "")).upper(),
                                                   not_run=not_run, worker_head=worker_head,
                                                   force_partial=deadline_forced, gate_error=str(gate_err))
            if sent_back:
                return sent_back
            raise
        return {"backlogItemKey": key, "status": "SUBMITTED", "answerStatus": answer.get("status"),
                "notRun": not_run, "stopWorker": was_running, "receipt": receipt,
                "instruction": "If stopWorker is true, stop that native worker now; its later writes are ignored."}

    def claim_task(self, backlog_item_key: str, worker_id: str, estimated_seconds: int = 420) -> Dict[str, Any]:
        try:
            res = self._native_coordinator().claim_task(backlog_item_key, worker_id, estimated_seconds)
        except Exception as e:
            if "Start Work must succeed" in str(e):
                sys.stderr.write(f"Auto-healing unstarted task {backlog_item_key} before claim...\n")
                self.start_work(backlog_item_key)
                res = self._native_coordinator().claim_task(backlog_item_key, worker_id, estimated_seconds)
            else:
                raise

        # Inject company tools catalog and pre-fetched fixtures/KB into TaskEnvelope metadata & worker worktree
        if isinstance(res, dict) and "taskEnvelope" in res and isinstance(res["taskEnvelope"], dict):
            env = res["taskEnvelope"]
            meta = env.setdefault("metadata", {})
            tools = self.list_company_tools()
            meta["company_tools"] = [
                {"name": t["name"], "category": t.get("category", "other"), "description": t.get("description", "")}
                for t in tools
            ]
            meta["company_tools_help"] = "Use call_company_tool(name, arguments) or CLI `python -m runner.control company call <name> '<json_args>'` to query official KB, QA store, and production logs."

            worker_match_str = res.get("matchRoot") or res.get("match_root")
            if worker_match_str:
                worker_match = Path(worker_match_str)
                fixture_filename = f"{backlog_item_key.lower()}-test-data.json"
                worker_fixture_path = worker_match / "answers" / backlog_item_key / "evidence" / fixture_filename
                if not worker_fixture_path.exists():
                    try:
                        fx = self.fetch_fixture(backlog_item_key, write_to_disk=False)
                        if fx.get("status") == "FETCHED" and fx.get("content"):
                            worker_fixture_path.parent.mkdir(parents=True, exist_ok=True)
                            worker_fixture_path.write_text(fx["content"], encoding="utf-8")
                            if fx.get("sidecar_content"):
                                (worker_fixture_path.parent / f"{fixture_filename}.source.json").write_text(
                                    fx["sidecar_content"], encoding="utf-8"
                                )
                    except Exception:
                        pass

            start_ctx = (meta.get("start_details") or {}).get("companyContext")
            if start_ctx:
                meta["company_context"] = start_ctx
            else:
                try:
                    ctx = self.prefetch_company_context(backlog_item_key, env.get("description", ""))
                    if ctx.get("kb") or ctx.get("logs"):
                        meta["company_context"] = ctx
                except Exception:
                    pass
        return res

    def record_worker_result(self, backlog_item_key: str, worker_result: Dict[str, Any]) -> Dict[str, Any]:
        return self._native_coordinator().record_worker_result(backlog_item_key, worker_result)

    def integration_gate(self, backlog_item_key: str, action: str = "begin") -> Dict[str, Any]:
        native = self._native_coordinator()
        if action == "begin":
            self.refresh_match()
            return native.begin_integration(backlog_item_key, require_quiesced=os.environ.get("MTM_ORCH_V2_ENABLED", "0") == "1")
        if action == "failed":
            return native.end_integration(backlog_item_key, success=False)
        raise ValueError("integration_gate action must be begin or failed")

    def reconcile_worker(self, backlog_item_key: str, confirmed_stopped: bool, reason: str, requeue: bool = True,
                         never_spawned: bool = False) -> Dict[str, Any]:
        native = self._native_coordinator()
        if confirmed_stopped is True:
            try:
                wt = native.task_details(backlog_item_key).get("worktreeRoot")
                if wt:
                    self._stop_worker_session(Path(wt) / native.repository_folder)
            except Exception:
                pass
        return native.reconcile_worker(
            backlog_item_key, confirmed_stopped=confirmed_stopped, reason=reason, requeue=requeue, never_spawned=never_spawned)

    def wait_for_update(self, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Bounded serial polling detecting wave releases and match status transitions."""
        bounded_ms = min(max(100, timeout_ms), 55000)
        start_time = time.time()
        elapsed_ms = 0
        poll_interval = 2.0
        if self.native and self.adapter:
            return self._wait_for_native_events(bounded_ms)

        while elapsed_ms < bounded_ms:
            # Poll match state and waves
            if self.adapter:
                old_wave = self.current_wave
                res = self.refresh_match()
                new_items = res.get("newlyDiscovered", [])
                if new_items or self.current_wave > old_wave:
                    return {
                        "status": "UPDATED",
                        "matchState": self.match_state,
                        "wave": self.current_wave,
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


    def _auto_finalize_settings(self) -> tuple[Optional[float], Optional[float]]:
        def read(name: str, default: str) -> Optional[float]:
            raw = os.environ.get(name, default).strip()
            try:
                value = float(raw)
            except ValueError:
                return None
            return value if value > 0 else None
        return read("MTM_AUTO_FINALIZE_SLACK", "150"), read("MTM_AUTO_FINALIZE_MATCH_SLACK", "240")

    def _wait_for_native_events(self, bounded_ms: int) -> Dict[str, Any]:
        """Return as soon as anything needs a root decision.

        Files are checked every second, the Game Master board every two. Items near
        their deadline (or the match end) are finalized here by code, not by the LLM.
        """
        native = self._native_coordinator()
        start = time.time()
        last_refresh = 0.0
        finalize_slack, match_slack = self._auto_finalize_settings()
        new_items: List[Any] = []
        old_wave = self.current_wave
        while True:
            now = time.time()
            if now - last_refresh >= 2.0:
                res = self.refresh_match()
                last_refresh = now
                new_items.extend(res.get("newlyDiscovered", []))
                if self.match_state in ("FINISHED", "PAUSED"):
                    return {"status": self.match_state, "matchState": self.match_state,
                            "elapsedMs": int((time.time() - start) * 1000)}
            events = native.poll_events(finalize_slack, match_slack)
            if new_items or self.current_wave > old_wave:
                events.insert(0, {"type": "new_items", "newItems": new_items, "wave": self.current_wave})
            if events:
                finalized = []
                for ev in [e for e in events if e["type"] == "auto_finalize"]:
                    try:
                        finalized.append(self.finalize_item(ev["backlogItemKey"], force_partial=True))
                    except Exception as exc:
                        finalized.append({"backlogItemKey": ev["backlogItemKey"], "status": "FAILED",
                                          "reason": str(exc)[:300]})
                out: Dict[str, Any] = {"status": "UPDATED", "matchState": self.match_state,
                                       "wave": self.current_wave, "newItems": new_items, "events": events,
                                       "autoFinalized": finalized,
                                       "elapsedMs": int((time.time() - start) * 1000)}
                if any(e["type"] in ("slot_free", "new_items") for e in events) or finalized:
                    out["dispatch"] = self.dispatch_ready()
                out["instruction"] = ("Spawn every dispatch.spawn[] worker; call finalize_item for each "
                                      "worker_result_file event; for every autoFinalized entry with status STOP_REQUIRED/STOP_PENDING, "
                                      "stop that native worker, then call finalize_item with confirmedStopped=true. For every `stop_worker` event, "
                                      "deliver its `control` to that worker and close it now. A server watchdog finalizes items inside the "
                                      "deadline slack on its own after a short stop grace; never wait for it idly.")
                return out
            if (time.time() - start) * 1000 >= bounded_ms:
                return {"status": "TIMEOUT", "matchState": self.match_state,
                        "elapsedMs": int((time.time() - start) * 1000)}
            time.sleep(1.0)


def get_tool_definitions() -> List[Dict[str, Any]]:
    native_tools = [
        ("plan_tasks", "Refresh server board and return eligible native tasks, reserved urgent capacity, and ready integrations.", {}, []),
        ("dispatch_ready", "Start Work + claim every task the planner selects (4 normal slots + 1 PI-only reserved slot) and return ready TaskEnvelopes. Root only spawns one native worker per spawn[] entry.",
         {"estimatedSeconds": {"type": "integer", "minimum": 10, "default": 420}}, []),
        ("finalize_item", "Root-only: snapshot a worker's worktree, make submit.json honest (missing evidence -> NOT_RUN, PARTIAL), cherry-pick, push and submit in one call. DONE-first: while time remains a non-DONE answer or a local gate rejection is not submitted; it returns SENT_BACK_FOR_DONE and the item is requeued for a worker to fix (then stop that worker and call dispatch_ready). Never fabricates evidence.",
         {"backlogItemKey": {"type": "string"}, "forcePartial": {"type": "boolean", "default": False},
          "confirmedStopped": {"type": "boolean", "default": False}}, ["backlogItemKey"]),
        ("claim_task", "Reserve an eligible task after Start Work; return isolated worktree and sanitized TaskEnvelope for native spawn.",
         {"backlogItemKey": {"type": "string"}, "workerId": {"type": "string"}, "estimatedSeconds": {"type": "integer", "minimum": 10, "default": 420}}, ["backlogItemKey", "workerId"]),
        ("record_worker_result", "Validate completed native WorkerResult and required local artifacts before root integration.",
         {"backlogItemKey": {"type": "string"}, "workerResult": {"type": "object"}}, ["backlogItemKey", "workerResult"]),
        ("integration_gate", "Root-only exclusive integration/push/submit lock. Begin before Git mutation; failed releases after reconciliation. Submit receipt releases success.",
         {"backlogItemKey": {"type": "string"}, "action": {"type": "string", "enum": ["begin", "failed"], "default": "begin"}}, ["backlogItemKey"]),
        ("reconcile_worker", "Reconcile a stopped/completed failed native worker. Never confirm stopped while it is still writing.",
         {"backlogItemKey": {"type": "string"}, "confirmedStopped": {"type": "boolean"}, "reason": {"type": "string"}, "requeue": {"type": "boolean", "default": True}, "neverSpawned": {"type": "boolean", "default": False}}, ["backlogItemKey", "confirmedStopped", "reason"]),
    ]
    return [{"name": name, "description": description, "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}
            for name, description, props, required in native_tools] + [
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
        {
            "name": "list_company_tools",
            "description": "List authoritative company tools available on the Match MCP server (KB search, QA fixture store, production observability logs).",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "call_company_tool",
            "description": "Invoke an authoritative company tool (KB search, QA fixture store, production observability logs) via Match MCP server.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "The exact name of the company tool to call"},
                    "arguments": {"type": "object", "description": "Arguments object passed to the tool"},
                },
                "required": ["name", "arguments"],
            },
        },
        {
            "name": "fetch_fixture",
            "description": "Fetch official QA test data fixture from Match MCP and save into answers/<key>/evidence/<id>-test-data.json with .source.json sidecar.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "backlogItemKey": {"type": "string", "description": "Backlog item key (e.g. CR-01, BUG-02)"},
                    "toolName": {"type": "string", "description": "Optional specific QA tool name to use"},
                    "arguments": {"type": "object", "description": "Optional custom arguments object"},
                },
                "required": ["backlogItemKey"],
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
                elif tool_name == "plan_tasks":
                    val = server.plan_tasks()
                elif tool_name == "dispatch_ready":
                    val = server.dispatch_ready(tool_args.get("estimatedSeconds", 420))
                elif tool_name == "finalize_item":
                    val = server.finalize_item(tool_args["backlogItemKey"], bool(tool_args.get("forcePartial", False)),
                                           bool(tool_args.get("confirmedStopped", False)))
                elif tool_name == "claim_task":
                    val = server.claim_task(tool_args["backlogItemKey"], tool_args["workerId"], tool_args.get("estimatedSeconds", 420))
                elif tool_name == "record_worker_result":
                    val = server.record_worker_result(tool_args["backlogItemKey"], tool_args["workerResult"])
                elif tool_name == "integration_gate":
                    val = server.integration_gate(tool_args["backlogItemKey"], tool_args.get("action", "begin"))
                elif tool_name == "reconcile_worker":
                    val = server.reconcile_worker(tool_args["backlogItemKey"], tool_args["confirmedStopped"], tool_args["reason"], tool_args.get("requeue", True), tool_args.get("neverSpawned", False))
                elif tool_name == "list_company_tools":
                    val = server.list_company_tools()
                elif tool_name == "call_company_tool":
                    val = server.call_company_tool(tool_args.get("name", ""), tool_args.get("arguments", {}))
                elif tool_name == "fetch_fixture":
                    val = server.fetch_fixture(
                        tool_args.get("backlogItemKey", ""),
                        tool_name=tool_args.get("toolName"),
                        arguments=tool_args.get("arguments"),
                    )
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
