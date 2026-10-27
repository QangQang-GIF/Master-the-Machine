"""
Root pytest configuration for tests/.
Ensures repository root and tests directory are in sys.path and exposes fixtures globally.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
TESTS_DIR = ROOT_DIR / "tests"

for p in [str(ROOT_DIR), str(TESTS_DIR)]:
    if p not in sys.path:
        sys.path.insert(0, p)

try:
    from tests.fixtures.mock_gm_server import mock_gm_server
    from tests.fixtures.git_harness import offline_git_env
    from tests.fixtures.telemetry_validator import validate_progress_ndjson
except ModuleNotFoundError:
    from fixtures.mock_gm_server import mock_gm_server
    from fixtures.git_harness import offline_git_env
    from fixtures.telemetry_validator import validate_progress_ndjson
