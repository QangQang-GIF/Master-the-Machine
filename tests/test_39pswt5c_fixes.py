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


# Layer 4: one command resolver for run, session and prepare -----------------------------------------------
def _project(tmp_path, scripts, bundled=True):
    from runner.project_runtime import ProjectRuntime
    match = tmp_path / "M4"
    match.mkdir()
    if bundled:
        (match / ".yarn" / "releases").mkdir(parents=True)
        (match / ".yarn" / "releases" / "yarn-4.17.1.cjs").write_text("console.log('yarn ' + process.argv.slice(2).join(' '))")
    (match / "package.json").write_text(json.dumps({"packageManager": "yarn@4.17.1", "scripts": scripts}))
    return ProjectRuntime(match, "M4", "w1")


def test_session_server_uses_the_bundled_yarn(tmp_path, monkeypatch):
    """CR-07 (39PSWT5C): `session` built `yarn run dev` itself; with no global yarn Popen raised WinError 2."""
    from runner import project_runtime
    rt = _project(tmp_path, {"start": "node server.js"})
    real_which = project_runtime.shutil.which
    monkeypatch.setattr(project_runtime.shutil, "which",
                        lambda n, *a, **k: None if Path(n).name.lower().startswith(("yarn", "corepack")) else real_which(n, *a, **k))
    server, note = rt.server_command("dev")
    assert server == ["yarn", "run", "start"] and "using 'start'" in note
    resolved = rt.resolve_command(server)
    assert resolved[1].endswith("yarn-4.17.1.cjs") and resolved[2:] == ["run", "start"]


def test_missing_server_script_lists_scripts_instead_of_crashing(tmp_path):
    rt = _project(tmp_path, {"build": "tsc", "test": "vitest"})
    with pytest.raises(ValueError, match="--server-cmd") as info:
        rt.server_command("dev")
    assert "build" in str(info.value)
    from runner.project_runtime import main
    code = main(["--match-root", str(rt.match_root), "--match-id", "M4", "--worker-id", "w1",
                 "session", "--", "python", "-c", "pass"])
    assert code == 2


def test_server_cmd_and_env_and_cwd_reach_the_server(tmp_path):
    import sys
    from runner.project_runtime import main
    rt = _project(tmp_path, {})
    (rt.match_root / "packages" / "sync").mkdir(parents=True)
    server_py = rt.match_root / "packages" / "sync" / "serve.py"
    server_py.write_text(
        "import http.server, os\n"
        "class H(http.server.BaseHTTPRequestHandler):\n"
        "    def do_GET(self):\n"
        "        body = (os.environ['DATA_DIR'] + '|' + os.getcwd()).encode()\n"
        "        self.send_response(200); self.end_headers(); self.wfile.write(body)\n"
        "    def log_message(self, *a): pass\n"
        "http.server.HTTPServer(('127.0.0.1', int(os.environ['PORT'])), H).serve_forever()\n")
    out = rt.match_root / "seen.txt"
    check = ("import os, urllib.request; open(r'%s','w').write(urllib.request.urlopen("
             "'http://127.0.0.1:%%s/' %% os.environ['PORT'], timeout=3).read().decode())" % out)
    code = main(["--match-root", str(rt.match_root), "--match-id", "M4", "--worker-id", "w1", "session",
                 "--server-cmd", f'"{sys.executable}" serve.py', "--server-cwd", "packages/sync",
                 "--server-env", "DATA_DIR=./data-w1", "--timeout", "20", "--", sys.executable, "-c", check])
    assert code == 0
    data_dir, cwd = out.read_text().split("|")
    assert data_dir == "./data-w1" and Path(cwd).name == "sync"


def test_local_node_modules_bin_is_found(tmp_path):
    rt = _project(tmp_path, {}, bundled=False)
    tool = rt.match_root / "node_modules" / ".bin" / "vitest-xyz"
    tool.parent.mkdir(parents=True)
    tool.write_text("#!/bin/sh\necho local-tool \"$@\"\n")
    tool.chmod(0o755)
    res = rt.run(["vitest-xyz", "run"], timeout=10)
    assert res.ok and "local-tool run" in res.stdout


# Layer 3: PASS rows cite the evidence their MVS scenario requires --------------------------------------
MVS_DOC = ("# TASK-01 minimum verification scenarios\n\n## TASK-01-TC-01 - documented behaviour\n\n"
           "Expected: documented.\n\nRequired evidence: `mvs-tests.log`.\n")


def _worker_adds(worker, match, files):
    for rel, text in files.items():
        (match / rel).parent.mkdir(parents=True, exist_ok=True)
        (match / rel).write_text(text)
    git(worker, "add", "-A", "project/answers")
    git(worker, "commit", "-m", "more evidence")


def test_pass_row_gets_its_required_mvs_evidence(native_server):
    """BUG-11 (39PSWT5C) cited evidence/tc-0N.log although the MVS required mvs-tests.log, which existed."""
    server = native_server
    publish(server, {"challenge/TASK-01-minimum-verification-scenarios.md": MVS_DOC})
    worker, match, _, _ = finish_worker(server)
    _worker_adds(worker, match, {"answers/TASK-01/evidence/mvs-tests.log": "1 passed\n"})
    server.record_worker_result("TASK-01")
    out = server.integrate_and_submit("TASK-01")
    sent = server.adapter.calls[-1]["answer"]
    assert sent["testResults"][0]["evidence"] == ["evidence/mvs-tests.log", "evidence/check.txt"]
    assert "evidence/mvs-tests.log" in sent["evidenceFiles"]
    committed = json.loads(git(server.checkout_root, "show", f"{out['gitRevision']}:project/answers/TASK-01/submit.json"))
    assert committed == sent


def test_missing_required_mvs_evidence_is_refused_while_there_is_time(native_server):
    server = native_server
    publish(server, {"challenge/TASK-01-minimum-verification-scenarios.md": MVS_DOC})
    finish_worker(server)
    server.record_worker_result("TASK-01")
    with pytest.raises(ValueError, match="MVS_EVIDENCE_MISSING") as info:
        server.integrate_and_submit("TASK-01")
    assert "mvs-tests.log" in str(info.value) and not server.adapter.calls
    assert server.integrate_and_submit("TASK-01", schema_autofill=True)["status"] == "ACCEPTED"


def test_mvs_parser_and_worker_check_command(tmp_path):
    import subprocess
    import sys
    from mtm_competition.integration import submit_schema
    from mtm_competition.integration.mvs_evidence import required_evidence
    assert required_evidence(MVS_DOC) == {"TASK-01-TC-01": ["mvs-tests.log"]}
    root = tmp_path / "m"
    (root / "challenge").mkdir(parents=True)
    (root / "challenge" / "TASK-01-minimum-verification-scenarios.md").write_text(MVS_DOC)
    (root / "challenge" / "TASK-01-submit.schema.json").write_text(json.dumps(SCHEMA))
    (root / "answers" / "TASK-01" / "evidence").mkdir(parents=True)
    (root / "answers" / "TASK-01" / "submit.json").write_text(json.dumps(
        {"status": "DONE", "testResults": [{"id": "TASK-01-TC-01", "result": "PASS", "evidence": ["evidence/x.log"]}],
         "evidenceFiles": [], "auditSummary": {}}))
    res = subprocess.run([sys.executable, submit_schema.__file__, str(root), "TASK-01"], capture_output=True, text=True)
    report = json.loads(res.stdout)
    assert res.returncode == 1 and report["errors"]
    assert report["mvsEvidence"]["rowsMissingRequiredFile"] == [{"id": "TASK-01-TC-01", "file": "mvs-tests.log"}]
