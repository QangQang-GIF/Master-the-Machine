"""Standardized failure taxonomy and trace classifier for offline learning."""
from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from mtm_core.contracts import WorkerResult
from mtm_core.runtime import ExecutionTrace


class FailureType(str, Enum):
    REPO_DISCOVERY = "REPO_DISCOVERY"
    TASK_UNDERSTANDING = "TASK_UNDERSTANDING"
    WRONG_HYPOTHESIS = "WRONG_HYPOTHESIS"
    OVER_EDIT = "OVER_EDIT"
    UNDER_EDIT = "UNDER_EDIT"
    TOOL_MISUSE = "TOOL_MISUSE"
    TOKEN_WASTE = "TOKEN_WASTE"
    MVS_OVERFIT = "MVS_OVERFIT"
    REGRESSION = "REGRESSION"
    EVIDENCE = "EVIDENCE"
    TIMEOUT = "TIMEOUT"
    MODEL_CAPABILITY = "MODEL_CAPABILITY"
    SKILL_ROUTING = "SKILL_ROUTING"
    INTEGRATION = "INTEGRATION"
    SUBMISSION = "SUBMISSION"


class FailureClassifier:
    """Classifies failed worker executions into taxonomy categories."""

    @staticmethod
    def classify(
        worker_result: WorkerResult | Dict[str, Any],
        trace: Optional[ExecutionTrace] = None,
    ) -> FailureType:
        res = worker_result if isinstance(worker_result, dict) else worker_result.to_dict()
        status = res.get("status", "")

        if status == "TIMED_OUT":
            return FailureType.TIMEOUT

        # Evidence missing
        if "missing evidence" in " ".join(res.get("unresolved_risks", [])).lower():
            return FailureType.EVIDENCE

        # Token waste
        if trace and trace.billable_tokens > 100000:
            return FailureType.TOKEN_WASTE

        # Over edit: changed way too many files (> 5 for a BUG)
        if len(res.get("changed_files", [])) > 5:
            return FailureType.OVER_EDIT

        # Under edit: claimed ready but 0 files changed
        if len(res.get("changed_files", [])) == 0 and status == "FAILED":
            return FailureType.UNDER_EDIT

        # Test failure after editing
        test_results = res.get("test_results", [])
        if any(not tr.get("passed") for tr in test_results):
            return FailureType.REGRESSION

        return FailureType.WRONG_HYPOTHESIS
