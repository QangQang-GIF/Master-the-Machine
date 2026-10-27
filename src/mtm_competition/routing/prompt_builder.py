"""
PromptBuilder: Generates canonical task context for Codex root agent conforming to official MTM contract.
Provides:
- build_thin_task_context: Thin context without redundant process prompts for official runner
- build_prompt: Backward-compatible task prompt generator with worker_result.json schema for legacy pipeline
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


class PromptBuilder:
    """Builds clean, self-contained worker instructions conforming to official MTM contracts."""

    @staticmethod
    def build_thin_task_context(
        task_id: str,
        task_type: str = "TASK",
        title: str = "",
        description: str = "",
        mvs: Optional[List[Any]] = None,
        submit_template: Optional[Dict[str, Any]] = None,
        knowledge_pointers: Optional[List[str]] = None,
        deadline_iso: Optional[str] = None,
        match_state: Optional[str] = "IN_PROGRESS",
        evidence_required: Optional[List[str]] = None,
    ) -> str:
        """Construct thin, official task context without duplicate process prompts."""
        lines = [
            f"# BACKLOG ITEM: {task_id} ({task_type})",
            f"**Title**: {title}",
            f"**Match State**: {match_state or 'IN_PROGRESS'}",
        ]
        if deadline_iso:
            lines.append(f"**Deadline**: {deadline_iso}")
        lines.append("")

        lines.extend([
            "## Description & Requirements",
            description.strip(),
            "",
        ])

        # Official Minimum Verification Scenarios
        if mvs:
            lines.append("## Minimum Verification Scenarios (MVS)")
            for item in mvs:
                if isinstance(item, dict):
                    m_id = item.get("id", "")
                    m_desc = item.get("description", "")
                    lines.append(f"- [{m_id}] {m_desc}")
                else:
                    lines.append(f"- {item}")
            lines.append("")

        # Evidence requirements
        if evidence_required:
            lines.append("## Required Evidence")
            for ev in evidence_required:
                lines.append(f"- Save evidence file: `answers/{task_id}/evidence/{ev}` and reference as `evidence/{ev}` in submit.json")
            lines.append("")

        # Knowledge pointers from PROJECT_MAP.md
        if knowledge_pointers:
            lines.append("## Repository Pointers (from PROJECT_MAP.md)")
            for ptr in knowledge_pointers:
                lines.append(f"- `{ptr}`")
            lines.append("")

        # Official Output Contract
        lines.extend([
            "## Submission Output Contract",
            f"1. **Answer File**: Save your final structured answer to `answers/{task_id}/submit.json`.",
            f"2. **Evidence Files**: If evidence is required, store artifacts under `answers/{task_id}/evidence/<filename>` and reference them in `submit.json` as `evidence/<filename>`.",
            "3. **Git Invariant**: Both the source fix, `submit.json`, and all evidence MUST be committed to the assigned git branch.",
            "",
        ])

        if submit_template is not None:
            lines.extend([
                "### Required submitTemplate Structure",
                "```json",
                json.dumps(submit_template, indent=2),
                "```",
                "",
            ])

        return "\n".join(lines)

    @staticmethod
    def build_prompt(
        task_envelope: Dict[str, Any],
        selected_skills: Optional[List[str]] = None,
    ) -> str:
        """Backward-compatible prompt builder supporting legacy runner and pipeline tests."""
        task_id = task_envelope.get("task_id", "UNKNOWN")
        task_type = task_envelope.get("task_type", "BUG")
        title = task_envelope.get("title", "")
        description = task_envelope.get("description", "")
        skills = selected_skills or task_envelope.get("selected_skills", ["mtm-task-worker@1.0.0"])
        evidence_required = task_envelope.get("evidence_required") or []
        mvs = task_envelope.get("mvs")

        lines = [
            f"# BACKLOG ITEM: {task_id} ({task_type})",
            f"**Title**: {title}",
            f"**Assigned Skills**: {', '.join(skills)}",
            "",
            "## Description & Requirements",
            description.strip(),
            "",
        ]

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

        if evidence_required:
            lines.append("## Required Evidence")
            for ev in evidence_required:
                lines.append(f"- Produce and save artifact: `{ev}` in workspace root")
            lines.append("")

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
            f'  "evidence_paths": {json.dumps(evidence_required)},',
            '  "git_revision": null,',
            '  "unresolved_risks": [],',
            '  "elapsed_seconds": 0',
            "}",
            "```",
            "Work autonomously until complete. Save worker_result.json."
        ])

        return "\n".join(lines)
