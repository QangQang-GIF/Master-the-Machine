"""
runner/telemetry.py: Append-only NDJSON Progress Event Logger.
Conforms strictly to schemas/progress-event.schema.json (Draft 2020-12, schemaVersion: 1,
persistent playerSessionId, additionalProperties: false, all 14 official event types).
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import jsonschema

# Path to the authoritative schema
DEFAULT_SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schemas" / "progress-event.schema.json"

_SCHEMA_CACHE: Optional[Dict[str, Any]] = None
_VALIDATOR_CACHE: Optional[Any] = None
_SCHEMA_LOCK = threading.RLock()


def get_schema(schema_path: Optional[Path | str] = None) -> Dict[str, Any]:
    """Load and cache the progress event schema."""
    global _SCHEMA_CACHE, _VALIDATOR_CACHE
    target_path = Path(schema_path).resolve() if schema_path else DEFAULT_SCHEMA_PATH
    with _SCHEMA_LOCK:
        if _SCHEMA_CACHE is None or schema_path is not None:
            if not target_path.exists():
                raise FileNotFoundError(f"Progress event schema not found at {target_path}")
            loaded = json.loads(target_path.read_text(encoding="utf-8"))
            if schema_path is None:
                _SCHEMA_CACHE = loaded
                validator_cls = jsonschema.validators.validator_for(loaded)
                _VALIDATOR_CACHE = validator_cls(loaded)
                return _SCHEMA_CACHE
            return loaded
        return _SCHEMA_CACHE


def get_validator(schema_path: Optional[Path | str] = None) -> Any:
    """Return a compiled jsonschema validator."""
    global _VALIDATOR_CACHE
    if schema_path is not None:
        schema = get_schema(schema_path)
        validator_cls = jsonschema.validators.validator_for(schema)
        return validator_cls(schema)
    with _SCHEMA_LOCK:
        if _VALIDATOR_CACHE is None:
            schema = get_schema()
            validator_cls = jsonschema.validators.validator_for(schema)
            _VALIDATOR_CACHE = validator_cls(schema)
        return _VALIDATOR_CACHE


def validate_progress_event(event: Dict[str, Any], schema_path: Optional[Path | str] = None) -> None:
    """Validate a single event object strictly against progress-event.schema.json."""
    validator = get_validator(schema_path)
    validator.validate(event)


def clean_summary(text: str, max_len: int = 240) -> str:
    """Sanitize summary string to adhere to schema pattern ^[^\r\n\u0000]+$ and maxLength 240."""
    if not text:
        return "Progress update"
    cleaned = re.sub(r"[\r\n\u0000]+", " ", str(text)).strip()
    if not cleaned:
        cleaned = "Progress update"
    return cleaned[:max_len]


class TelemetryLogger:
    """
    Append-only NDJSON logger that records events strictly validated against
    schemas/progress-event.schema.json, maintaining a consistent playerSessionId.
    """

    def __init__(
        self,
        log_path: Path | str,
        player_session_id: Optional[str] = None,
        match_code: Optional[str] = None,
        schema_path: Optional[Path | str] = None,
    ) -> None:
        target = Path(log_path)
        if target.is_dir() or (not target.suffix and match_code):
            target = target / f"{match_code}.progress.ndjson"
        self.log_file = target.resolve()
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # Persistent playerSessionId across all events logged by this instance
        if player_session_id:
            cleaned_session = str(player_session_id).strip()
            if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$", cleaned_session):
                raise ValueError(f"Invalid playerSessionId format: {cleaned_session}")
            self.player_session_id = cleaned_session
        else:
            self.player_session_id = str(uuid.uuid4())

        self.match_code = match_code
        self.schema_path = schema_path
        self._lock = threading.Lock()
        self._events_written = 0

    @property
    def events_written_count(self) -> int:
        return self._events_written

    def log_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Populate mandatory envelope fields (schemaVersion, playerSessionId),
        validate against schema, and append as a single NDJSON line.
        """
        event = dict(event_data)
        event.setdefault("schemaVersion", 1)
        event.setdefault("playerSessionId", self.player_session_id)

        # Strict validation
        validate_progress_event(event, self.schema_path)

        line = json.dumps(event, ensure_ascii=False) + "\n"
        with self._lock:
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError:
                    pass
            self._events_written += 1

        return event

    # --- Factory emitter methods for all 14 official schema event types ---

    def emit_orchestrator_started(self, summary: str = "Team Orchestrator started") -> Dict[str, Any]:
        return self.log_event({
            "type": "ORCHESTRATOR_STARTED",
            "summary": clean_summary(summary),
        })

    def emit_orchestrator_restarted(
        self,
        attempt: int,
        phase: str,
        reason: str,
        summary: str = "Team Orchestrator restarted",
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "ORCHESTRATOR_RESTARTED",
            "attempt": int(attempt),
            "phase": phase,
            "reason": reason,
            "summary": clean_summary(summary),
        })

    def emit_preparation_status(
        self,
        stage: str,
        status: str,
        summary: str,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "PREPARATION_STATUS",
            "stage": stage,
            "status": status,
            "summary": clean_summary(summary),
        })

    def emit_environment_ready(
        self,
        request_id: str,
        repo_url: str,
        branch: str,
        folder: str,
        revision: str,
        build: str = "PASS",
        match_login: str = "PASS",
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "ENVIRONMENT_READY",
            "requestId": request_id,
            "repository": {
                "url": repo_url,
                "branch": branch,
                "folder": folder,
            },
            "revision": revision,
            "build": build,
            "matchLogin": match_login,
        })

    def emit_game_start_status(
        self,
        stage: str = "BACKLOG_SYNC",
        status: str = "COMPLETED",
        summary: str = "Backlog sync completed",
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "GAME_START_STATUS",
            "stage": stage,
            "status": status,
            "summary": clean_summary(summary),
        })

    def emit_backlog_status(
        self,
        backlog_id: str,
        title: str,
        status: str,
        summary: str,
        revision: Optional[str] = None,
        receipt_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "BACKLOG_STATUS",
            "backlogId": backlog_id,
            "title": title[:160],
            "status": status,
            "summary": clean_summary(summary),
            "revision": revision,
            "receiptId": receipt_id,
        })

    def emit_backlog_tests(
        self,
        backlog_id: str,
        status: str,
        required: int,
        executed: int,
        passed: int,
        failed: int,
        skipped: int,
        summary: str,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "BACKLOG_TESTS",
            "backlogId": backlog_id,
            "status": status,
            "required": int(required),
            "executed": int(executed),
            "passed": int(passed),
            "failed": int(failed),
            "skipped": int(skipped),
            "summary": clean_summary(summary),
        })

    def emit_game_master_api(
        self,
        operation: str,
        phase: str,
        summary: str,
        backlog_id: Optional[str] = None,
        method: Optional[str] = None,
        origin: Optional[str] = None,
        path: Optional[str] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "GAME_MASTER_API",
            "operation": operation,
            "backlogId": backlog_id,
            "method": method,
            "origin": origin,
            "path": path,
            "phase": phase,
            "httpStatus": http_status,
            "requestId": request_id,
            "summary": clean_summary(summary),
        })

    def emit_model_usage(
        self,
        model: str,
        input_tokens: int,
        cached_input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        summary: str = "Model tokens accounted",
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "MODEL_USAGE",
            "model": model[:128],
            "inputTokens": int(input_tokens),
            "cachedInputTokens": int(cached_input_tokens),
            "outputTokens": int(output_tokens),
            "reasoningTokens": int(reasoning_tokens),
            "summary": clean_summary(summary),
        })

    def emit_tool_activity(
        self,
        tool_name: str,
        operation: str,
        phase: str,
        summary: str,
        backlog_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "TOOL_ACTIVITY",
            "toolName": tool_name,
            "operation": operation,
            "backlogId": backlog_id,
            "phase": phase,
            "summary": clean_summary(summary),
        })

    def emit_network_activity(
        self,
        operation: str,
        method: str,
        origin: str,
        path: str,
        phase: str,
        summary: str,
        backlog_id: Optional[str] = None,
        http_status: Optional[int] = None,
        request_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "NETWORK_ACTIVITY",
            "operation": operation,
            "backlogId": backlog_id,
            "method": method,
            "origin": origin,
            "path": path,
            "phase": phase,
            "httpStatus": http_status,
            "requestId": request_id,
            "summary": clean_summary(summary),
        })

    def emit_blocker_opened(
        self,
        blocker_id: str,
        summary: str,
        backlog_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "BLOCKER_OPENED",
            "blockerId": blocker_id,
            "backlogId": backlog_id,
            "summary": clean_summary(summary),
        })

    def emit_blocker_resolved(
        self,
        blocker_id: str,
        summary: str,
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "BLOCKER_RESOLVED",
            "blockerId": blocker_id,
            "summary": clean_summary(summary),
        })

    def emit_orchestrator_ended(
        self,
        status: str,
        summary: str = "Match concluded",
    ) -> Dict[str, Any]:
        return self.log_event({
            "type": "ORCHESTRATOR_ENDED",
            "status": status,
            "summary": clean_summary(summary),
        })


_GLOBAL_LOGGER: Optional[TelemetryLogger] = None
_GLOBAL_LOCK = threading.Lock()


def get_telemetry_logger(
    log_path: Optional[Path | str] = None,
    player_session_id: Optional[str] = None,
    match_code: Optional[str] = None,
) -> TelemetryLogger:
    """Retrieve or create a singleton TelemetryLogger instance."""
    global _GLOBAL_LOGGER
    with _GLOBAL_LOCK:
        if _GLOBAL_LOGGER is None or log_path is not None:
            effective_path = log_path or os.environ.get("MTM_PROGRESS_LOG_FILE") or "match.progress.ndjson"
            _GLOBAL_LOGGER = TelemetryLogger(
                log_path=effective_path,
                player_session_id=player_session_id or os.environ.get("MTM_PLAYER_SESSION_ID"),
                match_code=match_code or os.environ.get("MTM_MATCH_CODE"),
            )
        return _GLOBAL_LOGGER
