"""Configuration loader and integrity verification."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

from mtm.constants import (
    MODEL,
    MODEL_EFFORT,
    DEFAULT_TASK_TIMEOUT_SECONDS,
    CONCURRENCY,
    DEFAULT_WORKER_BACKEND,
    STRICT_BACKEND,
    ALLOW_FALLBACK,
    PROJECT_ROOT,
    get_project_root,
)

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent.parent / "config"


def load_yaml(file_path: Path | str) -> Dict[str, Any]:
    """Load and parse a YAML file."""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"Configuration file does not exist: {p}")
    content = p.read_text(encoding="utf-8")
    return yaml.safe_load(content) or {}


def get_config_dir() -> Path:
    return CONFIG_DIR


def get_environment_mode(explicit_mode: Optional[str] = None) -> str:
    """Resolve environment mode: explicit argument -> MTM_ENV -> MTM_MODE -> 'development'."""
    if explicit_mode:
        return explicit_mode.lower()
    return (os.environ.get("MTM_ENV") or os.environ.get("MTM_MODE") or "development").lower()


def load_environment_config(mode: Optional[str] = None) -> Dict[str, Any]:
    """Return centralized configuration backed by src/mtm/constants.py."""
    resolved_mode = get_environment_mode(mode)
    is_prod = resolved_mode == "production"
    return {
        "mode": resolved_mode,
        "supervisor": "none" if is_prod else "antigravity_or_codex_desktop",
        "orchestrator": {
            "worker_backend": DEFAULT_WORKER_BACKEND,
        },
        "worker": {
            "strict_backend": STRICT_BACKEND,
            "allow_fallback": ALLOW_FALLBACK,
        },
        "skills": {
            "registry_dir": ".agents/skills",
            "mutable": not is_prod,
            "allow_evolution": not is_prod,
            "frozen_manifest": "releases/prod_manifest.json",
            "verify_checksums": True,
        },
        "runtime": {
            "default_timeout_seconds": int(DEFAULT_TASK_TIMEOUT_SECONDS),
            "concurrency_limit": CONCURRENCY,
            "log_level": "INFO",
            "trace_logging": True,
            "submission": {
                "once_only": True,
                "require_evidence": True,
                "require_mvs_passed": True,
            },
        },
    }


def load_model_profiles() -> Dict[str, Any]:
    """Return fixed tournament model profile backed by src/mtm/constants.py."""
    profile = {
        "codex_model": MODEL,
        "codex_effort": MODEL_EFFORT,
        "agy_model": "gemini-3.8-flash-low",
        "agy_effort": MODEL_EFFORT,
    }
    return {
        "default_profile": "standard",
        "profiles": {
            "fast": profile,
            "standard": profile,
            "deep_reasoning": profile,
        },
        "allowlist": {
            "models": [MODEL],
            "effort_levels": ["low", "medium", "high"],
        },
    }


def verify_skill_checksums(
    root_dir: Path | str,
    checksum_file: Optional[Path | str] = None,
) -> bool:
    """Verify SHA256 integrity of all frozen production skills.

    Raises RuntimeError if any checksum mismatch or missing skill file is detected.
    """
    import hashlib

    base = get_project_root() if not root_dir or str(root_dir) == "." else Path(root_dir).resolve()
    cs_path = Path(checksum_file).resolve() if checksum_file else base / "releases" / "checksums.sha256"
    if not cs_path.exists():
        raise FileNotFoundError(f"Skill checksum file not found: {cs_path}")

    lines = cs_path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) != 2:
            continue
        expected_hash, rel_path = parts[0], parts[1].strip()
        target_file = base / rel_path
        if not target_file.exists():
            raise RuntimeError(f"Skill integrity check failed: missing file {rel_path}")

        file_bytes = target_file.read_bytes().replace(b"\r\n", b"\n")
        actual_hash = hashlib.sha256(file_bytes).hexdigest()
        if actual_hash.lower() != expected_hash.lower():
            raise RuntimeError(
                f"Skill checksum mismatch for {rel_path}: expected {expected_hash}, calculated {actual_hash}"
            )

    return True


def verify_profile_checksums(
    root_dir: Path | str = ".",
    checksum_file: Optional[Path | str] = None,
) -> bool:
    """Verify that all agent profiles in profiles/agents/*.yaml are pinned in releases/checksums.sha256."""
    import hashlib

    base = get_project_root() if not root_dir or str(root_dir) == "." else Path(root_dir).resolve()
    cs_path = Path(checksum_file).resolve() if checksum_file else base / "releases" / "checksums.sha256"
    if not cs_path.exists():
        raise FileNotFoundError(f"Checksum file not found: {cs_path}")

    pinned: Dict[str, str] = {}
    for line in cs_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(None, 1)
        if len(parts) == 2:
            pinned[parts[1].strip()] = parts[0].lower()

    profiles_dir = base / "profiles" / "agents"
    if profiles_dir.is_dir():
        for profile_path in sorted(profiles_dir.glob("*.yaml")):
            rel_path = profile_path.relative_to(base).as_posix()
            if rel_path not in pinned:
                raise RuntimeError(f"Unpinned agent profile in release manifest: {rel_path}")
            actual_hash = hashlib.sha256(profile_path.read_bytes().replace(b"\r\n", b"\n")).hexdigest().lower()
            if actual_hash != pinned[rel_path]:
                raise RuntimeError(
                    f"Profile checksum mismatch for {rel_path}: expected {pinned[rel_path]}, calculated {actual_hash}"
                )
    return verify_skill_checksums(base, cs_path)

