"""
Telemetry NDJSON Schema Validator Fixture.
Validates progress events written to <matchCode>.progress.ndjson against schemas/progress-event.schema.json.
Enforces Draft 2020-12 / additionalProperties: false schema validation and persistent playerSessionId tracking.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jsonschema
import pytest

SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "schemas" / "progress-event.schema.json"


def load_progress_schema() -> Dict[str, Any]:
    """Load and return the official progress event JSON schema."""
    if not SCHEMA_PATH.exists():
        raise FileNotFoundError(f"Progress event schema not found at {SCHEMA_PATH}")
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


_CACHED_SCHEMA: Optional[Dict[str, Any]] = None


def get_cached_schema() -> Dict[str, Any]:
    global _CACHED_SCHEMA
    if _CACHED_SCHEMA is None:
        _CACHED_SCHEMA = load_progress_schema()
    return _CACHED_SCHEMA


def validate_event_object(event: Dict[str, Any], schema: Optional[Dict[str, Any]] = None) -> None:
    """Validate a single event dictionary against the progress event schema."""
    target_schema = schema or get_cached_schema()
    # jsonschema draft 2020-12 validator
    validator_cls = jsonschema.validators.validator_for(target_schema)
    validator = validator_cls(target_schema)
    validator.validate(event)


def validate_progress_file(
    file_path: Path,
    expected_session_id: Optional[str] = None,
    expected_types: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Validate an NDJSON progress file:
    1. File exists and is non-empty.
    2. Each line is valid JSON.
    3. Each JSON object validates against schemas/progress-event.schema.json.
    4. If expected_session_id is provided, ensures all events share that exact playerSessionId.
    5. Returns the list of parsed events.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"Progress log file does not exist: {file_path}")

    content = file_path.read_text(encoding="utf-8").strip()
    if not content:
        raise ValueError(f"Progress log file is empty: {file_path}")

    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        raise ValueError(f"Progress log contains no valid lines: {file_path}")

    schema = get_cached_schema()
    validator_cls = jsonschema.validators.validator_for(schema)
    validator = validator_cls(schema)

    events: List[Dict[str, Any]] = []
    seen_session_id: Optional[str] = None

    for idx, line in enumerate(lines, start=1):
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Line {idx} in {file_path} is not valid JSON: {exc}") from exc

        try:
            validator.validate(event)
        except jsonschema.ValidationError as exc:
            raise jsonschema.ValidationError(
                f"Line {idx} failed schema validation: {exc.message} at path {list(exc.path)}"
            ) from exc

        session_id = event.get("playerSessionId")
        if expected_session_id and session_id != expected_session_id:
            raise ValueError(
                f"Line {idx} playerSessionId '{session_id}' does not match expected '{expected_session_id}'"
            )
        if seen_session_id is None:
            seen_session_id = session_id
        elif seen_session_id != session_id:
            raise ValueError(
                f"Line {idx} playerSessionId '{session_id}' diverged from file session '{seen_session_id}'"
            )

        events.append(event)

    if expected_types:
        actual_types = [e.get("type") for e in events]
        for exp_type in expected_types:
            if exp_type not in actual_types:
                raise AssertionError(f"Expected event type '{exp_type}' not found in {file_path}")

    return events


@pytest.fixture
def validate_progress_ndjson() -> Callable[..., List[Dict[str, Any]]]:
    """Pytest fixture returning the progress file validator function."""
    return validate_progress_file
