import json
import time
from pathlib import Path

import importlib.util

_spec = importlib.util.spec_from_file_location("check_done", Path(__file__).resolve().parents[1] / "scripts" / "check_done.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
evaluate = _mod.evaluate

KEY = "BUG-04"


def _setup(tmp_path, results, files=()):
    root = tmp_path / "m"
    (root / "challenge").mkdir(parents=True)
    ev = root / "answers" / KEY / "evidence"
    ev.mkdir(parents=True)
    (root / "challenge" / f"{KEY}-minimum-verification-scenarios.md").write_text(
        "# x\n\nRequired evidence:\n- `tc-02-top-order.png`\n")
    for f in files:
        (ev / f).write_bytes(b"data")
    (root / "answers" / KEY / "submit.json").write_text(json.dumps({"status": "DONE", "testResults": results}))
    return root


def test_not_ready_lists_missing_screenshot_and_failing_test(tmp_path):
    root = _setup(tmp_path, [{"id": f"{KEY}-TC-01", "result": "PASS", "evidence": []},
                             {"id": f"{KEY}-TC-02", "result": "NOT_RUN", "evidence": []}])
    out = evaluate(root, KEY, cutoff_epoch=time.time() + 300)
    assert not out["ready"] and out["secondsLeft"] > 200
    text = " ".join(out["todo"])
    assert "BUG-04-TC-02" in text and "tc-02-top-order.png" in text


def test_ready_when_all_pass_and_evidence_present(tmp_path):
    root = _setup(tmp_path, [{"id": f"{KEY}-TC-01", "result": "PASS", "evidence": []},
                             {"id": f"{KEY}-TC-02", "result": "PASS", "evidence": []}],
                  files=["tc-02-top-order.png"])
    out = evaluate(root, KEY)
    assert out["ready"] and out["status"] == "DONE" and out["todo"] == []


def test_empty_evidence_file_does_not_count(tmp_path):
    root = _setup(tmp_path, [{"id": f"{KEY}-TC-01", "result": "PASS", "evidence": []}])
    (root / "answers" / KEY / "evidence" / "tc-02-top-order.png").write_bytes(b"")
    assert not evaluate(root, KEY)["ready"]


def test_missing_submit_is_an_error(tmp_path):
    assert "error" in evaluate(tmp_path, KEY)
