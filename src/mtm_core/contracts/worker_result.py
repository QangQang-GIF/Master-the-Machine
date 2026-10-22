"""WorkerResult v2 Contract.

Standardized structured result returned by workers for verification and QA gating.
Enforces strict enum statuses and evidence auditability.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class WorkerStatus(str, Enum):
    READY_FOR_REVIEW = "READY_FOR_REVIEW"
    BLOCKED = "BLOCKED"
    TIMED_OUT = "TIMED_OUT"
    FAILED = "FAILED"
    ESCALATE = "ESCALATE"


@dataclass
class TestResult:
    command: str
    passed: bool
    output: str = ""


@dataclass
class WorkerResult:
    task_id: str
    status: str  # WorkerStatus or str
    skill_ids: List[str] = field(default_factory=lambda: ["mtm-task-worker@0.1.0"])
    changed_files: List[str] = field(default_factory=list)
    test_results: List[Dict[str, Any]] = field(default_factory=list)
    evidence_paths: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0

    # Optional v2 fields
    usage: Dict[str, Any] = field(default_factory=dict)
    git_revision: Optional[str] = None
    base_revision: Optional[str] = None
    result_revision: Optional[str] = None
    unresolved_risks: List[str] = field(default_factory=list)
    mvs_results: List[Dict[str, Any]] = field(default_factory=list)
    model: Optional[str] = None
    effort: Optional[str] = None
    confidence: float = 1.0
    notes: str = ""

    @property
    def skills_used(self) -> List[str]:
        return self.skill_ids

    @property
    def tests(self) -> List[Dict[str, Any]]:
        return self.test_results

    @property
    def evidence(self) -> List[str]:
        return self.evidence_paths

    @property
    def elapsed(self) -> float:
        return self.elapsed_seconds

    @property
    def risk_assessment(self) -> List[str]:
        return self.unresolved_risks

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> WorkerResult:
        d = dict(data)
        if "skills_used" in d and "skill_ids" not in d:
            d["skill_ids"] = d.pop("skills_used")
        if "tests" in d and "test_results" not in d:
            d["test_results"] = d.pop("tests")
        if "evidence" in d and "evidence_paths" not in d:
            d["evidence_paths"] = d.pop("evidence")
        if "elapsed" in d and "elapsed_seconds" not in d:
            d["elapsed_seconds"] = d.pop("elapsed")
        if "risk_assessment" in d and "unresolved_risks" not in d:
            d["unresolved_risks"] = d.pop("risk_assessment")

        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in field_names}
        return cls(**filtered)
