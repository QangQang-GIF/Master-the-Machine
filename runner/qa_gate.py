"""QA Gate: Deterministic schema and state validation for TaskEnvelope and WorkerResult."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"


class QAGateError(Exception):
    """Raised when an envelope or worker result violates contracts."""
    pass


def _load_schema(name: str) -> Dict[str, Any]:
    schema_path = SCHEMA_DIR / f"{name}.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"Schema not found: {schema_path}")
    return json.loads(schema_path.read_text(encoding="utf-8"))


def validate_schema(data: Dict[str, Any], schema_name: str) -> None:
    """Validate data against JSON schema using jsonschema or fallback basic checks."""
    try:
        import jsonschema
        schema = _load_schema(schema_name)
        jsonschema.validate(instance=data, schema=schema)
    except ImportError:
        # Fallback manual type/field validation when jsonschema is not installed
        _manual_validate(data, schema_name)


def _manual_validate(data: Dict[str, Any], schema_name: str) -> None:
    if schema_name == "task_envelope":
        required = ["task_id", "task_type", "title", "description", "repository_path",
                    "base_revision", "points", "selected_skills", "time_budget_seconds"]
        for k in required:
            if k not in data:
                raise QAGateError(f"TaskEnvelope missing required field: {k}")
        if data["task_type"] not in ("BUG", "CR", "TASK", "PI"):
            raise QAGateError(f"Invalid task_type: {data['task_type']}")

    elif schema_name == "worker_result":
        required = ["task_id", "status", "skill_ids", "changed_files", "test_results",
                    "evidence_paths", "elapsed_seconds"]
        for k in required:
            if k not in data:
                raise QAGateError(f"WorkerResult missing required field: {k}")
        if data["status"] not in ("READY_FOR_REVIEW", "BLOCKED", "TIMED_OUT", "FAILED", "ESCALATE"):
            raise QAGateError(f"Invalid status: {data['status']}")
        for tr in data.get("test_results", []):
            if "command" not in tr or "passed" not in tr:
                raise QAGateError("Each test_result must have 'command' and 'passed' (bool)")


def validate_task_envelope(envelope: Dict[str, Any]) -> None:
    """Validate TaskEnvelope schema and repository availability."""
    validate_schema(envelope, "task_envelope")
    repo_path = Path(envelope["repository_path"])
    if not repo_path.exists():
        raise QAGateError(f"TaskEnvelope repository_path does not exist: {repo_path}")


def validate_worker_result(result: Dict[str, Any], workspace_dir: Optional[Path] = None) -> None:
    """Validate WorkerResult schema and physical evidence/files."""
    validate_schema(result, "worker_result")

    if workspace_dir is not None:
        ws = Path(workspace_dir)
        # Check that changed files actually exist on disk
        for cf in result.get("changed_files", []):
            full_path = ws / cf
            if not full_path.exists():
                raise QAGateError(f"Changed file declared but missing on disk: {cf} ({full_path})")

        # Check evidence files if declared
        for ef in result.get("evidence_paths", []):
            full_path = ws / ef if not Path(ef).is_absolute() else Path(ef)
            if not full_path.exists():
                raise QAGateError(f"Evidence path declared but missing on disk: {ef} ({full_path})")
