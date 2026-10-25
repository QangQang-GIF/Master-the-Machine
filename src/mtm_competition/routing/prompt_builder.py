"""PromptBuilder: Single canonical prompt generator for Codex and Antigravity workers."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PromptBuilder:
    """Builds clean, self-contained worker instructions conforming to MTM contracts."""

    @staticmethod
    def build_prompt(
        task_envelope: Dict[str, Any],
        selected_skills: Optional[List[str]] = None,
    ) -> str:
        task_id = task_envelope.get("task_id", "UNKNOWN")
        task_type = task_envelope.get("task_type", "BUG")
        title = task_envelope.get("title", "")
        description = task_envelope.get("description", "")
        skills = selected_skills or task_envelope.get("selected_skills", ["mtm-task-worker@1.0.0"])

        lines = [
            f"# BACKLOG ITEM: {task_id} ({task_type})",
            f"**Title**: {title}",
            f"**Assigned Skills**: {', '.join(skills)}",
            "",
            "## Description & Requirements",
            description.strip(),
            "",
        ]

        # Include MVS section if officially announced
        mvs = task_envelope.get("mvs")
        if mvs:
            lines.append("## Minimum Verification Scenarios (MVS) Criteria")
            for item in mvs:
                if isinstance(item, dict):
                    m_id = item.get("id", "")
                    m_desc = item.get("description", "")
                    lines.append(f"- [{m_id}] {m_desc}")
                else:
                    lines.append(f"- {item}")
            lines.append("")

        # Include evidence requirements if specified
        evidence_required = task_envelope.get("evidence_required")
        if evidence_required:
            lines.append("## Required Evidence")
            for ev in evidence_required:
                lines.append(f"- Produce and save artifact: `{ev}` in workspace root")
            lines.append("")

        # Operational guidelines (Lean2)
        lines.extend([
            "## Operating Instructions",
            "1. **Investigate autonomously**: Use `rg` to find relevant symbols and examine only the necessary code slices.",
            "2. **Minimal Change**: Apply the smallest surgical edit to resolve the task.",
            "3. **Verify**: Run the project tests to confirm your fix passes and causes no regressions.",
            "4. **Output Contract**: Before finishing, you MUST generate `worker_result.json` in the root of the workspace.",
            "",
            "### worker_result.json Schema",
            "```json",
            "{",
            f'  "task_id": "{task_id}",',
            '  "status": "READY_FOR_REVIEW",',
            f'  "skill_ids": {json.dumps(skills)},',
            '  "changed_files": ["<path_to_modified_file>", ...],',
            '  "test_results": [{"command": "<cmd>", "passed": true}],',
            f'  "evidence_paths": {json.dumps(evidence_required or [])},',
            '  "git_revision": null,',
            '  "unresolved_risks": [],',
            '  "elapsed_seconds": 0',
            "}",
            "```",
            "Work autonomously until complete. Save worker_result.json."
        ])

        return "\n".join(lines)
