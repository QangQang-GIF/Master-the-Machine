"""mtm_training — Offline self-learning, failure classification, and skill evolution."""
from .failure_analysis import FailureClassifier, FailureType
from .lesson_extractor import LessonExtractor
from .skill_evolver import SkillEvolver
from .promotion import PromotionGate
from .pipeline import (
    PairedEvaluation,
    ABPipelineReport,
    evaluate_paired_runs,
    propose_from_failures,
    generate_proposal_diff,
    save_paired_dataset,
    load_paired_dataset,
    run_ab_training_pipeline,
    rollback_skill_promotion,
)

__all__ = [
    "FailureClassifier",
    "FailureType",
    "LessonExtractor",
    "SkillEvolver",
    "PromotionGate",
    "PairedEvaluation",
    "ABPipelineReport",
    "evaluate_paired_runs",
    "propose_from_failures",
    "generate_proposal_diff",
    "save_paired_dataset",
    "load_paired_dataset",
    "run_ab_training_pipeline",
    "rollback_skill_promotion",
]
