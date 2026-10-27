#!/usr/bin/env python3
"""
check-distribution.py: Read-only distribution verification script.
Verifies registrations/payload parity, identity/version, schema/manifest.
Outputs "READY" and exits 0 on success; non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def fail(msg: str) -> None:
    sys.stderr.write(f"DISTRIBUTION_CHECK_FAILED: {msg}\n")
    sys.exit(1)


def verify_distribution(base_dir: Path) -> None:
    # 1. team-runtime.json
    runtime_path = base_dir / "team-runtime.json"
    if not runtime_path.exists():
        fail(f"Missing team-runtime.json at {runtime_path}")
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid JSON in team-runtime.json: {exc}")

    if runtime.get("schemaVersion") != 1:
        fail(f"team-runtime.json schemaVersion must be 1, got {runtime.get('schemaVersion')}")
    if runtime.get("kind") != "mtm-team-runtime":
        fail(f"team-runtime.json kind must be 'mtm-team-runtime', got '{runtime.get('kind')}'")
    if runtime.get("name") != "team-sdlc-pack":
        fail(f"team-runtime.json name must be 'team-sdlc-pack', got '{runtime.get('name')}'")
    if runtime.get("protocolVersion") != 4:
        fail(f"team-runtime.json protocolVersion must be 4, got {runtime.get('protocolVersion')}")
    if runtime.get("provider") != "codex":
        fail(f"team-runtime.json provider must be 'codex', got '{runtime.get('provider')}'")
    if not isinstance(runtime.get("entrypoint"), dict) or runtime["entrypoint"].get("rootAgentType") != "team_orchestrator":
        fail("team-runtime.json entrypoint.rootAgentType must be 'team_orchestrator'")

    # 2. team-pack-info.json
    info_path = base_dir / "team-pack-info.json"
    if not info_path.exists():
        fail(f"Missing team-pack-info.json at {info_path}")
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid JSON in team-pack-info.json: {exc}")

    required_keys = {"schemaVersion", "version", "teamId", "teamName"}
    info_keys = set(info.keys())
    if info_keys != required_keys:
        fail(f"team-pack-info.json must contain exactly 4 keys ({required_keys}), got: {info_keys}")
    if info.get("schemaVersion") != 1:
        fail("team-pack-info.json schemaVersion must be 1")
    if info.get("version") != runtime.get("version"):
        fail(f"Version mismatch: team-runtime ({runtime.get('version')}) vs team-pack-info ({info.get('version')})")
    if not isinstance(info.get("teamId"), str) or not info["teamId"].strip():
        fail("Invalid teamId in team-pack-info.json")
    if not isinstance(info.get("teamName"), str) or not info["teamName"].strip():
        fail("Invalid teamName in team-pack-info.json")

    # 3. Static Provider Profiles
    required_profiles = [
        "team_orchestrator",
        "team_repo_scout",
        "team_feature_worker",
        "team_quality_checker",
    ]

    for prof in required_profiles:
        candidates = [
            base_dir / "agents" / f"{prof}.toml",
            base_dir / "agents" / f"{prof.replace('_', '-')}.toml",
            base_dir / "profiles" / "agents" / f"{prof}.toml",
            base_dir / "profiles" / "agents" / f"{prof.replace('_', '-')}.toml",
        ]
        found = next((p for p in candidates if p.exists()), None)
        if not found:
            fail(f"Missing provider profile for '{prof}'. Checked: {[str(c) for c in candidates]}")

        content = found.read_text(encoding="utf-8")
        match = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if not match or match.group(1) != prof:
            fail(f"Profile at {found} must declare name = '{prof}'")

    # 4. Progress Event Schema
    schema_path = base_dir / "schemas" / "progress-event.schema.json"
    if schema_path.exists():
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"Invalid schema JSON at {schema_path}: {exc}")

    print("Distribution check passed: ALL_VERIFIED")
    print("READY")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MTM player pack distribution")
    parser.add_argument("--team-workspace", dest="team_workspace", default=".", help="Path to team workspace")
    args = parser.parse_args()
    verify_distribution(Path(args.team_workspace).resolve())


if __name__ == "__main__":
    main()
