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
