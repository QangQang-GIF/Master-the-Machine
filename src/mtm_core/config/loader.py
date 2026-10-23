"""Configuration loader for development, production, and model routing configs."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional
import yaml

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


def load_environment_config(mode: Optional[str] = None) -> Dict[str, Any]:
    """Load configuration for specified mode ('development' or 'production')."""
    if mode is None:
        mode = os.environ.get("MTM_MODE", "development").lower()

    filename = f"{mode}.yaml"
    target = CONFIG_DIR / filename
    if not target.exists():
        # Fallback to development if missing
        target = CONFIG_DIR / "development.yaml"
    return load_yaml(target)


def load_model_profiles() -> Dict[str, Any]:
    """Load model routing profiles configuration."""
    target = CONFIG_DIR / "model_profiles.yaml"
    if not target.exists():
        # Also check models.yaml alias
        alt = CONFIG_DIR / "models.yaml"
        if alt.exists():
            return load_yaml(alt)
    return load_yaml(target)


def verify_skill_checksums(
    root_dir: Path | str,
    checksum_file: Optional[Path | str] = None,
) -> bool:
    """Verify SHA256 integrity of all frozen production skills.

    Raises RuntimeError if any checksum mismatch or missing skill file is detected.
    """
    import hashlib

    base = Path(root_dir).resolve()
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
