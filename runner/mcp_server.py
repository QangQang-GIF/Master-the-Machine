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
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.scheduling.native import NativeTaskCoordinator
from mtm_knowledge.bootstrap import KnowledgeBootstrap, detect_tech_stack
from runner.telemetry import (
    TelemetryLogger,
    get_telemetry_logger,
    validate_progress_event,
)


_SESSION_MEMORY: Dict[tuple[str, str], Dict[str, Any]] = {}


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
    """Inventory explicit public scenario IDs, independent of a worker's answer."""
    ids: set[str] = set()
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
            ids.update(re.findall(r"\b(?:[A-Z0-9]+-)*(?:TC|MVS|SCENARIO|TEST)-\d+(?:-[A-Za-z0-9]+)*\b", value))
    walk(details.get("submitTemplate", {}))
    walk(details.get("minimumVerificationScenarios", details.get("mvs")), True)
    return sorted(ids)


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
                    timeout=float(os.environ.get("MTM_COMPANY_TOOL_TIMEOUT", "10")),
                    state_dir=state_dir,
                )
            self.company_tools = self.company_tools_client.discover_tools()
        except Exception as exc:
            sys.stderr.write(f"Warning: company tools discovery: {exc}\n")
        if self.company_tools_client:
            self._ensure_company_bridge()

    # ---- Company-tool bridge for worker sessions -------------------------------------------------
    # K2EMUW3I: native worker sessions could not call company tools (their helper process has no
    # match credentials), so the Root fetched 44 KB/log results and copied them into worker folders:
    # ~80 Root model turns. A worker's helper (MCP or `runner.control company ...`) now drops a request
    # file into the bridge folder and the Root's helper process, which holds the credentials in memory,
    # answers it on a background thread. No credential, HTTP listener or bearer ever leaves the Root.

    BRIDGE_OPS = ("list", "call", "collect_logs", "fetch_fixture")

    def company_bridge_dir(self) -> Path:
        """Folder the Root helper serves. In a team workspace both candidates below are the same folder."""
        override = os.environ.get("MTM_COMPANY_BRIDGE_DIR")
        if override:
            return Path(override)
        return Path(self.workspace_root) / "team-sdlc-pack" / "state" / "company-bridge"

    def _bridge_candidates(self) -> List[Path]:
        override = os.environ.get("MTM_COMPANY_BRIDGE_DIR")
        if override:
            return [Path(override)]
        # A worker CLI (`runner.control company ...`) runs with its worktree as cwd, so also look next to
        # this file: <TeamWorkspace>/team-sdlc-pack/runner/mcp_server.py -> team-sdlc-pack/state/company-bridge.
        return [self.company_bridge_dir(), Path(__file__).resolve().parents[1] / "state" / "company-bridge"]

    def _has_company_credentials(self) -> bool:
        return bool(self.company_tools_client or self.bearer_token or self.descriptor.get("apiKeySecret")
                    or self.descriptor.get("bearerToken"))

    def _ensure_company_bridge(self) -> None:
        if getattr(self, "_bridge_thread", None) is not None or os.environ.get("MTM_COMPANY_BRIDGE", "1") == "0":
            return
        bridge = self.company_bridge_dir()
        bridge.mkdir(parents=True, exist_ok=True)
        self._bridge_busy: set = set()
        self._bridge_lock = threading.Lock()
        self._bridge_thread = threading.Thread(target=self._bridge_loop, name="company-bridge", daemon=True)
        self._bridge_thread.start()

    def _bridge_loop(self) -> None:
        bridge = self.company_bridge_dir()
        last_beat = 0.0
        while self.running:
            try:
                now = time.time()
                if now - last_beat >= 2:
                    _atomic_write_json(bridge / "root.json", {"pid": os.getpid(), "match": self.match_code,
                                                              "updated": now})
                    last_beat = now
                for req in sorted(bridge.glob("req-*.json")):
                    rid = req.stem[4:]
                    with self._bridge_lock:
                        if rid in self._bridge_busy or len(self._bridge_busy) >= 3:
                            continue
                        self._bridge_busy.add(rid)
                    threading.Thread(target=self._bridge_serve, args=(req, rid), daemon=True).start()
            except Exception as exc:
                sys.stderr.write(f"Warning: company bridge loop: {exc}\n")
            time.sleep(0.2)

    def _bridge_serve(self, req: Path, rid: str) -> None:
        bridge = self.company_bridge_dir()
        try:
            try:
                request = json.loads(req.read_text(encoding="utf-8"))
                op, args = request.get("op"), request.get("args") or {}
                if op not in self.BRIDGE_OPS or not isinstance(args, dict):
                    raise ValueError(f"unsupported bridge operation {op!r}")
                if op == "list":
                    value: Any = self.list_company_tools()
                elif op == "call":
                    value = self.call_company_tool(str(args.get("name", "")), args.get("arguments") or {})
                elif op == "collect_logs":
                    value = self.collect_logs(args.get("query", ""), args.get("service", ""), args.get("outPath"),
                                              max_calls=int(args.get("maxCalls", 600)))
                else:   # fetch_fixture: content only; the worker writes it into its own worktree
                    value = self.fetch_fixture(str(args.get("backlogItemKey", "")), tool_name=args.get("toolName"),
                                               arguments=args.get("arguments"), write_to_disk=False)
                response = {"ok": True, "result": value}
            except Exception as exc:
                response = {"ok": False, "error": str(exc)[:2000]}
            response["finished"] = time.time()
            _atomic_write_json(bridge / f"res-{rid}.json", response)
            try:
                req.unlink()
            except OSError:
                pass
        finally:
            with self._bridge_lock:
                self._bridge_busy.discard(rid)

    def _delegate_company(self, op: str, args: Dict[str, Any], wait_seconds: float) -> Any:
        """Worker side: ask the Root's helper to run a company-tool operation and wait for its answer."""
        bridge, beat = self.company_bridge_dir(), {}
        for candidate in self._bridge_candidates():
            try:
                found = json.loads((candidate / "root.json").read_text(encoding="utf-8"))
            except Exception:
                continue
            if time.time() - float(found.get("updated") or 0) <= 15 and found.get("pid") != os.getpid():
                bridge, beat = candidate, found
                break
        if not beat:
            raise CompanyBridgeUnavailable("Company tools unavailable: no match credentials in this process and the Root "
                               "helper is not serving the company-tool bridge. Report this blocker; do not retry.")
        rid = hashlib.sha256(json.dumps([op, args], sort_keys=True).encode("utf-8")).hexdigest()[:20]
        res, req = bridge / f"res-{rid}.json", bridge / f"req-{rid}.json"
        if res.exists() and time.time() - res.stat().st_mtime > 600:
            res.unlink(missing_ok=True)
        if not res.exists() and not req.exists():
            _atomic_write_json(req, {"op": op, "args": args, "created": time.time()})
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if res.exists():
                try:
                    response = json.loads(res.read_text(encoding="utf-8"))
                except Exception:
                    time.sleep(0.3)
                    continue
                if not response.get("ok"):
                    res.unlink(missing_ok=True)       # an error is not cached: a later call retries
                    raise RuntimeError(response.get("error") or "company tool failed")
                return response.get("result")
            time.sleep(0.2)
        return {"status": "PENDING", "requestId": rid,
                "note": "Still running in the Root helper. Call again with exactly the same arguments to get the result."}


    def list_company_tools(self) -> List[Dict[str, Any]]:
        """List discovered tools from the official Match MCP server."""
        if not self._has_company_credentials():
            try:
                listed = self._delegate_company("list", {}, 20)
            except CompanyBridgeUnavailable:
                return []
            return listed if isinstance(listed, list) else []
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
        if not self._has_company_credentials():
            try:
                return self._delegate_company("call", {"name": name, "arguments": arguments or {}}, 45)
            except CompanyBridgeUnavailable:
                pass                       # fall through to the original, explicit error
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
        if not self._has_company_credentials():
            try:
                return self._delegate_company("fetch_fixture", {"backlogItemKey": backlog_item_key, "toolName": tool_name,
                                                                "arguments": arguments}, 45)
            except CompanyBridgeUnavailable as exc:
                return {"status": "UNAVAILABLE", "reason": str(exc)}
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
            # Article ids look like KB-12, KB-SUP-9901, KB-BANK-RULE-06. No keyword fallback: it pulled
            # unrelated 17-20 KB articles that every worker turn then re-read. Workers search themselves.
            for kb_id in kb_article_ids(description_text or ""):
                if kb_id not in queries:
                    queries.append(kb_id)

            param_key = "query"
            for candidate_key in ("query", "keyword", "search", "id", "topic", "term"):
                if candidate_key in kb_props:
                    param_key = candidate_key
                    break

            from concurrent.futures import ThreadPoolExecutor

            def one(q: str) -> Optional[Dict[str, Any]]:
                try:
                    res = self.company_tools_client.call_tool(kb_tool_name, {param_key: q})
                    return {"query": q, "tool": kb_tool_name, "result": res} if res else None
                except Exception as exc:
                    return {"query": q, "tool": kb_tool_name, "error": str(exc)[:300]}

            with ThreadPoolExecutor(max_workers=4) as pool:
                for entry in pool.map(one, queries[:12]):
                    if entry:
                        context["kb"].append(entry)

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
            )
            self._reconcile_native_receipts()

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
        client_submission_id: Optional[str] = None,
        backlog_item_key: str = "",
        git_revision: Optional[str] = None,
        answer: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Omitted arguments are filled from disk instead of the Root's context: `answer` from the root
        checkout's answers/<key>/submit.json, `gitRevision` from the root HEAD, and `clientSubmissionId`
        from the journal (an unresolved submit keeps its identity) or a fresh UUID."""
        if not backlog_item_key:
            raise ValueError("backlogItemKey is required")
        entry = self._in_flight_entry(backlog_item_key)
        if entry and answer is None and not git_revision:
            journaled = json.loads(bytes(entry["request_body_bytes"]).decode("utf-8"))
            client_submission_id = journaled["clientSubmissionId"]
            git_revision, answer = journaled["gitRevision"], journaled["answer"]
        if answer is None:
            path = Path(self.match_root) / "answers" / backlog_item_key / "submit.json"
            if not path.is_file():
                raise FileNotFoundError(f"{backlog_item_key}: {path} not found in the root checkout; integrate first")
            answer = json.loads(path.read_text(encoding="utf-8-sig"))
        if not git_revision:
            git_revision = self._git_head()
        if not client_submission_id:
            client_submission_id = (entry or {}).get("client_submission_id") or str(uuid.uuid4())
        return self._submit_work_impl(client_submission_id, backlog_item_key, git_revision, answer)

    def _git_head(self) -> str:
        res = subprocess.run(["git", "-C", str(self.checkout_root), "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10)
        if res.returncode != 0 or not res.stdout.strip():
            raise RuntimeError(f"git rev-parse HEAD failed: {res.stderr.strip()}")
        return res.stdout.strip()

    def _in_flight_entry(self, key: str) -> Optional[Dict[str, Any]]:
        if not (self.journal and self.match_uuid):
            return None
        entry = self.journal.get_in_flight(self.match_uuid, key)
        return entry if entry and entry.get("state") == "IN_FLIGHT" and entry.get("request_body_bytes") else None

    def integrate_and_submit(self, backlog_item_key: str) -> Dict[str, Any]:
        """The whole SUBMIT path in one call: integration lock -> cherry-pick the worker's commits onto the
        root checkout (skipping commits already there) -> submit_work from answers/<key>/submit.json.
        A conflict aborts the cherry-pick, releases the lock and is returned with the files. A Submit whose
        outcome is unknown is resent (same journaled bytes and idempotency key) a few seconds later."""
        native = self._native_coordinator()
        key = backlog_item_key
        if not self._in_flight_entry(key):
            self.refresh_match()
            native.begin_integration(key)
            details = native.task_details(key)
            worktree = details.get("worktreeRoot")
            if not worktree:
                native.end_integration(key, success=False)
                raise ValueError(f"{key}: no worker worktree to integrate")
            head = subprocess.run(["git", "-C", worktree, "rev-parse", "HEAD"], capture_output=True, text=True, timeout=15)
            if head.returncode != 0:
                native.end_integration(key, success=False)
                raise RuntimeError(f"{key}: cannot read worker HEAD: {head.stderr.strip()[:200]}")
            git = lambda *a, t=60: subprocess.run(["git", "-C", str(self.checkout_root), *a], capture_output=True,
                                                   text=True, timeout=t)
            cherry = git("cherry", "HEAD", head.stdout.strip())
            if cherry.returncode != 0:
                native.end_integration(key, success=False)
                raise RuntimeError(f"{key}: git cherry failed: {cherry.stderr.strip()[:200]}")
            todo = [line.split()[1] for line in cherry.stdout.splitlines() if line.startswith("+ ")]
            if todo:
                pick = git("cherry-pick", "--allow-empty", *todo, t=120)
                if pick.returncode != 0:
                    conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.splitlines()
                    git("cherry-pick", "--abort")
                    native.end_integration(key, success=False)
                    return {"key": key, "status": "CONFLICT", "conflicts": [c for c in conflicts if c][:20],
                            "error": (pick.stderr or pick.stdout).strip()[:400],
                            "next": "REWORK via reconcile_worker(requeue=true), or integrate manually and call integrate_and_submit again."}
            if not (Path(self.match_root) / "answers" / key / "submit.json").is_file():
                native.end_integration(key, success=False)
                raise FileNotFoundError(f"{key}: the worker commits do not contain answers/{key}/submit.json")
        attempts = max(1, int(os.environ.get("MTM_SUBMIT_FAST_REPLAYS", "2")))
        for attempt in range(attempts + 1):
            try:
                self.submit_work(backlog_item_key=key)        # raises unless Game Master accepted it
                break
            except Exception:
                if attempt >= attempts or not self._in_flight_entry(key):
                    raise                                     # validation/terminal errors: nothing to replay
                time.sleep(float(os.environ.get("MTM_SUBMIT_REPLAY_DELAY", "3")))
        return {"key": key, "status": "ACCEPTED", "receiptId": native.task_details(key).get("receiptId"),
                "gitRevision": self._git_head(), "note": "ACCEPTED is a receipt, not a grade."}

    def _submit_work_impl(
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
            details = self.native.task_details(backlog_item_key)["data"].get("start_details", {})
            required_test_ids = announced_test_ids(details)

        # Ensure local commits are pushed to remote origin before pre-submission verification
        if self.checkout_root:
            try:
                # Auto-commit any pending evidence or answer files on disk before push
                status_res = subprocess.run(["git", "-C", str(self.checkout_root), "status", "--porcelain"], capture_output=True, text=True, timeout=10)
                if status_res.stdout.strip():
                    subprocess.run(["git", "-C", str(self.checkout_root), "add", "-A"], capture_output=True, timeout=15)
                    subprocess.run(["git", "-C", str(self.checkout_root), "commit", "-m", f"Auto-capture evidence for {backlog_item_key}"], capture_output=True, timeout=15)
                    rev_res = subprocess.run(["git", "-C", str(self.checkout_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
                    if rev_res.returncode == 0 and rev_res.stdout.strip():
                        git_revision = rev_res.stdout.strip()

                if not self.descriptor.get("offline"):
                    subprocess.run(
                        ["git", "-C", str(self.checkout_root), "pull", "--rebase", "origin", assigned_branch],
                        capture_output=True, text=True, timeout=30
                    )
                    subprocess.run(
                        ["git", "-C", str(self.checkout_root), "push", "origin", assigned_branch],
                        capture_output=True, text=True, timeout=30
                    )
            except Exception as e:
                sys.stderr.write(f"Notice: Auto-commit/push before submit: {e}\n")

        # 1. Pre-submission Git Invariant Verification (GIT-01..05) with Self-Healing Retry
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
            )
        except Exception as gate_err:
            sys.stderr.write(f"Submission Gate auto-healing retry for {backlog_item_key}: {gate_err}\n")
            if isinstance(answer, dict):
                answer["status"] = "PARTIAL"
            if self.checkout_root:
                subprocess.run(["git", "-C", str(self.checkout_root), "add", "-A"], capture_output=True, timeout=15)
                subprocess.run(["git", "-C", str(self.checkout_root), "commit", "-m", f"Auto-heal evidence for {backlog_item_key}"], capture_output=True, timeout=15)
                rev_res = subprocess.run(["git", "-C", str(self.checkout_root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
                if rev_res.returncode == 0 and rev_res.stdout.strip():
                    git_revision = rev_res.stdout.strip()
                if not self.descriptor.get("offline"):
                    subprocess.run(["git", "-C", str(self.checkout_root), "push", "origin", assigned_branch], capture_output=True, text=True, timeout=30)
            self.git_guard.validate_pre_submission(
                target_commit_sha=git_revision,
                assigned_branch_ref=assigned_branch,
                backlog_item_key=backlog_item_key,
                answer_payload=answer,
                match_root=self.match_root,
                checkout_root=self.checkout_root,
                require_remote=False if self.descriptor.get("offline") else True,
                raise_on_error=False,
                required_test_ids=required_test_ids,
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

    def record_worker_result(self, backlog_item_key: str, worker_result: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Record a worker's result. Without `worker_result` the helper reads the worker's own
        worker_result.json from its worktree, so the Root never copies the file through its context.
        Any dev server the worker left running is stopped afterwards (harmless if none)."""
        native = self._native_coordinator()
        details = native.task_details(backlog_item_key)
        worker_match = Path(details["worktreeRoot"]) / native.repository_folder if details.get("worktreeRoot") else None
        if worker_result is None:
            if worker_match is None:
                raise ValueError(f"{backlog_item_key}: no worker worktree; pass workerResult explicitly")
            path = worker_match / "worker_result.json"
            if not path.is_file():
                raise FileNotFoundError(f"{backlog_item_key}: worker has not written {path.name} yet")
            worker_result = json.loads(path.read_text(encoding="utf-8-sig"))
            if not isinstance(worker_result, dict):
                raise ValueError(f"{backlog_item_key}: worker_result.json is not a JSON object")
        result = native.record_worker_result(backlog_item_key, worker_result)
        if worker_match is not None and details.get("workerId"):
            self._stop_worker_server(worker_match, str(details["workerId"]))
        if isinstance(result, dict) and isinstance(result.get("workerResult"), dict):
            # The Root needs the verdict, not the whole result echoed back.
            wr = result["workerResult"]
            result["workerResult"] = {k: wr.get(k) for k in ("status", "answer_status", "verification_status",
                                                             "mvs_passed") if k in wr}
        return result

    def _stop_worker_server(self, worker_match: Path, worker_id: str) -> None:
        runtime = Path(__file__).resolve().parent / "project_runtime.py"
        if not runtime.is_file() or not self.match_code or "serve-stop" not in runtime.read_text(encoding="utf-8"):
            return
        try:
            subprocess.run([sys.executable, str(runtime), "--match-root", str(worker_match), "--match-id",
                            str(self.match_code), "--worker-id", worker_id, "serve-stop"],
                           capture_output=True, text=True, timeout=30)
        except Exception as exc:
            sys.stderr.write(f"Warning: serve-stop for {worker_id}: {exc}\n")


    def integration_gate(self, backlog_item_key: str, action: str = "begin") -> Dict[str, Any]:
        native = self._native_coordinator()
        if action == "begin":
            self.refresh_match()
            return native.begin_integration(backlog_item_key)
        if action == "failed":
            return native.end_integration(backlog_item_key, success=False)
        raise ValueError("integration_gate action must be begin or failed")

    def reconcile_worker(self, backlog_item_key: str, confirmed_stopped: bool, reason: str, requeue: bool = True) -> Dict[str, Any]:
        return self._native_coordinator().reconcile_worker(
            backlog_item_key, confirmed_stopped=confirmed_stopped, reason=reason, requeue=requeue)

    def wait_for_update(self, timeout_ms: int = 55000) -> Dict[str, Any]:
        """Bounded serial polling detecting wave releases and match status transitions."""
        bounded_ms = min(max(100, timeout_ms), 55000)
        start_time = time.time()
        elapsed_ms = 0
        poll_interval = 2.0

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


class CompanyBridgeUnavailable(RuntimeError):
    """No credentials in this process and no live Root helper serving the company-tool bridge."""


def _atomic_write_json(path: Path, value: Any) -> None:
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def get_tool_definitions() -> List[Dict[str, Any]]:
    native_tools = [
        ("plan_tasks", "Refresh server board and return eligible native tasks, reserved urgent capacity, and ready integrations.", {}, []),
        ("claim_task", "Reserve an eligible task after Start Work; return isolated worktree and sanitized TaskEnvelope for native spawn.",
         {"backlogItemKey": {"type": "string"}, "workerId": {"type": "string"}, "estimatedSeconds": {"type": "integer", "minimum": 10, "default": 420}}, ["backlogItemKey", "workerId"]),
        ("record_worker_result", "Validate a finished worker's result and artifacts before integration. Omit workerResult: the helper reads the worker's worker_result.json itself. Returns phase and a short verdict.",
         {"backlogItemKey": {"type": "string"}, "workerResult": {"type": "object", "description": "Optional; only when no worker_result.json exists"}}, ["backlogItemKey"]),
        ("integrate_and_submit", "The whole submit path in one call: integration lock, cherry-pick the worker commits onto the root checkout (skips commits already there), then submit_work from answers/<key>/submit.json. Returns ACCEPTED+receiptId, or CONFLICT with files. An unknown-outcome submit is resent with the same journaled body.",
         {"backlogItemKey": {"type": "string"}}, ["backlogItemKey"]),
        ("integration_gate", "Root-only exclusive integration/push/submit lock. Begin before Git mutation; failed releases after reconciliation. Submit receipt releases success.",
         {"backlogItemKey": {"type": "string"}, "action": {"type": "string", "enum": ["begin", "failed"], "default": "begin"}}, ["backlogItemKey"]),
        ("reconcile_worker", "Reconcile a stopped/completed failed native worker. Never confirm stopped while it is still writing.",
         {"backlogItemKey": {"type": "string"}, "confirmedStopped": {"type": "boolean"}, "reason": {"type": "string"}, "requeue": {"type": "boolean", "default": True}}, ["backlogItemKey", "confirmedStopped", "reason"]),
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
                    "clientSubmissionId": {"type": "string", "description": "Optional UUID v4; default: the journaled one for an unresolved submit, else a new one"},
                    "backlogItemKey": {"type": "string", "description": "Backlog item key"},
                    "gitRevision": {"type": "string", "description": "Full remote-pushed 40-character Git SHA"},
                    "answer": {"type": "object", "description": "Optional; default: answers/<key>/submit.json in the root checkout. Do not paste it."},
                },
                "required": ["backlogItemKey"],
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
                    "timeoutMs": {"type": "integer", "description": "Timeout in milliseconds (max 55000). Use 55000: it returns early on any change.", "default": 55000}
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


# ---- Compact tool output for the Root ------------------------------------------------------
# start_work / claim_task / plan_tasks used to hand the Root every delivered file, the submit
# template, MVS, company context and the item description - twice per item, plus once per poll.
# Over a 40-minute match that filled the Root's context (594OYFBS: Codex FAILED mid-match).
# The full payload is now written to state/<match>/handoff/ and the Root receives a short summary
# with the file path; the worker reads the file. MTM_COMPACT_ROOT_OUTPUT=0 restores full output.

_BULKY_FIELDS = ("descriptionMd", "description", "start_details", "startInstruction", "files", "companyContext",
                 "company_context", "submitTemplate", "minimumVerificationScenarios", "mvs", "onStartFiles",
                 "releaseFiles", "prior_attempts")
_ENVELOPE_SUMMARY_FIELDS = ("task_id", "task_type", "title", "worker_id", "repository_path", "match_root", "base_revision",
                            "points", "deadline_epoch", "time_budget_seconds", "model", "reasoning_effort", "backend",
                            "lane", "agent_profile", "selected_skills", "allowed_paths",
                            "evidence_required", "attempt_id", "result_contract")


def kb_article_ids(text: str) -> List[str]:
    """KB article ids in order of appearance: KB-12, KB-SUP-9901, KB-BANK-RULE-06 (upper-cased, unique)."""
    out: List[str] = []
    for match in re.findall(r"\bKB-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b", text or "", flags=re.IGNORECASE):
        kb_id = match.upper()
        if kb_id not in out:
            out.append(kb_id)
    return out


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value))[:120] or "item"


# Row fields the Root never needs to decide its next call (paths are resolved by the helper itself).
_ROOT_DROP_FIELDS = set(_BULKY_FIELDS) | {"worktreeRoot", "worktree", "match_root", "claimed_paths", "lease",
                                         "attempt_id", "result", "workerResult", "data", "deadlineEpoch", "deadline_epoch",
                                         "revealedElapsedSec", "deadlineMinutes", "title"}


def _slim_value(v: Any) -> Any:
    if isinstance(v, float):
        return round(v)
    if isinstance(v, dict):
        return {k: _slim_value(x) for k, x in v.items() if x not in (None, [], {}, "")}
    return v


def _slim_row(row: Any) -> Any:
    if not isinstance(row, dict):
        return row
    return {k: _slim_value(v) for k, v in row.items() if k not in _ROOT_DROP_FIELDS and v not in (None, [], {}, "")}


def compact_plan(val: Dict[str, Any]) -> Dict[str, Any]:
    """plan_tasks for the Root: one short row per item, no paths, no duplicated diagnostics, no empty lists.

    Every poll result stays in the Root's context for the rest of the match and is re-read on every later
    model call, so each byte here is paid many times (K2EMUW3I: ~29M cached input tokens on the Root).
    """
    out: Dict[str, Any] = {}
    ready_keys = {r.get("key") for r in val.get("readyToIntegrate") or [] if isinstance(r, dict)}
    for key, value in val.items():
        if key == "active" and isinstance(value, list):
            out["active"] = [f"{r.get('key')}:{r.get('workerId') or r.get('worker_id') or '?'}" if isinstance(r, dict) else r
                             for r in value]
        elif key == "readyDiagnostics" and isinstance(value, list):
            rest = [_slim_row(r) for r in value if not (isinstance(r, dict) and r.get("key") in ready_keys)]
            if rest:
                out[key] = rest
        elif isinstance(value, list):
            if value:
                out[key] = [_slim_row(r) for r in value]
        elif value not in (None, {}, ""):
            out[key] = _slim_value(value)
    return out


def slim_worker_envelope(env: Dict[str, Any], context_dir: Path) -> Dict[str, Any]:
    """The envelope a worker reads first stays in its context for every later turn.

    K2EMUW3I envelopes were 22-135 KB: every delivered file again (already written into the worktree,
    e.g. an 80 KB fixture), and the same KB results twice. The worker now gets the file list and the KB
    results as separate files it opens only when needed.
    """
    slim = json.loads(json.dumps(env))
    meta = slim.setdefault("metadata", {})
    details = meta.get("start_details") if isinstance(meta.get("start_details"), dict) else {}
    files = details.pop("files", None)
    if isinstance(files, dict):
        details["delivered_files"] = sorted(files)
        details["delivered_files_note"] = "Already written into your worktree (match_root) at these relative paths."
    ctx = details.pop("companyContext", None) or meta.pop("company_context", None)
    meta.pop("company_context", None)
    index = []
    if isinstance(ctx, dict):
        context_dir.mkdir(parents=True, exist_ok=True)
        for kind in ("kb", "logs"):
            for n, entry in enumerate(ctx.get(kind) or []):
                if not isinstance(entry, dict):
                    entry = {"result": entry}
                label = _safe_name(str(entry.get("query") or entry.get("tool") or f"{kind}-{n}"))
                target = context_dir / f"{kind}-{n:02d}-{label}.json"
                body = json.dumps(entry, ensure_ascii=False, indent=1)
                target.write_text(body, encoding="utf-8")
                index.append({"kind": kind, "query": entry.get("query"), "path": str(target), "bytes": len(body),
                              **({"error": entry["error"]} if entry.get("error") else {})})
    if index:
        meta["company_context_files"] = index
    for key in ("submit_template",):            # already in start_details.submitTemplate
        if details.get("submitTemplate") is not None:
            meta.pop(key, None)
    if slim.get("submit_template") is not None and details.get("submitTemplate") is not None:
        slim.pop("submit_template", None)
    return slim


def compact_root_output(server: "MCPHelperServer", tool_name: str, args: Dict[str, Any], val: Any) -> Any:
    if os.environ.get("MTM_COMPACT_ROOT_OUTPUT", "1") == "0" or not isinstance(val, dict):
        return val
    try:
        if tool_name == "plan_tasks":
            return compact_plan(val)
        if tool_name == "wait_for_update" and val.get("status") == "EVENT":
            out = {k: v for k, v in val.items() if v not in (None, [], {})}
            if isinstance(out.get("events"), list):
                out["events"] = [{k: e.get(k) for k in ("id", "type", "key") if e.get(k) is not None}
                                 if isinstance(e, dict) else e for e in out["events"]]
            for key in ("deadlineFlushRequests", "workerResultsAvailable", "reconciliationRequired"):
                if isinstance(out.get(key), list):
                    out[key] = [_slim_row(r) for r in out[key]]
            return out
        if tool_name not in ("start_work", "claim_task"):
            return val
        handoff = Path(server.workspace_root) / "team-sdlc-pack" / "state" / _safe_name(server.match_code or "match") / "handoff"
        handoff.mkdir(parents=True, exist_ok=True)
        if tool_name == "start_work":
            key = str(args.get("backlogItemKey", "item"))
            path = handoff / f"{_safe_name(key)}-start.json"
            path.write_text(json.dumps(val, indent=1), encoding="utf-8")
            template = val.get("submitTemplate") if isinstance(val.get("submitTemplate"), dict) else {}
            rows = [r.get("id") for r in template.get("testResults", []) if isinstance(r, dict)]
            ctx = val.get("companyContext") or {}
            return {k: v for k, v in {
                "backlogItemKey": key, "status": "STARTED", "requestId": val.get("requestId") or val.get("request_id"),
                "replay": val.get("replay"), "startDetailsPath": str(path),
                "files": sorted((val.get("files") or {}).keys()) if isinstance(val.get("files"), dict) else None,
                "unavailableAnnouncedFiles": val.get("unavailableAnnouncedFiles"),
                "submitTemplateTestIds": rows or None,
                "submitTemplate": template if template and len(json.dumps(template)) <= 8000 else None,
                "companyContext": {"kb": len(ctx.get("kb") or []), "logs": len(ctx.get("logs") or [])} if ctx else None,
                "note": "Full start details are saved at startDetailsPath and reach the worker through claim_task. Do not read them into the Root unless a decision needs them.",
            }.items() if v is not None}
        # claim_task
        env = val.get("taskEnvelope") if isinstance(val.get("taskEnvelope"), dict) else None
        if env is None:
            return val
        worker_name = _safe_name(val.get('workerId') or args.get('workerId') or 'worker')
        path = handoff / f"{worker_name}-envelope.json"
        (handoff / f"{worker_name}-envelope-raw.json").write_text(json.dumps(env, indent=1), encoding="utf-8")
        path.write_text(json.dumps(slim_worker_envelope(env, handoff / f"{worker_name}-context"), indent=1),
                        encoding="utf-8")
        meta = env.get("metadata") or {}
        summary = {k: env.get(k) for k in _ENVELOPE_SUMMARY_FIELDS if env.get(k) is not None}
        summary["full_envelope_path"] = str(path)
        desc = str(env.get("descriptionMd") or env.get("description") or "")
        if desc:  # a short excerpt so a worker that skips the file still knows the gist
            summary["description_excerpt"] = desc[:1200] + ("... [truncated: read full_envelope_path]" if len(desc) > 1200 else "")
        summary["metadata"] = {k: meta.get(k) for k in ("match_root", "project_runtime_path", "match_id", "worker_id",
                                                         "worker_result_path", "attempt_id", "worker_checkpoint_path")
                               if meta.get(k) is not None}
        if meta.get("company_tools"):
            summary["metadata"]["company_tools"] = [t.get("name") for t in meta["company_tools"] if isinstance(t, dict)]
        return {"workerId": val.get("workerId"), "worktreeRoot": val.get("worktreeRoot"), "matchRoot": val.get("matchRoot"),
                "taskEnvelope": summary, "taskEnvelopePath": str(path),
                "spawnInstruction": ("Spawn the worker with this compact taskEnvelope as-is. Its first step is to read "
                                     "taskEnvelope.full_envelope_path (description, delivered files, submit template, MVS, "
                                     "company context). The Root does not need to read that file.")}
    except Exception as exc:  # never lose a tool result because compaction failed
        sys.stderr.write(f"Warning: compact output for {tool_name} failed: {exc}\n")
        return val

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
                        client_submission_id=tool_args.get("clientSubmissionId") or None,
                        backlog_item_key=tool_args.get("backlogItemKey", ""),
                        git_revision=tool_args.get("gitRevision") or None,
                        answer=tool_args.get("answer"),
                    )
                elif tool_name == "integrate_and_submit":
                    val = server.integrate_and_submit(tool_args["backlogItemKey"])
                elif tool_name == "report_progress":
                    val = server.report_progress(tool_args.get("event", {}))
                elif tool_name == "wait_for_update":
                    val = server.wait_for_update(tool_args.get("timeoutMs", 55000))
                elif tool_name == "plan_tasks":
                    val = server.plan_tasks()
                elif tool_name == "claim_task":
                    val = server.claim_task(tool_args["backlogItemKey"], tool_args["workerId"], tool_args.get("estimatedSeconds", 420))
                elif tool_name == "record_worker_result":
                    val = server.record_worker_result(tool_args["backlogItemKey"], tool_args.get("workerResult"))
                elif tool_name == "integration_gate":
                    val = server.integration_gate(tool_args["backlogItemKey"], tool_args.get("action", "begin"))
                elif tool_name == "reconcile_worker":
                    val = server.reconcile_worker(tool_args["backlogItemKey"], tool_args["confirmedStopped"], tool_args["reason"], tool_args.get("requeue", True))
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

                val = compact_root_output(server, tool_name, tool_args, val)

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
