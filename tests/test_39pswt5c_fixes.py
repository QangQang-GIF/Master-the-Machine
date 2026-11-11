"""39PSWT5C generalized fixes.

Layer 1: an item whose announced inputs are odd (another item's file, binary, protected) still starts and is claimed.
Layer 2: submit.json is checked against challenge/<key>-submit.schema.json before SUBMIT.
"""
import json
from pathlib import Path

import pytest

from tests.test_credit_optimizations import Board, finish_worker, git, native_server  # noqa: F401

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["status", "testResults", "evidenceFiles", "auditSummary"],
    "properties": {
        "status": {"enum": ["DONE", "PARTIAL", "BLOCKED"]},
        "testResults": {"type": "array"},
        "evidenceFiles": {"type": "array", "items": {"type": "string"}},
        "auditSummary": {"type": "object", "required": ["totalRecords", "anomalies"],
                         "properties": {"totalRecords": {"type": "integer", "minimum": 0},
                                        "anomalies": {"type": "array", "minItems": 1, "items": {"type": "string"}}}},
    },
}


def publish(server, files):
    for rel, content in files.items():
        path = server.match_root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        (path.write_bytes if isinstance(content, bytes) else path.write_text)(content)
    git(server.checkout_root, "add", "-A")
    git(server.checkout_root, "commit", "-m", "publish inputs")
    git(server.checkout_root, "push", "origin", "main")


def test_schema_invalid_answer_is_refused_with_exact_errors_then_autofilled_on_request(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(SCHEMA)})
    finish_worker(server)
    server.record_worker_result("TASK-01")
    with pytest.raises(ValueError, match="SUBMIT_SCHEMA_INVALID") as info:
        server.integrate_and_submit("TASK-01")
    assert "auditSummary" in str(info.value) and not server.adapter.calls
    out = server.integrate_and_submit("TASK-01", schema_autofill=True)
    assert out["status"] == "ACCEPTED"
    sent = server.adapter.calls[-1]["answer"]
    assert sent["auditSummary"]["totalRecords"] == 0 and len(sent["auditSummary"]["anomalies"]) == 1
    assert sent["status"] == "PARTIAL"
    committed = json.loads(git(server.checkout_root, "show", f"{out['gitRevision']}:project/answers/TASK-01/submit.json"))
    assert committed == sent


def test_schema_autofill_is_automatic_near_the_deadline(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(SCHEMA)})
    finish_worker(server)
    server.record_worker_result("TASK-01")
    server.adapter.get_match_projection = lambda _: {"matchUuid": server.match_uuid, "matchCode": "TEST1234",
                                                     "status": "RUNNING", "remainingSec": 200,
                                                     "backlogItems": server.adapter.items}
    out = server.integrate_and_submit("TASK-01")
    assert out["status"] == "ACCEPTED" and "auditSummary" in server.adapter.calls[-1]["answer"]


def test_valid_answer_is_untouched(native_server):
    server = native_server
    loose = dict(SCHEMA, required=["status"])
    publish(server, {"challenge/TASK-01-submit.schema.json": json.dumps(loose)})
    _, _, answer, _ = finish_worker(server)
    server.record_worker_result("TASK-01")
    server.integrate_and_submit("TASK-01")
    assert server.adapter.calls[-1]["answer"] == answer


def test_cross_item_and_odd_inputs_do_not_block_start_or_claim(native_server):
    """CR-08 announced challenge/CR-05-seed.js; the strict resolver raised after START_WORK succeeded."""
    server = native_server
    publish(server, {"challenge/TASK-01.md": "# TASK-01\n", "challenge/CR-05-seed.js": "export default [1]\n",
                     "challenge/data/TASK-01.bin": b"\x00\xff\xfe binary"})
    server.adapter.items[0]["releaseFiles"] = ["challenge/TASK-01.md", "challenge/CR-05-seed.js",
                                           "challenge/data/TASK-01.bin", "challenge/.env", "challenge/gone.md"]
    result = server.start_work("TASK-01")
    assert result["files"]["challenge/CR-05-seed.js"] == "export default [1]\n"
    assert result["binaryFiles"] == ["challenge/data/TASK-01.bin"]
    assert {w["path"] for w in result["announcedInputWarnings"]} >= {"challenge/.env"}
    assert "challenge/gone.md" in result["unavailableAnnouncedFiles"]
    claim = server.claim_task("TASK-01", "worker-01", 120)
    match = Path(claim["matchRoot"])
    assert (match / "challenge/CR-05-seed.js").read_text() == "export default [1]\n"
    assert (match / "challenge/data/TASK-01.bin").read_bytes().startswith(b"\x00\xff")
    assert claim["taskEnvelope"]["input_warnings"]
