"""Routing contracts: Skill, model, and resource assignments."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class RoutingDecision:
    task_id: str
    selected_skills: List[str]
    model: str = "gpt-6.1-sol"
    reasoning_effort: str = "medium"
    timeout_seconds: int = 420
    backend: str = "codex"  # "codex" | "antigravity"
    token_budget: Optional[int] = None
    rationale: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> RoutingDecision:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
