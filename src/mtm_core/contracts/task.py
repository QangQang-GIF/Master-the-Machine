"""TaskEnvelope v2 Contract.

Standardized task specification passed from Orchestrator / Scheduler to WorkerLaunchers.
Supports strict validation, path boundary containment, and model/skill routing configurations.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TaskEnvelope:
    task_id: str
    task_type: str  # BUG, CR, TASK, PI
    title: str
    description: str
    repository_path: str
    base_revision: Optional[str] = "HEAD"
    points: int = 100
    selected_skills: List[str] = field(default_factory=lambda: ["mtm-task-worker@0.1.0"])
    time_budget_seconds: int = 420

    # Optional v2 fields
    deadline_epoch: Optional[float] = None
    match_remaining_time: Optional[float] = None
    mvs: Optional[List[Dict[str, Any]]] = None
    evidence_required: List[str] = field(default_factory=list)
    model: Optional[str] = None
    reasoning_effort: Optional[str] = None
    backend: str = "codex"  # "codex" | "antigravity"
    token_budget: Optional[int] = None
    sandbox_policy: str = "workspace-write"
    allowed_paths: List[str] = field(default_factory=list)
    attempt_number: int = 1
    skill_profile: Optional[str] = None
    model_profile: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def repo_path(self) -> str:
        return self.repository_path

    @property
    def time_budget(self) -> int:
        return self.time_budget_seconds

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskEnvelope:
        d = dict(data)
        # Handle field aliases from v1 or external Game Master format
        if "repo_path" in d and "repository_path" not in d:
            d["repository_path"] = d.pop("repo_path")
        if "time_budget" in d and "time_budget_seconds" not in d:
            d["time_budget_seconds"] = d.pop("time_budget")
        if "skills" in d and "selected_skills" not in d:
            d["selected_skills"] = d.pop("skills")
        if "effort" in d and "reasoning_effort" not in d:
            d["reasoning_effort"] = d.pop("effort")

        # Strip unknown kwargs if necessary or filter to fields
        field_names = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in field_names}
        return cls(**filtered)
