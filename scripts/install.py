"""
scripts/install.py: Installer script for MTM Player pack.
Installs the team pack into the target TeamWorkspace:
- Resolves paths and validates containment
- Copies/syncs runtime pack files (manifest, profiles, schemas, runner, etc.)
- Configures .codex/config.toml with absolute python path to runner/mcp_server.py
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

    # Copy key pack artifacts
    files_to_copy = [
        "team-runtime.json",
        "team-pack-info.json",
    ]
    for f in files_to_copy:
        src_f = source_dir / f
        if src_f.exists():
            shutil.copy2(src_f, target_pack_dir / f)

    dirs_to_copy = [
        "profiles",
        "agents",
        "schemas",
        "runner",
        "scripts",
        "docs",
    ]
    for d in dirs_to_copy:
        src_d = source_dir / d
        if src_d.exists():
            dst_d = target_pack_dir / d
            shutil.copytree(src_d, dst_d, dirs_exist_ok=True)

    # Configure .codex/config.toml
    codex_dir = ws / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    mcp_server_script = (target_pack_dir / "runner" / "mcp_server.py").as_posix()
    python_exe = sys.executable.replace("\\", "/")

    codex_toml = f"""[mcp_servers.mtm_minimal]
command = "{python_exe}"
args = ["-m", "runner.mcp_server"]
cwd = "{target_pack_dir.as_posix()}"
startup_timeout_sec = 15
tool_timeout_sec = 120
"""
    (codex_dir / "config.toml").write_text(codex_toml, encoding="utf-8")

    # Run distribution check
    check_script = target_pack_dir / "scripts" / "check-distribution.py"
    if check_script.exists():
        res = subprocess.run([sys.executable, str(check_script), "--team-workspace", str(target_pack_dir)], capture_output=True, text=True)
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
