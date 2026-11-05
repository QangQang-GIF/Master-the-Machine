"""V2 gap fixes: quiescence before integration, model routing in the claim envelope."""
import subprocess

import pytest

from mtm_competition.scheduling.native import NativeCoordinationError, NativeTaskCoordinator


def _coord(tmp_path, **kw):
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    c = NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH", max_workers=5, normal_workers=4, reserved_workers=1, **kw)
    c.register_projection({"status": "RUNNING", "server_epoch": 1000, "remaining_seconds": 2400,
                           "items": [{"key": "BUG-01", "type": "BUG", "status": "OPEN", "points": 10, "title": "Fix status precedence"}]})
    c.register_started("BUG-01", {})
    return c


def _result():
    return {"task_id": "BUG-01", "status": "READY_FOR_REVIEW", "skill_ids": ["x"], "changed_files": [], "test_results": [],
            "evidence_paths": [], "elapsed_seconds": 1}


def test_begin_integration_can_require_a_confirmed_stop(tmp_path):
    c = _coord(tmp_path)
    c.claim_task("BUG-01", "w1")
    c.record_worker_result("BUG-01", _result())
    ready = c.plan_tasks()["readyToIntegrate"][0]
    assert ready["quiesced"] is False
    with pytest.raises(NativeCoordinationError, match="confirmed stopped"):
        c.begin_integration("BUG-01", require_quiesced=True)
    assert c.begin_integration("BUG-01")["phase"] == "INTEGRATING"  # legacy behaviour unchanged by default


def test_begin_integration_passes_after_stop_confirmation(tmp_path):
    c = _coord(tmp_path)
    c.claim_task("BUG-01", "w1")
    c.confirm_stopped("BUG-01", "native_runtime")
    c.record_worker_result("BUG-01", _result())
    assert c.plan_tasks()["readyToIntegrate"][0]["quiesced"] is True
    assert c.begin_integration("BUG-01", require_quiesced=True)["phase"] == "INTEGRATING"


def test_claim_envelope_carries_requested_model_and_never_invents_actual(tmp_path):
    c = _coord(tmp_path, model_routing=True)
    claim = c.claim_task("BUG-01", "w1")
    env = claim["taskEnvelope"]
    sel = env["model_selection"]
    assert sel["actualModel"] is None and sel["actualSource"] == "unreported"
    assert env["model"] == sel["model"] and env["reasoning_effort"] == sel["effort"]
    assert sel["triage"]["workloadSubtype"] == "LOGIC_FIX"


def test_claim_has_no_model_selection_when_routing_is_off(tmp_path):
    c = _coord(tmp_path)
    assert "model_selection" not in c.claim_task("BUG-01", "w1")["taskEnvelope"]
