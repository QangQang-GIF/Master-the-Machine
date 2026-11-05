"""V2 Phase 3: triage, expected-score priority, model routing."""
from pathlib import Path

import pytest

from mtm_competition.scheduling.model_policy import ModelPolicyError, load_policy, record_actual, select_model
from mtm_competition.tasks.scoring import expected_score_priority
from mtm_competition.tasks.triage import WORKLOAD_SUBTYPES, triage_task

POLICY = load_policy(Path(__file__).resolve().parents[1] / "config" / "model_profiles.yaml")


def t(key, **kw):
    return {"key": key, "type": key.split("-")[0], **kw}


def test_triage_output_contract():
    r = triage_task(t("CR-05", title="Rebuild billing across several modules", minimumVerificationScenarios=[{}] * 6)).to_dict()
    for field in ("backlogItemKey", "officialType", "workloadSubtype", "complexity", "confidence",
                  "estimatedWorkSeconds", "estimatedEvidenceSeconds", "parallelizable", "recommendedModelTier", "reason"):
        assert field in r
    assert r["workloadSubtype"] in WORKLOAD_SUBTYPES
    assert r["estimateKind"] == "forecast"
    assert 0 < r["confidence"] <= 1


def test_official_metadata_wins_over_keywords():
    r = triage_task(t("BUG-01", title="data csv report", workloadSubtype="EVIDENCE_HEAVY"))
    assert r.workloadSubtype == "EVIDENCE_HEAVY" and r.confidence >= 0.9


def test_heavy_prefix_and_pi_classes():
    assert triage_task(t("TASK-05", title="[Heavy Task] Rebuild reporting")).workloadSubtype == "HEAVY_DAG"
    assert triage_task(t("TASK-05", title="[Heavy Task] x")).parallelizable is True
    assert triage_task(t("PI-01", title="login 500 error")).workloadSubtype == "PI_FAST"
    assert triage_task(t("PI-02", title="intermittent data corruption root cause unknown")).workloadSubtype == "PI_COMPLEX"


def test_data_and_logic_and_evidence_subtypes():
    assert triage_task(t("TASK-01", title="Compute totals from csv")).workloadSubtype == "DATA_FAST"
    assert triage_task(t("BUG-02", title="Fix status precedence")).workloadSubtype == "LOGIC_FIX"
    ev = triage_task(t("BUG-03", title="Fix dashboard layout screenshot", minimumVerificationScenarios=[{}] * 4))
    assert ev.workloadSubtype == "EVIDENCE_HEAVY"
    assert ev.estimatedEvidenceSeconds > triage_task(t("BUG-04", title="Fix status precedence")).estimatedEvidenceSeconds


def test_low_confidence_never_picks_weakest_tier():
    r = triage_task(t("TASK-09", title="Misc"))
    if r.confidence < 0.6:
        assert r.recommendedModelTier != "fast"


def test_history_adjusts_forecast_only():
    base = triage_task(t("BUG-01", title="Fix thing"))
    adj = triage_task(t("BUG-01", title="Fix thing"), history={"BUG": 600.0})
    assert adj.estimatedWorkSeconds > base.estimatedWorkSeconds
    assert adj.confidence == base.confidence


def test_expected_score_prefers_value_per_time_and_explains():
    now = 1000.0
    quick = expected_score_priority(t("BUG-01", points=80), {"estimatedWorkSeconds": 100, "estimatedEvidenceSeconds": 20, "confidence": 0.8}, 2000, now)
    slow = expected_score_priority(t("CR-01", points=100), {"estimatedWorkSeconds": 800, "estimatedEvidenceSeconds": 100, "confidence": 0.8}, 2000, now)
    assert quick["priority"] > slow["priority"]
    assert quick["reasons"] and quick["estimateKind"] == "forecast"


def test_infeasible_task_gets_zero_priority_with_reason():
    r = expected_score_priority(t("CR-02", points=500, deadline_epoch=1050), {"estimatedWorkSeconds": 300, "estimatedEvidenceSeconds": 60}, 2000, 1000.0)
    assert r["priority"] == 0 and "only" in r["reasons"][0]


def test_model_selection_stays_in_allowlist():
    for tier in ("fast", "standard", "strong", "bogus"):
        for backend in ("codex", "agy"):
            s = select_model(POLICY, tier, backend)
            assert s["model"] in POLICY["allowlist"]["models"]
            assert s["effort"] in POLICY["allowlist"]["effort_levels"]


def test_fallback_when_requested_model_unavailable_is_recorded_and_allowed():
    s = select_model(POLICY, "strong", "codex", unavailable_models=["gpt-6.1-sol"])
    assert s["fallbackUsed"] is True and s["model"] == "gpt-6-luna"
    assert s["model"] in POLICY["allowlist"]["models"]
    assert any("fallback" in r for r in s["reasons"])


def test_no_candidate_raises_instead_of_leaving_allowlist():
    with pytest.raises(ModelPolicyError):
        select_model(POLICY, "standard", "codex", unavailable_models=POLICY["allowlist"]["models"])


def test_effort_is_capped_and_never_silently_raised():
    s = select_model(POLICY, "strong", "codex", max_effort="medium")
    assert s["effort"] == "medium"
    tight = select_model(POLICY, "standard", "codex", seconds_left=100, estimated_seconds=200)
    assert tight["effort"] == "low"
    relaxed = select_model(POLICY, "standard", "codex", seconds_left=5000, estimated_seconds=200)
    assert relaxed["effort"] == "medium"


def test_telemetry_never_invents_actual_model():
    s = select_model(POLICY, "standard", "codex")
    assert s["actualModel"] is None and s["actualSource"] == "unreported"
    r = record_actual(s, "gpt-6-luna")
    assert r["actualModel"] == "gpt-6-luna" and r["honored"] is False


def _coordinator(tmp_path, adaptive):
    import subprocess
    from mtm_competition.scheduling.native import NativeTaskCoordinator
    repo = tmp_path / "checkout"
    (repo / "source").mkdir(parents=True)
    (repo / "source" / "main.py").write_text("print(1)\n")
    for a in (["init"], ["config", "user.email", "t@t.com"], ["config", "user.name", "T"], ["add", "."], ["commit", "-m", "i"]):
        subprocess.run(["git", "-C", str(repo), *a], check=True, capture_output=True)
    return NativeTaskCoordinator(tmp_path / "state", repo, repo / "source", "source", "MATCH",
                                 max_workers=3, normal_workers=1, reserved_workers=1, adaptive=adaptive)


def test_adaptive_scheduler_orders_by_expected_score_and_explains(tmp_path):
    items = [
        {"key": "CR-01", "type": "CR", "status": "OPEN", "points": 100, "title": "Rebuild reporting", "minimumVerificationScenarios": [{}] * 6},
        {"key": "TASK-01", "type": "TASK", "status": "OPEN", "points": 100, "title": "Compute totals from csv"},
    ]
    proj = {"status": "RUNNING", "server_epoch": 1_000_000, "remaining_seconds": 2400, "items": items}
    legacy = _coordinator(tmp_path / "a", False)
    legacy.register_projection(proj)
    assert legacy.plan_tasks()["selected"][0]["key"] == "CR-01"  # arrival order
    ad = _coordinator(tmp_path / "b", True)
    ad.register_projection(proj)
    plan = ad.plan_tasks()
    assert plan["selected"][0]["key"] == "TASK-01"  # same points, far less time
    assert plan["decisions"]["TASK-01"]["decision"] == "selected"
    assert plan["decisions"]["CR-01"]["decision"].startswith("waiting")
    assert plan["decisions"]["TASK-01"]["estimateKind"] == "forecast"
