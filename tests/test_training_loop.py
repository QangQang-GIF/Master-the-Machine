import pytest
from runner.training_loop import TrainingLoop, classify_runtime_failure


def test_ten_minute_gate_and_atomic_duplicate_observer(tmp_path):
    loop = TrainingLoop(tmp_path / "cycle.db")
    loop.start_round("NEW12345", "a" * 40, now=100)
    assert not loop.reserve_observation(now=699)["readAllowed"]
    assert loop.reserve_observation(now=700)["readAllowed"]
    assert not TrainingLoop(loop.path).reserve_observation(now=700)["readAllowed"]
    assert loop.reserve_observation(now=1300)["readAllowed"]


def test_only_finished_official_full_score_completes_target(tmp_path):
    loop = TrainingLoop(tmp_path / "cycle.db")
    loop.start_round("NEW12345", "a" * 40)
    metrics = {"matchId": "NEW12345", "officialStatus": "FINISHED", "awardedPoints": 1000, "totalPoints": 1000}
    assert not loop.record(metrics)["targetReached"]
    assert not loop.record({**metrics, "scoreSource": "GAME_MASTER", "officialStatus": "IN_PROGRESS"})["targetReached"]
    assert loop.record({**metrics, "scoreSource": "GAME_MASTER"})["targetReached"]
    assert not loop.reserve_observation(now=10**12)["readAllowed"]


def test_history_payloads_rejected_and_active_error_does_not_unfreeze_skills(tmp_path):
    loop = TrainingLoop(tmp_path / "cycle.db")
    loop.start_round("NEW12345", "a" * 40)
    with pytest.raises(ValueError):
        loop.record({"matchId": "NEW12345", "answer": {"status": "DONE"}})
    with pytest.raises(ValueError):
        loop.record({"matchId": "NEW12345", "officialStatus": {"private": "not permitted"}})
    assert loop.record({"matchId": "NEW12345", "officialStatus": "IN_PROGRESS", "cliStatus": "FAILED"})["phase"] == "FROZEN_MATCH"
    assert loop.record({"matchId": "NEW12345", "officialStatus": "PREPARING", "preparationFailed": True})["phase"] == "DEV_REVIEW_REQUIRED"


def test_authorized_abort_still_requires_verified_stopped_runtime(tmp_path):
    loop = TrainingLoop(tmp_path / "cycle.db")
    loop.start_round("NEW12345", "a" * 40)
    with pytest.raises(ValueError, match="authorization"):
        loop.confirm_stopped_for_dev({"status": "STOPPED", "pid": None})
    loop.authorize_abort_on_error()
    assert loop.record({"matchId": "NEW12345", "officialStatus": "IN_PROGRESS", "runtimeFailed": True})["phase"] == "STOP_FOR_DEV"
    with pytest.raises(ValueError, match="STOPPED"):
        loop.confirm_stopped_for_dev({"status": "RUNNING", "pid": 123})
    assert loop.confirm_stopped_for_dev({"status": "STOPPED", "pid": None})["phase"] == "DEV_REVIEW_REQUIRED"
    loop.start_round("NEXT1234", "b" * 40)
    assert loop.abort_on_error()


def test_expected_command_failure_is_not_a_terminal_run_failure():
    assert not classify_runtime_failure({"toolActivity": [{"phase": "FAILED"}]}, {"status": "RUNNING"})["runtimeFailed"]
    assert classify_runtime_failure({"backlogs": [{"status": "BLOCKED", "receiptId": None}]}, {"status": "RUNNING"})["runtimeFailed"]
    outcome = classify_runtime_failure({}, {"status": "WAITING_APPROVAL", "approvals": [{"id": "approval"}]})
    assert outcome["hostApprovalRequired"] and not outcome["runtimeFailed"]
