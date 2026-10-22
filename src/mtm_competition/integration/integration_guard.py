"""IntegrationGuard: Integrates validated worker patches into the canonical submission repository."""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from mtm_core.contracts import WorkerResult
from mtm_core.git import git_diff, git_status, run_git, get_head_sha


@dataclass
class IntegrationResult:
    success: bool
    commit_sha: Optional[str] = None
    applied_files: List[str] = None
    error_message: Optional[str] = None


class IntegrationGuard:
    """Safely stages, applies, and commits worker patches onto the submission branch."""

    def __init__(self, target_repo_path: Path | str):
        self.target_repo = Path(target_repo_path).resolve()

    def integrate(
        self,
        worker_result: WorkerResult,
        worker_workspace: Path | str,
        commit_message: Optional[str] = None,
    ) -> IntegrationResult:
        ws = Path(worker_workspace).resolve()

        # Extract patch from isolated worker workspace
        patch_text = git_diff(ws, base="HEAD", stage_all=True)
        if not patch_text and not worker_result.changed_files:
            return IntegrationResult(
                success=False,
                error_message="Worker reported changes but diff is empty",
            )

        # Apply patch or copy files into target repository
        applied_files = []
        for rel_file in worker_result.changed_files:
            src_file = ws / rel_file
            dest_file = self.target_repo / rel_file
            if src_file.exists():
                dest_file.parent.mkdir(parents=True, exist_ok=True)
                dest_file.write_bytes(src_file.read_bytes())
                applied_files.append(rel_file)

        # Stage and commit in target repository
        run_git(self.target_repo, ["add", "-A"])
        msg = commit_message or f"feat({worker_result.task_id}): apply verified fix"
        commit_res = run_git(self.target_repo, ["commit", "-m", msg])

        if commit_res.returncode != 0:
            # Check if there were no changes
            if "nothing to commit" in commit_res.stdout or "nothing to commit" in commit_res.stderr:
                return IntegrationResult(
                    success=True,
                    commit_sha=get_head_sha(self.target_repo),
                    applied_files=applied_files,
                )
            return IntegrationResult(
                success=False,
                error_message=f"Git commit failed: {commit_res.stderr}",
            )

        sha = get_head_sha(self.target_repo)
        return IntegrationResult(
            success=True,
            commit_sha=sha,
            applied_files=applied_files,
        )
