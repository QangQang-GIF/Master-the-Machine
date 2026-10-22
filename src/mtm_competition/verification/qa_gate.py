"""QA Gate: Deterministic schema and state validation for TaskEnvelope and WorkerResult."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

SCHEMA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "schemas"


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
        ws = Path(workspace_dir).resolve()
        # Check that changed files do not escape workspace and actually exist on disk
        for cf in result.get("changed_files", []):
            try:
                cf_path = (ws / cf).resolve()
                cf_path.relative_to(ws)
            except (ValueError, RuntimeError):
                raise QAGateError(f"Path escape detected: '{cf}' escapes workspace {ws}")
            if not cf_path.exists():
                raise QAGateError(f"Changed file declared but missing on disk: {cf} ({cf_path})")

        # Check evidence files if declared
        for ef in result.get("evidence_paths", []):
            try:
                ef_path = (ws / ef).resolve() if not Path(ef).is_absolute() else Path(ef).resolve()
                if not Path(ef).is_absolute():
                    ef_path.relative_to(ws)
            except (ValueError, RuntimeError):
                raise QAGateError(f"Path escape detected: evidence '{ef}' escapes workspace {ws}")
            if not ef_path.exists():
                raise QAGateError(f"Evidence path declared but missing on disk: {ef} ({ef_path})")


def validate_qa_gate(
    envelope: Dict[str, Any],
    result: Dict[str, Any],
    workspace_dir: Optional[Path] = None,
) -> None:
    """Rigorous QA Gate: Cross-check TaskEnvelope against WorkerResult.

    Validates:
    1. Schema conformance of both envelope and result.
    2. Exact task_id match.
    3. Path escape prevention and allowed_paths containment.
    4. Physical existence of all declared changed files.
    5. Mandatory presence and existence of all required evidence files.
    6. Base revision consistency.
    7. Test verification: READY_FOR_REVIEW status requires all executed tests to pass.
    """
    validate_task_envelope(envelope)
    validate_worker_result(result, workspace_dir=workspace_dir)

    ws = Path(workspace_dir or envelope["repository_path"]).resolve()

    # 1. Exact task_id match
    if result["task_id"] != envelope["task_id"]:
        raise QAGateError(
            f"Task ID mismatch: envelope has {envelope['task_id']}, result has {result['task_id']}"
        )

    # 2. Path escape prevention
    for cf in result.get("changed_files", []):
        try:
            cf_path = (ws / cf).resolve()
            cf_path.relative_to(ws)
        except (ValueError, RuntimeError):
            raise QAGateError(f"Path escape detected: '{cf}' escapes workspace {ws}")

    # 3. Allowed paths containment
    allowed_paths = envelope.get("allowed_paths") or envelope.get("limits", {}).get("allowed_paths")
    if allowed_paths:
        allowed_resolved = [Path(p).resolve() for p in allowed_paths]
        for cf in result.get("changed_files", []):
            cf_path = (ws / cf).resolve()
            if not any(cf_path == ap or ap in cf_path.parents for ap in allowed_resolved):
                raise QAGateError(f"File '{cf}' is outside declared allowed_paths: {allowed_paths}")

    # 4. Mandatory evidence requirements
    required_evidence = envelope.get("evidence_required", [])
    result_evidence = result.get("evidence_paths", [])
    for req_ev in required_evidence:
        if req_ev not in result_evidence:
            raise QAGateError(f"Required evidence missing from WorkerResult: '{req_ev}'")
        ev_path = ws / req_ev if not Path(req_ev).is_absolute() else Path(req_ev)
        if not ev_path.exists():
            raise QAGateError(f"Required evidence missing on disk: '{req_ev}' ({ev_path})")

    # 5. Base revision consistency
    env_base = envelope.get("base_revision")
    res_base = result.get("base_revision")
    if env_base and res_base and env_base not in ("HEAD", None):
        if not res_base.startswith(env_base) and not env_base.startswith(res_base):
            raise QAGateError(
                f"Base revision mismatch: envelope expected {env_base}, result recorded {res_base}"
            )

    # 6. Status and test consistency
    if result["status"] == "READY_FOR_REVIEW":
        for tr in result.get("test_results", []):
            if not tr.get("passed"):
                raise QAGateError(
                    f"Status is READY_FOR_REVIEW but test failed: {tr.get('command')}"
                )
