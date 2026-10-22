"""Unit tests for mtm_arena and mtm_training (simulator, metrics, self-learning)."""
import json
from pathlib import Path
from unittest.mock import MagicMock
import pytest

from mtm_arena.metrics import CompetitionMetrics, MatchScorecard
from mtm_arena.simulator import MatchSimulator, WaveItem
from mtm_training.failure_analysis import FailureClassifier, FailureType
from mtm_training.lesson_extractor import LessonExtractor
from mtm_training.skill_evolver import SkillEvolver
from mtm_training.promotion import PromotionGate


def test_competition_metrics_calculation():
    submissions = [
        {"task_id": "BUG-01", "status": "ACCEPTED", "score": 200},
        {"task_id": "CR-01", "status": "ACCEPTED", "score": 300},
        {"task_id": "TASK-01", "status": "REJECTED", "score": 0},
    ]
    attempts = [
        {"billable_tokens": 15000, "status": "READY_FOR_REVIEW"},
        {"billable_tokens": 20000, "status": "READY_FOR_REVIEW"},
        {"billable_tokens": 5000, "status": "FAILED"},
    ]
    card = CompetitionMetrics.compute_scorecard(submissions, attempts, match_duration_minutes=40.0)

    assert card.total_score == 500
    assert card.tasks_submitted == 3
    assert card.tasks_passed == 2
    assert card.tasks_failed == 1
    assert card.pass_rate == round(2 / 3, 4)
    assert card.points_per_minute == 12.5
    assert card.billable_tokens == 40000
    assert card.wasted_attempts == 1


def test_failure_classifier():
    # 1. Timeout
    f1 = FailureClassifier.classify({"status": "TIMED_OUT", "changed_files": []})
    assert f1 == FailureType.TIMEOUT

    # 2. Over-edit
    f2 = FailureClassifier.classify({"status": "FAILED", "changed_files": ["a", "b", "c", "d", "e", "f"]})
    assert f2 == FailureType.OVER_EDIT

    # 3. Test regression
    f3 = FailureClassifier.classify({
        "status": "READY_FOR_REVIEW",
        "changed_files": ["calc.py"],
        "test_results": [{"command": "pytest", "passed": False}],
    })
    assert f3 == FailureType.REGRESSION


def test_offline_self_learning_lifecycle(tmp_path):
    skills_dir = tmp_path / "skills"
    manifest_path = tmp_path / "manifest.json"

    # Setup dummy skill
    bug_dir = skills_dir / "mtm-bug-worker"
    bug_dir.mkdir(parents=True)
    (bug_dir / "SKILL.md").write_text("# Bug Worker\nBase instructions.\n", encoding="utf-8")

    manifest = {"skills": {"mtm-bug-worker": {"version": "1.0.0"}}}
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    # 1. Lesson extraction
    failures = [{"task": 1}, {"task": 2}, {"task": 3}]
    lesson = LessonExtractor.extract_from_cluster(
        failure_type=FailureType.OVER_EDIT,
        target_skill="mtm-bug-worker",
        occurrences=failures,
    )
    assert "surgical scope" in lesson.rule_statement.lower()
    assert lesson.confidence >= 0.65

    # 2. Skill Evolution
    evolver = SkillEvolver(skills_dir=skills_dir)
    proposal = evolver.propose_candidate("mtm-bug-worker", "1.0.0", lesson)
    assert proposal.candidate_version == "1.1.0"
    assert "Learned Procedural Guideline" in proposal.candidate_content

    # 3. Promotion Gate
    gate = PromotionGate(skills_dir=skills_dir, registry_manifest=manifest_path)

    # Rejection scenario: pass rate regresses
    rej = gate.evaluate_and_promote(proposal, baseline_pass_rate=0.8, candidate_pass_rate=0.7, candidate_tokens=1000, baseline_tokens=1000)
    assert rej.promoted is False
    assert "regressed" in rej.reason

    # Rejection scenario: token increase exceeds 10% threshold
    rej_tokens = gate.evaluate_and_promote(proposal, baseline_pass_rate=0.8, candidate_pass_rate=0.85, candidate_tokens=1150, baseline_tokens=1000)
    assert rej_tokens.promoted is False
    assert "increased excessively" in rej_tokens.reason

    # Approval scenario: pass rate improves with acceptable tokens
    app = gate.evaluate_and_promote(proposal, baseline_pass_rate=0.8, candidate_pass_rate=0.9, candidate_tokens=1050, baseline_tokens=1000)
    assert app.promoted is True
    assert app.to_version == "1.1.0"

    # Verify disk was updated
    updated_skill = (bug_dir / "SKILL.md").read_text(encoding="utf-8")
    assert "Learned Procedural Guideline" in updated_skill
    updated_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert updated_manifest["skills"]["mtm-bug-worker"]["version"] == "1.1.0"
