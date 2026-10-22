"""mtm_competition.integration — Integration guard, Git guard, and submission gating."""
from .integration_guard import IntegrationGuard, IntegrationResult
from .git_guard import GitGuard, VerifiedRevision
from .submission_guard import SubmissionGuard, SubmissionResult

__all__ = [
    "IntegrationGuard",
    "IntegrationResult",
    "GitGuard",
    "VerifiedRevision",
    "SubmissionGuard",
    "SubmissionResult",
]
