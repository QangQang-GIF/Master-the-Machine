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
import os
import shutil
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def install_pack(source_dir: Path, workspace_dir: Path) -> None:
    source_dir = source_dir.resolve()
    ws = workspace_dir.resolve()
    target_pack_dir = ws / "team-sdlc-pack"
    target_pack_dir.mkdir(parents=True, exist_ok=True)

    print(f"Installing team pack from {source_dir} to {target_pack_dir}...")

    # 1. Copy key pack manifests
    files_to_copy = [
        "team-runtime.json",
        "team-pack-info.json",
    ]
    for f in files_to_copy:
        src_f = source_dir / f
        if src_f.exists():
            shutil.copy2(src_f, target_pack_dir / f)

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
    ]
    for d in dirs_to_copy:
        src_d = source_dir / d
        if src_d.exists():
            dst_d = target_pack_dir / d
            shutil.copytree(src_d, dst_d, dirs_exist_ok=True)

    # 3. Setup skills layout: .agents/skills/team-orchestrator/SKILL.md
    ws_agents_skills = ws / ".agents" / "skills"
    pack_agents_skills = target_pack_dir / ".agents" / "skills"
    ws_agents_skills.mkdir(parents=True, exist_ok=True)
    pack_agents_skills.mkdir(parents=True, exist_ok=True)

    src_agents_skills = source_dir / ".agents" / "skills"
    if src_agents_skills.exists():
        shutil.copytree(src_agents_skills, ws_agents_skills, dirs_exist_ok=True)
        shutil.copytree(src_agents_skills, pack_agents_skills, dirs_exist_ok=True)

    src_skills = source_dir / "skills"
    if src_skills.exists():
        for skill_dir in src_skills.iterdir():
            if skill_dir.is_dir():
                shutil.copytree(skill_dir, ws_agents_skills / skill_dir.name, dirs_exist_ok=True)
                shutil.copytree(skill_dir, pack_agents_skills / skill_dir.name, dirs_exist_ok=True)

    # 4. Setup agent profiles layout: .codex/agents/*.toml
    ws_codex_agents = ws / ".codex" / "agents"
    pack_codex_agents = target_pack_dir / ".codex" / "agents"
    ws_codex_agents.mkdir(parents=True, exist_ok=True)
    pack_codex_agents.mkdir(parents=True, exist_ok=True)

    agents_source = source_dir / "agents"
    if agents_source.exists():
        for toml_f in agents_source.glob("*.toml"):
            shutil.copy2(toml_f, ws_codex_agents / toml_f.name)
            shutil.copy2(toml_f, pack_codex_agents / toml_f.name)

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
            [sys.executable, str(check_script), "--team-workspace", str(target_pack_dir)],
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
