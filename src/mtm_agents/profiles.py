"""Backend-neutral agent profiles; Codex and Antigravity are render targets."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
from typing import Iterable

import yaml
from mtm_core.config.loader import get_project_root

_TASK_ROLES = {
    "BUG": "bug-fixer",
    "CR": "feature-implementer",
    "TASK": "general-task-worker",
    "PI": "incident-responder",
}
_VALID_NAME = re.compile(r"^[a-z][a-z0-9-]{1,63}$")


@dataclass(frozen=True)
class AgentProfile:
    name: str
    description: str
    instructions: str
    task_types: tuple[str, ...]
    skills: tuple[str, ...]
    version: str = "1.0.0"

    @classmethod
    def load(cls, path: Path | str) -> "AgentProfile":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("Agent profile must be a mapping")
        name = data.get("name")
        if not isinstance(name, str) or not _VALID_NAME.fullmatch(name):
            raise ValueError("Invalid agent profile name")
        description = data.get("description")
        instructions = data.get("instructions")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Agent description is required")
        if not isinstance(instructions, str) or not instructions.strip():
            raise ValueError("Agent instructions are required")
        task_types = data.get("task_types", [])
        skills = data.get("skills", [])
        if not isinstance(task_types, list) or any(t not in _TASK_ROLES for t in task_types):
            raise ValueError("Invalid task_types")
        if not isinstance(skills, list) or any(not isinstance(s, str) or not _VALID_NAME.fullmatch(s) for s in skills):
            raise ValueError("Invalid skills")
        return cls(
            name=name,
            description=description.strip(),
            instructions=instructions.strip(),
            task_types=tuple(task_types),
            skills=tuple(skills),
            version=str(data.get("version", "1.0.0")),
        )

    def codex_toml(self) -> str:
        """Render only validated fields; JSON string literals are valid TOML strings."""
        return "\n".join([
            f"name = {json.dumps(self.name)}",
            f"description = {json.dumps(self.description)}",
            f"developer_instructions = {json.dumps(self.instructions)}",
            "",
        ])

    def antigravity_markdown(self) -> str:
        """Antigravity custom subagent Markdown with YAML frontmatter."""
        header = yaml.safe_dump(
            {"name": self.name, "description": self.description},
            sort_keys=False,
            allow_unicode=True,
        ).strip()
        return f"---\n{header}\n---\n\n{self.instructions}\n"


def resolve_profile(task_type: str, profiles_dir: Path | str | None = None) -> AgentProfile:
    role = _TASK_ROLES.get(str(task_type).upper())
    if role is None:
        raise ValueError(f"Unknown task type: {task_type}")
    root = Path(profiles_dir) if profiles_dir is not None else get_project_root() / "profiles" / "agents"
    return AgentProfile.load(root / f"{role}.yaml")


def export_native_agents(
    output_root: Path | str,
    profiles_dir: Path | str | None = None,
) -> list[Path]:
    """Generate native definitions in an isolated output directory, never customer sources."""
    source = Path(profiles_dir) if profiles_dir else get_project_root() / "profiles" / "agents"
    target = Path(output_root).resolve()
    written: list[Path] = []
    for profile_file in sorted(source.glob("*.yaml")):
        profile = AgentProfile.load(profile_file)
        for rel, body in (
            (Path(".codex/agents") / f"{profile.name}.toml", profile.codex_toml()),
            (Path(".agents/agents") / f"{profile.name}.md", profile.antigravity_markdown()),
        ):
            dest = target / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding="utf-8")
            written.append(dest)
    return written
