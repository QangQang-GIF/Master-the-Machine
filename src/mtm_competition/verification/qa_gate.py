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
    except jsonschema.exceptions.ValidationError as exc:
        raise QAGateError(f"Schema validation failed for {schema_name}: {exc.message}") from exc
    except jsonschema.exceptions.SchemaError as exc:
        raise QAGateError(f"Invalid JSON schema {schema_name}: {exc.message}") from exc


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


def map_mvs_to_test_command(mvs_item: Dict[str, Any] | str) -> str:
    """Map an MVS declaration from Game Master to an executable test command."""
    if isinstance(mvs_item, str):
        desc = mvs_item
        cmd = None
    else:
        desc = mvs_item.get("description", "")
        cmd = mvs_item.get("command")

    if cmd:
        return cmd
    desc = desc.strip()
    if desc.startswith("pytest ") or desc.startswith("python "):
        return desc
    if desc.startswith("test_") or "::test_" in desc:
        return f"pytest -k {desc}"
    return f"pytest -k {desc}" if desc else "pytest"


def validate_qa_gate(
    envelope: Dict[str, Any],
    result: Dict[str, Any],
    workspace_dir: Optional[Path] = None,
    reverify_tests: bool = False,
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
    8. Optional independent test execution without blindly trusting worker result JSON.
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
        task_type = str(envelope.get("task_type", "BUG")).upper()
        test_results = result.get("test_results", [])
        if task_type in ("CR", "BUG", "PI") and not test_results:
            raise QAGateError(
                f"Task {envelope.get('task_id')} of type '{task_type}' requires test verification, but test_results is empty"
            )
        for tr in test_results:
            if not tr.get("passed"):
                raise QAGateError(
                    f"Status is READY_FOR_REVIEW but test failed: {tr.get('command')}"
                )

        # 7. Mandatory Minimum Viable Solution (MVS) Enforcement
        mvs_list = envelope.get("mvs") or []
        if mvs_list:
            for mvs in mvs_list:
                mvs_id = mvs.get("id") if isinstance(mvs, dict) else str(mvs)
                mvs_desc = mvs.get("description", "") if isinstance(mvs, dict) else str(mvs)
                mvs_cmd = map_mvs_to_test_command(mvs)

                matched = False
                for tr in test_results:
                    if not tr.get("passed"):
                        continue
                    tr_cmd = tr.get("command", "")
                    tr_mvs = tr.get("mvs_id")
                    if tr_mvs and tr_mvs == mvs_id:
                        matched = True
                        break
                    if mvs_id and mvs_id in tr_cmd:
                        matched = True
                        break
                    if mvs_desc and mvs_desc in tr_cmd:
                        matched = True
                        break
                    if mvs_cmd and (mvs_cmd == tr_cmd or mvs_cmd in tr_cmd):
                        matched = True
                        break
                    if tr_cmd.strip() in ("pytest", "python -m pytest", "pytest -v", "pytest -q"):
                        matched = True
                        break

                if not matched and reverify_tests and ws.exists():
                    if reverify_tests_independently(ws, [mvs_cmd]):
                        matched = True

                if not matched:
                    raise QAGateError(
                        f"Mandatory MVS requirement '{mvs_id}' ({mvs_desc}) was not verified or failed"
                    )

        # 8. Independent test execution
        if reverify_tests and test_results:
            test_cmds = [tr.get("command") for tr in test_results if tr.get("command")]
            env_cmd = envelope.get("verification_cmd")
            if env_cmd and env_cmd not in test_cmds:
                test_cmds.append(env_cmd)
            for mvs in mvs_list:
                m_cmd = map_mvs_to_test_command(mvs)
                if m_cmd and m_cmd not in test_cmds:
                    test_cmds.append(m_cmd)
            if test_cmds and ws.exists():
                if not reverify_tests_independently(ws, test_cmds):
                    raise QAGateError(
                        f"Independent test re-verification failed for commands: {test_cmds}"
                    )


def reverify_tests_independently(
    workspace_dir: Path | str,
    commands: List[str],
    timeout: int = 120,
    log_file: Optional[Path | str] = None,
) -> bool:
    """Independently re-run test commands to verify worker's reported claims."""
    import subprocess
    from .command_safety import run_verification_command
    ws = Path(workspace_dir).resolve()
    logs = []
    for cmd in commands:
        try:
            proc = run_verification_command(
                cmd,
                cwd=ws,
                timeout=timeout,
            )
            logs.append(f"$ {cmd}\nexit: {proc.returncode}\n{proc.stdout}\n{proc.stderr}")
            if proc.returncode != 0:
                if log_file:
                    Path(log_file).write_text("\n".join(logs), encoding="utf-8")
                return False
        except subprocess.TimeoutExpired:
            logs.append(f"$ {cmd}\nTIMED OUT after {timeout}s")
            if log_file:
                Path(log_file).write_text("\n".join(logs), encoding="utf-8")
            return False
        except Exception as e:
            logs.append(f"$ {cmd}\nEXCEPTION: {e}")
            if log_file:
                Path(log_file).write_text("\n".join(logs), encoding="utf-8")
            return False
    if log_file and logs:
        Path(log_file).write_text("\n".join(logs), encoding="utf-8")
    return True
