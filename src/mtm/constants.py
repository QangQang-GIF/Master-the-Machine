"""src/mtm/constants.py: Centralized runtime constants for MTM 2026 Tournament.

Deterministic, single-path competition runtime configuration targeting Codex CLI 0.164.0.
Eliminates dynamic YAML/TOML configuration loaders and multi-model routing at runtime.
"""
from __future__ import annotations

from pathlib import Path

# ==============================================================================
# 1. Model & Worker Execution Parameters (Fixed Codex Production Runtime)
# ==============================================================================
MODEL: str = "gpt-6.1-sol"
MODEL_EFFORT: str = "medium"
CONCURRENCY: int = 1
DEFAULT_WORKER_BACKEND: str = "codex"
STRICT_BACKEND: bool = True
ALLOW_FALLBACK: bool = False

# ==============================================================================
# 2. Sub-Agent / Worker Archetype Identifiers
# ==============================================================================
AGENT_BUG: str = "bug"
AGENT_TASK: str = "task"
AGENT_CR: str = "cr"
AGENT_PI: str = "pi"
TASK_TYPES: tuple[str, ...] = ("BUG", "TASK", "CR", "PI")

# ==============================================================================
# 3. Operational Timeouts & Clock Limits (in seconds)
# ==============================================================================
MATCH_DURATION_SECONDS: float = 2400.0         # 40 minutes official tournament clock
DEFAULT_TASK_TIMEOUT_SECONDS: float = 420.0    # 7 minutes maximum per task execution
HTTP_TIMEOUT_SECONDS: float = 15.0             # Outbound Game Master network timeout
TOOL_TIMEOUT_SECONDS: float = 120.0            # Codex CLI MCP tool invocation timeout
MCP_TOOL_TIMEOUT_SECONDS: float = 120.0        # Alias for backward compatibility
STARTUP_TIMEOUT_SECONDS: float = 15.0          # Codex CLI MCP server startup timeout
MCP_STARTUP_TIMEOUT_SECONDS: float = 15.0      # Alias for backward compatibility
SQLITE_BUSY_TIMEOUT_MS: int = 30000            # SQLite busy wait timeout (30 seconds)

# ==============================================================================
# 4. Game Master Wire Contract Endpoints & Paths (Official Digital Cuder Contract)
# ==============================================================================
DEFAULT_GAME_MASTER_ORIGIN: str = "https://masterthemachine.tech"
LOCAL_ORIGIN_FALLBACK: str = "http://localhost:8000"
API_BASE_PATH: str = "/api/v1"
API_V1_PREFIX: str = "/api/v1"

# Authoritative Digital Cuder Machine Endpoints
START_WORK_PATH: str = "/api/v1/matches/{match_id}/backlogs/start"
SUBMISSION_PATH: str = "/api/v1/matches/{match_id}/submissions"
SUBMIT_PATH: str = "/api/v1/matches/{match_id}/submissions"
MATCH_PAGE_PATH: str = "/match/{match_id}"
AUTH_LOGIN_PATH: str = "/api/v1/auth/login"

# Forbidden Headers & Wire Constraints
FORBIDDEN_HEADER_TEAM_ID: str = "X-Team-ID"
IDEMPOTENCY_KEY_REGEX: str = r"^[A-Za-z0-9._~-]{8,128}$"
UUID_REGEX: str = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"

# Payload & Answer Limits
MAX_SUBMISSION_BODY_BYTES: int = 524_288     # 512 KB
MAX_ANSWER_CHARS: int = 200_000
MAX_ARTIFACT_URLS: int = 20
MAX_ARTIFACT_URL_LENGTH: int = 2048

# ==============================================================================
# 5. Deterministic Submission & Verification Policy Flags
# ==============================================================================
SUBMISSION_ONCE_ONLY: bool = True
REQUIRE_MVS: bool = True
REQUIRE_MVS_PASSED: bool = True
REQUIRE_EVIDENCE: bool = True
REQUIRE_REMOTE_PUSH: bool = True
REQUIRE_CLEAN_GIT: bool = True

# ==============================================================================
# 6. File Safety Patterns and Blocked Files (GIT-04 & Workspace Containment)
# ==============================================================================
FORBIDDEN_FILE_PATTERNS: tuple[str, ...] = (
    "exam-info.json",   # Immutable contest metadata
    ".env",             # Secret files
    ".env.*",
    "*.env",
    "*secret*",
    "*token*",
    "project_map.md",   # Scratch knowledge artifacts
    "index-meta.json",
)

FORBIDDEN_DIR_PREFIXES: tuple[str, ...] = (
    ".codex",
    ".agents",
    "team-sdlc-pack/state",
)

# ==============================================================================
# 7. Project Root Directory Anchor
# ==============================================================================
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent.parent


def get_project_root() -> Path:
    """Return canonical project root directory."""
    return PROJECT_ROOT
