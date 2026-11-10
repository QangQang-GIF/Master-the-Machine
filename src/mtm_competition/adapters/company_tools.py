"""
Company Tools Adapter: Bridge to the official Match MCP server.
Handles tools/list discovery, role categorization (kb, qa_data, logs, other),
JSON-RPC tool execution over HTTP, caching, and audit logging.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def categorize_tool(name: str, description: str = "") -> str:
    """Categorize a company tool into kb, qa_data, logs, or other based on semantic indicators."""
    text = f"{name.lower()} {description.lower()}"

    # QA Data / Fixtures
    qa_keywords = ["qa", "fixture", "test_data", "test-data", "test_store", "dataset", "mock", "sample"]
    if any(k in text for k in qa_keywords):
        return "qa_data"

    # Knowledge Base / Standards / Docs
    kb_keywords = ["kb", "knowledge", "doc", "standard", "manual", "wiki", "guide", "rule", "policy"]
    if any(k in text for k in kb_keywords):
        return "kb"

    # Observability / Logs / Traces
    log_keywords = ["log", "trace", "observability", "metrics", "monitor", "telemetry"]
    if any(k in text for k in log_keywords):
        return "logs"

    return "other"


class CompanyToolsClient:
    """Client for Match MCP HTTP endpoint."""

    def __init__(
        self,
        base_url: str,
        match_id: str,
        api_key: str,
        timeout: float = 10.0,
        state_dir: Optional[Path] = None,
    ) -> None:
        self.match_id = match_id
        self.api_key = api_key
        self.timeout = timeout
        self.state_dir = Path(state_dir) if state_dir else None

        # Build endpoint: https://masterthemachine.tech/api/v1/mcp/<MatchID>
        clean_base = base_url.rstrip("/")
        if "/api/v1/mcp" in clean_base:
            self.endpoint = f"{clean_base}/{match_id}" if not clean_base.endswith(match_id) else clean_base
        else:
            self.endpoint = f"{clean_base}/api/v1/mcp/{match_id}"

        self._tools: List[Dict[str, Any]] = []
        self._cache: Dict[str, Any] = {}
        self._rpc_id = 0
        self._mcp_session_id: Optional[str] = None

    def _next_rpc_id(self) -> int:
        self._rpc_id += 1
        return self._rpc_id

    @staticmethod
    def _parse_rpc_response(raw_text: str) -> Dict[str, Any]:
        """Parse either standard application/json or text/event-stream SSE JSON-RPC response."""
        text = raw_text.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        last_obj: Dict[str, Any] = {}
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("data:"):
                payload_str = stripped[5:].strip()
                if not payload_str or payload_str == "[DONE]":
                    continue
                try:
                    candidate = json.loads(payload_str)
                    if isinstance(candidate, dict):
                        last_obj = candidate
                        if "result" in candidate or "error" in candidate:
                            return candidate
                except Exception:
                    continue
        if last_obj:
            return last_obj
        raise ValueError(f"Unable to parse Match MCP response: {text[:300]}")

    def _send_rpc(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send a JSON-RPC request, retrying transient failures (timeout, connection error, 429, 5xx).

        K2EMUW3I: 15 of 44 company-tool calls and 3 of 7 log collections failed with "The read operation
        timed out" and the same call succeeded seconds later. Each failure that reaches the Root costs a full
        model turn, so bounded retries happen here. Business errors are never retried.
        """
        retries = max(0, int(os.environ.get("MTM_COMPANY_TOOL_RETRIES", "2")))
        delay = 0.5
        for attempt in range(retries + 1):
            try:
                return self._send_rpc_once(method, params)
            except RuntimeError as exc:
                if attempt >= retries or not self._is_transient(exc):
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")

    @staticmethod
    def _is_transient(exc: BaseException) -> bool:
        text = str(exc).lower()
        if "timed out" in text or "timeout" in text or "connection error" in text or "temporarily" in text:
            return True
        import re as _re
        match = _re.search(r"match mcp http (\d{3})", text)
        return bool(match and (match.group(1) == "429" or match.group(1).startswith("5")))

    def _send_rpc_once(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Send JSON-RPC request to Match MCP endpoint."""
        rpc_payload = {
            "jsonrpc": "2.0",
            "id": self._next_rpc_id(),
            "method": method,
            "params": params or {},
        }
        data = json.dumps(rpc_payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self.api_key}",
            "User-Agent": "MTM-Player-TeamBridge/1.0",
        }
        if self._mcp_session_id:
            headers["Mcp-Session-Id"] = self._mcp_session_id

        req = urllib.request.Request(
            self.endpoint,
            data=data,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if hasattr(resp, "headers") and resp.headers:
                    sid = resp.headers.get("Mcp-Session-Id") or resp.headers.get("mcp-session-id")
                    if sid:
                        self._mcp_session_id = sid
                resp_bytes = resp.read()
                return self._parse_rpc_response(resp_bytes.decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Match MCP HTTP {exc.code} {exc.reason}: {err_body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Match MCP connection error: {exc.reason}") from exc
        except (TimeoutError, OSError) as exc:   # socket read timeouts surface here, not as URLError
            raise RuntimeError(f"Match MCP connection error: {exc}") from exc

    def discover_tools(self) -> List[Dict[str, Any]]:
        """Query tools/list from Match MCP and categorize tools."""
        res = self._send_rpc("tools/list")
        if "error" in res:
            err = res["error"]
            raise RuntimeError(f"tools/list failed: {err.get('message', err)}")

        raw_tools = res.get("result", {}).get("tools", [])
        tools = []
        categories: Dict[str, List[str]] = {"kb": [], "qa_data": [], "logs": [], "other": []}

        for t in raw_tools:
            name = t.get("name", "")
            desc = t.get("description", "")
            cat = categorize_tool(name, desc)
            tool_entry = {
                "name": name,
                "description": desc,
                "inputSchema": t.get("inputSchema", {}),
                "category": cat,
            }
            tools.append(tool_entry)
            categories[cat].append(name)

        self._tools = tools

        # Persist to state_dir if configured
        if self.state_dir:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            output_file = self.state_dir / "company-tools.json"
            catalog = {
                "matchId": self.match_id,
                "updatedAt": datetime.now(timezone.utc).isoformat(),
                "endpoint": self.endpoint,
                "categories": categories,
                "tools": tools,
            }
            output_file.write_text(json.dumps(catalog, indent=2), encoding="utf-8")

        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Any:
        """Call a company tool with JSON arguments, caching, and audit logging."""
        cache_key = hashlib.sha256(f"{name}:{json.dumps(arguments, sort_keys=True)}".encode("utf-8")).hexdigest()
        if cache_key in self._cache:
            return self._cache[cache_key]

        start_time = time.time()
        res = self._send_rpc("tools/call", {"name": name, "arguments": arguments})
        duration_ms = int((time.time() - start_time) * 1000)

        if "error" in res:
            err = res["error"]
            raise RuntimeError(f"Tool '{name}' error: {err.get('message', err)}")

        result_obj = res.get("result", {})
        if result_obj.get("isError"):
            err_info = result_obj.get("error", {})
            err_msg = err_info.get("message") if isinstance(err_info, dict) else str(err_info)
            raise RuntimeError(f"Tool '{name}' returned business error: {err_msg or result_obj}")

        # Extract content (unwrap up to 2 nested MCP text envelopes if present)
        parsed_result: Any = result_obj
        for _ in range(2):
            if isinstance(parsed_result, dict) and isinstance(parsed_result.get("content"), list) and parsed_result["content"]:
                first = parsed_result["content"][0]
                if isinstance(first, dict) and first.get("type") == "text" and "text" in first:
                    text_val = first.get("text", "")
                    try:
                        parsed_result = json.loads(text_val)
                    except Exception:
                        parsed_result = text_val
                        break
                else:
                    break
            else:
                break

        # Calculate response hash
        res_hash = hashlib.sha256(json.dumps(parsed_result, sort_keys=True, default=str).encode("utf-8")).hexdigest()

        # Audit log
        if self.state_dir:
            self.state_dir.mkdir(parents=True, exist_ok=True)
            audit_file = self.state_dir / "company-tool-calls.jsonl"
            audit_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "tool": name,
                "arguments": arguments,
                "sha256": res_hash,
                "duration_ms": duration_ms,
            }
            with open(audit_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(audit_entry) + "\n")

        self._cache[cache_key] = parsed_result
        return parsed_result
