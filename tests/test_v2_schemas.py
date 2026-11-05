"""V2 contracts validate against their JSON schemas."""
import json
import subprocess
from pathlib import Path

import jsonschema

from mtm_competition.scheduling.deadline_policy import build_control_instruction
from mtm_competition.scheduling.native import NativeTaskCoordinator
from tests.test_v2_phase5_heavy_plan import BASE

SCHEMAS = Path(__file__).resolve().parents[1] / "schemas"


def load(name):
    return json.loads((SCHEMAS / name).read_text(encoding="utf-8"))


def test_all_schema_files_are_valid_json_schemas():
    for f in SCHEMAS.glob("*.json"):
        jsonschema.Draft7Validator.check_schema(json.loads(f.read_text(encoding="utf-8")))


def test_heavy_plan_example_validates_and_bad_plans_do_not():
    jsonschema.validate(BASE, load("heavy_execution_plan.json"))
    bad = json.loads(json.dumps(BASE))
    bad["subtasks"][0]["ownedPaths"] = []
    try:
        jsonschema.validate(bad, load("heavy_execution_plan.json"))
        raise AssertionError("empty ownedPaths must be rejected")
    except jsonschema.ValidationError:
        pass


def test_control_instruction_and_stop_ack_validate():
    for state in ("DEADLINE_WARNING", "CODE_FREEZE", "EVIDENCE_PRIORITY", "HANDOFF", "FINALIZATION"):
        jsonschema.validate(build_control_instruction("BUG-04", 123.0, state, f"ctl:BUG-04:1:{state}"), load("control_instruction.json"))
    jsonschema.validate({"instructionId": "stop:BUG-04:1"}, load("stop_ack.json"))
    try:
        jsonschema.validate({"instructionId": "other"}, load("stop_ack.json"))
        raise AssertionError("ack must carry a stop: instruction id")
    except jsonschema.ValidationError:
        pass


def test_claim_envelope_with_model_selection_still_matches_task_envelope_schema(tmp_path):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=5,
                              normal_workers=4, reserved_workers=1, model_routing=True)
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400,
                           "items": [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10}]})
    c.register_started("BUG-01", {})
    env = c.claim_task("BUG-01", "w1")["taskEnvelope"]
    jsonschema.validate({k: v for k, v in env.items() if v is not None}, load("task_envelope.json"))
    assert env["model_selection"]["actualModel"] is None
