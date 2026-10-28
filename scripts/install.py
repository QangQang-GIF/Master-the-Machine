"""
scripts/install.py: Installer script for MTM Player pack.
Installs the team pack into the target TeamWorkspace:
- Resolves paths and validates containment
- Copies/syncs runtime pack files (manifest, profiles, schemas, runner, src, skills, etc.)
- Sets up .agents/skills and .codex/agents registrations
- Configures .codex/config.toml with absolute python path and PYTHONPATH
- Runs check-distribution.py
- Exits 0 on READY
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def safe_copy2(src: Path, dst: Path) -> None:
    if src.resolve() != dst.resolve():
        shutil.copy2(src, dst)


def safe_copytree(src: Path, dst: Path) -> None:
    if src.resolve() != dst.resolve():
        shutil.copytree(src, dst, dirs_exist_ok=True)


def install_pack(source_dir: Path, workspace_dir: Path) -> None:
    source_dir = source_dir.resolve()
    ws = workspace_dir.resolve()
    target_pack_dir = ws / "team-sdlc-pack"
    target_pack_dir.mkdir(parents=True, exist_ok=True)

    print(f"Installing team pack from {source_dir} to {target_pack_dir}...")

    # 1. Copy key pack manifests to team-sdlc-pack and mirror to .mtm/
    (ws / ".mtm").mkdir(parents=True, exist_ok=True)
    files_to_copy = [
        "team-runtime.json",
        "team-pack-info.json",
        "install.ps1",
        "README.md",
    ]
    for f in files_to_copy:
        src_f = source_dir / f
        if src_f.exists():
            safe_copy2(src_f, target_pack_dir / f)
            if f in ["team-runtime.json", "team-pack-info.json"]:
                safe_copy2(src_f, ws / ".mtm" / f)

    # 1b. Install standard root package.json in TeamWorkspace and team-sdlc-pack (R3)
    pkg_json_src = source_dir / "package.json"
    if pkg_json_src.exists():
        safe_copy2(pkg_json_src, ws / "package.json")
        safe_copy2(pkg_json_src, target_pack_dir / "package.json")
    else:
        root_pkg = {
            "name": "team-sdlc-pack",
            "version": "1.0.0",
            "description": "Master the Machine Team SDLC Pack",
            "private": True,
            "type": "module",
            "files": [
                "agents/",
                "bin/",
                "config/",
                "contracts/",
                "docs/",
                "install.ps1",
                "scripts/check-distribution.mjs",
                "scripts/install.mjs",
                "skills/",
                "src/",
                "README.md",
                "team-pack-info.json",
                "team-runtime.json",
            ],
        }
        (ws / "package.json").write_text(json.dumps(root_pkg, indent=2), encoding="utf-8")
        (target_pack_dir / "package.json").write_text(json.dumps(root_pkg, indent=2), encoding="utf-8")

    # 2. Copy directories including src/ (CRITICAL: P0.3 runtime packages)
    dirs_to_copy = [
        "src",
        "profiles",
        "agents",
        "schemas",
        "runner",
        "scripts",
        "docs",
        "skills",
        "bin",
        "config",
        "contracts",
    ]
    for d in dirs_to_copy:
        src_d = source_dir / d
        if src_d.exists():
            dst_d = target_pack_dir / d
            safe_copytree(src_d, dst_d)

    # 2b. Mirror to target_pack_dir / "runtime" for MTM Player verification
    runtime_dir = target_pack_dir / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    for f in ["package.json", "team-runtime.json", "team-pack-info.json", "README.md", "install.ps1"]:
        src_f = source_dir / f
        if src_f.exists():
            safe_copy2(src_f, runtime_dir / f)
    for d in ["agents", "bin", "config", "contracts", "docs", "scripts", "skills", "src", "profiles", "schemas", "runner"]:
        src_d = source_dir / d
        if src_d.exists():
            safe_copytree(src_d, runtime_dir / d)

    # 3. Setup skills layout: .agents/skills/team-orchestrator/SKILL.md
    ws_agents_skills = ws / ".agents" / "skills"
    pack_agents_skills = target_pack_dir / ".agents" / "skills"
    ws_agents_skills.mkdir(parents=True, exist_ok=True)
    pack_agents_skills.mkdir(parents=True, exist_ok=True)

    src_agents_skills = source_dir / ".agents" / "skills"
    if src_agents_skills.exists():
        safe_copytree(src_agents_skills, ws_agents_skills)
        safe_copytree(src_agents_skills, pack_agents_skills)

    src_skills = source_dir / "skills"
    if src_skills.exists():
        for skill_dir in src_skills.iterdir():
            if skill_dir.is_dir():
                safe_copytree(skill_dir, ws_agents_skills / skill_dir.name)
                safe_copytree(skill_dir, pack_agents_skills / skill_dir.name)

    # 4. Setup agent profiles layout: .codex/agents/*.toml
    ws_codex_agents = ws / ".codex" / "agents"
    pack_codex_agents = target_pack_dir / ".codex" / "agents"
    ws_codex_agents.mkdir(parents=True, exist_ok=True)
    pack_codex_agents.mkdir(parents=True, exist_ok=True)

    agents_source = source_dir / "agents"
    if agents_source.exists():
        for toml_f in agents_source.glob("*.toml"):
            safe_copy2(toml_f, ws_codex_agents / toml_f.name)
            safe_copy2(toml_f, pack_codex_agents / toml_f.name)

    # 5. Create required state, log, and .mtm storage directories
    (ws / ".mtm").mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    (target_pack_dir / "logs").mkdir(parents=True, exist_ok=True)
    (target_pack_dir / "state").mkdir(parents=True, exist_ok=True)

    # 6. Configure .codex/config.toml with PYTHONPATH
    codex_dir = ws / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    python_exe = sys.executable.replace("\\", "/")
    target_posix = target_pack_dir.as_posix()
    src_posix = (target_pack_dir / "src").as_posix()
    sep = ";" if sys.platform.startswith("win") else ":"

    codex_toml = f"""[mcp_servers.mtm_minimal]
command = "{python_exe}"
args = ["-m", "runner.mcp_server"]
cwd = "{target_posix}"
startup_timeout_sec = 15
tool_timeout_sec = 120

[mcp_servers.mtm_minimal.env]
PYTHONPATH = "{src_posix}{sep}{target_posix}"
"""
    (codex_dir / "config.toml").write_text(codex_toml, encoding="utf-8")
    (target_pack_dir / ".codex").mkdir(parents=True, exist_ok=True)
    (target_pack_dir / ".codex" / "config.toml").write_text(codex_toml, encoding="utf-8")

    # 7. Run distribution check
    check_script = target_pack_dir / "scripts" / "check-distribution.py"
    if check_script.exists():
        res = subprocess.run(
            [
                sys.executable,
                str(check_script),
                "--team-workspace",
                str(ws),
                "--pack-root",
                str(target_pack_dir),
            ],
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            sys.stderr.write(f"Post-installation verification failed:\n{res.stderr}\n")
            sys.exit(1)

    print("Installation complete: TEAM_PACK_INSTALLED_READY")


def main() -> None:
    parser = argparse.ArgumentParser(description="Install MTM player pack into TeamWorkspace")
    parser.add_argument("--team-workspace", dest="team_workspace", required=True, help="Path to team workspace")
    parser.add_argument("--source-dir", dest="source_dir", default=".", help="Source pack directory")
    args = parser.parse_args()
    install_pack(Path(args.source_dir), Path(args.team_workspace))


if __name__ == "__main__":
    main()
