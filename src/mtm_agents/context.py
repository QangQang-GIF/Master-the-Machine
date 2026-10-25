"""Compile bounded role/knowledge/skill guidance into the existing TASK.md contract."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from mtm_core.config.loader import get_project_root
from mtm_knowledge import build_index, retrieve_knowledge
from .profiles import resolve_profile


def compile_task_context(
    envelope: dict[str, Any],
    selected_skills: list[str],
    root: Path | str | None = None,
    max_skill_chars: int = 9000,
) -> str:
    """Read only trusted MTM skills; repository content remains untrusted task data."""
    project = Path(root).resolve() if root else get_project_root()
    profile = resolve_profile(str(envelope.get("task_type", "TASK")), project / "profiles" / "agents")
    parts = [
        "## Specialist Agent Profile",
        f"Profile: {profile.name} v{profile.version}",
        f"Role: {profile.description}",
        profile.instructions,
    ]
    repo = envelope.get("repository_path")
    if repo:
        index = build_index(repo)
        query = " ".join(str(envelope.get(k, "")) for k in ("title", "description", "task_type"))
        parts += ["## Project Knowledge Index", retrieve_knowledge(index, query)]
    parts.append("## Selected MTM Skill Instructions")
    remaining = max(0, max_skill_chars)
    skill_root = project / ".agents" / "skills"
    for selected in selected_skills:
        name = selected.split("@", 1)[0]
        # Resolved against the trusted skill directory, not paths supplied by a worker.
        if not name or "/" in name or "\\" in name or name.startswith("."):
            raise ValueError(f"Invalid skill identifier: {selected}")
        path = skill_root / name / "SKILL.md"
        if not path.is_file() or path.is_symlink():
            raise FileNotFoundError(f"Selected skill is unavailable: {name}")
        content = path.read_text(encoding="utf-8")
        if len(content) > remaining:
            content = content[:remaining] + "\n[Skill excerpt truncated due to context budget]"
        parts += [f"### {selected}", content]
        remaining -= min(len(content), remaining)
        if remaining == 0:
            break
    return "\n\n".join(parts)
