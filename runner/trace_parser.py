"""JSONL Execution Trace Parser for Codex CLI and Antigravity."""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class ExecutionTrace:
    raw_lines: List[str] = field(default_factory=list)
    turns: int = 0
    commands: List[Dict[str, Any]] = field(default_factory=list)
    file_changes: int = 0
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    last_message: str = ""
    structured_result: Optional[Dict[str, Any]] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    @property
    def billable_tokens(self) -> int:
        # Billable tokens exclude cached input tokens
        uncached_input = max(0, self.input_tokens - self.cached_input_tokens)
        return uncached_input + self.output_tokens


def parse_codex_trace(content_or_lines: str | List[str]) -> ExecutionTrace:
    """Parse Codex CLI JSONL output stream."""
    lines = content_or_lines if isinstance(content_or_lines, list) else content_or_lines.splitlines()
    trace = ExecutionTrace(raw_lines=lines)

    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue
        try:
            ev = json.loads(line_str)
        except Exception:
            continue

        ev_type = ev.get("type")
        if ev_type == "turn.completed":
            trace.turns += 1
            usage = ev.get("usage", {})
            trace.input_tokens += int(usage.get("input_tokens") or 0)
            trace.cached_input_tokens += int(usage.get("cached_input_tokens") or 0)
            trace.output_tokens += int(usage.get("output_tokens") or 0)
            trace.reasoning_output_tokens += int(usage.get("reasoning_output_tokens") or 0)

        elif ev_type == "item.completed":
            item = ev.get("item", {})
            item_type = item.get("type")
            if item_type == "command_execution":
                trace.commands.append({
                    "command": item.get("command", ""),
                    "exit_code": item.get("exit_code"),
                    "output": item.get("output", "")
                })
            elif item_type == "file_change":
                trace.file_changes += 1
            elif item_type == "message" and item.get("role") == "assistant":
                trace.last_message = item.get("text", "")

    return trace


def parse_agy_trace(content_or_lines: str | List[str]) -> ExecutionTrace:
    """Parse Antigravity CLI JSON/NDJSON output stream."""
    lines = content_or_lines if isinstance(content_or_lines, list) else content_or_lines.splitlines()
    trace = ExecutionTrace(raw_lines=lines)

    for line in reversed(lines):
        line_str = line.strip()
        if not (line_str.startswith("{") and line_str.endswith("}")):
            continue
        try:
            data = json.loads(line_str)
            if "usage" in data or "response" in data:
                u = data.get("usage", {})
                trace.input_tokens = int(u.get("input_tokens") or 0)
                trace.cached_input_tokens = int(u.get("cache_read_tokens") or 0)
                trace.output_tokens = int(u.get("output_tokens") or 0)
                trace.reasoning_output_tokens = int(u.get("thinking_tokens") or 0)
                trace.turns = int(data.get("num_turns") or 1)
                trace.last_message = str(data.get("response") or "")
                break
        except Exception:
            continue

    return trace


def load_trace_file(path: str | Path) -> ExecutionTrace:
    p = Path(path)
    if not p.exists():
        return ExecutionTrace()
    content = p.read_text(encoding="utf-8", errors="replace")
    # Detect Codex JSONL vs agy
    if '"turn.completed"' in content or '"item.completed"' in content:
        return parse_codex_trace(content)
    return parse_agy_trace(content)
