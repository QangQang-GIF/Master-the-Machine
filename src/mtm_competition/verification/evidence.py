"""EvidenceValidator: Verifies that required evidence artifacts exist, are non-empty, and valid."""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple


class EvidenceValidationError(Exception):
    pass


class EvidenceValidator:
    """Validates existence and integrity of required evidence files."""

    @staticmethod
    def validate(
        workspace_dir: Path | str,
        required_evidence: List[str],
        declared_evidence: List[str],
    ) -> Tuple[bool, List[str]]:
        """Verify declared and required evidence files on disk.

        Returns (is_valid, missing_files).
        """
        ws = Path(workspace_dir).resolve()
        missing = []

        for req in required_evidence:
            if req not in declared_evidence:
                missing.append(req)
                continue
            ev_file = ws / req
            if not ev_file.exists():
                missing.append(req)
            elif ev_file.is_file() and ev_file.stat().st_size == 0:
                missing.append(f"{req} (empty file)")

        return (len(missing) == 0, missing)
