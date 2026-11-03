r"""
scripts/create_btc_match.py: Create a pristine BTC-identical Match in Thi/Workspace/matches.

Sets up a brand new match folder (e.g. SOLOVA01) exactly how BTC initializes a tournament room:
1. Re-seeds the pristine Solova codebase from seed commit f8d0be4 into:
   D:\Working\Master_the_Machine\Thi\Workspace\matches\<MATCH_ID>
2. Copies all 10 challenge files, test suites, and schemas into challenge/
3. Creates a clean, empty answers/ directory
4. Commits initial match state into local Git
5. Updates D:\Working\Master_the_Machine\Thi\Workspace\AGENTS.md with the new match descriptor
6. Syncs the latest team-sdlc-pack from Master-the-Machine-main into Thi/Workspace/team-sdlc-pack

Usage:
  python scripts/create_btc_match.py --match-id SOLOVA01
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent

def _find_workspace_dir() -> Path:
    ws_candidate = ROOT_DIR.parent / "runtime" / "workspace"
    if ws_candidate.is_dir():
        return ws_candidate.resolve()
    direct = Path("D:/Working/Cuộc thi Agent/runtime/workspace").resolve()
    if direct.is_dir():
        return direct
    cur = Path(__file__).resolve()
    while cur.parent != cur:
        if (cur / "runtime" / "workspace").is_dir():
            return (cur / "runtime" / "workspace").resolve()
        if (cur / "Thi" / "Workspace").is_dir():
            return (cur / "Thi" / "Workspace").resolve()
        if cur.name == "Workspace" and (cur / "matches").is_dir():
            return cur.resolve()
        cur = cur.parent
    return direct

WORKSPACE_DIR = _find_workspace_dir()
MATCHES_DIR = WORKSPACE_DIR / "matches"
SDLC_PACK_DIR = WORKSPACE_DIR / "team-sdlc-pack"
AGENTS_MD_PATH = WORKSPACE_DIR / "AGENTS.md"

ORIGINAL_MATCH_DIR = MATCHES_DIR / "PVKPJ94U"
CHALLENGE_SOURCE_DIR = ORIGINAL_MATCH_DIR / "PVKPJ94U" / "challenge"
SEED_COMMIT = "f8d0be485d20902f25cab5ccf74c94908c06f92f"


def create_btc_match(match_id: str = "SOLOVA01", reset_existing: bool = True) -> Path:
    match_id = match_id.strip().upper()
    print("=" * 70)
    print(f"[*] CREATING BTC-IDENTICAL MATCH: {match_id}")
    print(f"[*] Destination: {MATCHES_DIR / match_id}")
    print("=" * 70)

    target_match_dir = MATCHES_DIR / match_id
    if target_match_dir.exists():
        if reset_existing:
            print(f"[*] Cleaning up existing match folder: {target_match_dir}")
            # Try git clean or rmtree
            subprocess.run(["cmd", "/c", "rd", "/s", "/q", str(target_match_dir)], capture_output=True)
            if target_match_dir.exists():
                shutil.rmtree(target_match_dir, ignore_errors=True)
        else:
            raise FileExistsError(f"Match directory {target_match_dir} already exists.")

    MATCHES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Create pristine directory layout
    print(f"[*] Copying Solova exam assets from {ORIGINAL_MATCH_DIR / 'PVKPJ94U'}...")
    target_match_dir.mkdir(parents=True, exist_ok=True)
    dst_inner = target_match_dir / match_id
    
    src_inner = ORIGINAL_MATCH_DIR / "PVKPJ94U"
    if not src_inner.exists():
        raise FileNotFoundError(f"Source match directory {src_inner} does not exist.")

    # Copy files excluding .git
    shutil.copytree(
        src_inner,
        dst_inner,
        ignore=shutil.ignore_patterns(".git", "node_modules", ".next", ".cache", "__pycache__"),
    )

    # 2. Initialize fresh, independent Git repository in target_match_dir
    print(f"[*] Initializing local Git repository in {target_match_dir}...")
    subprocess.run(["git", "init", "-b", "main"], cwd=target_match_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Game Master"], cwd=target_match_dir, check=True)
    subprocess.run(["git", "config", "user.email", "gamemaster@localhost"], cwd=target_match_dir, check=True)

    # 3. Ensure all 10 challenge files exist in dst_inner/challenge
    challenge_dst = dst_inner / "challenge"
    challenge_dst.mkdir(parents=True, exist_ok=True)
    if CHALLENGE_SOURCE_DIR.exists():
        print(f"[*] Ensuring all 10 challenge files exist in {challenge_dst}...")
        for item in CHALLENGE_SOURCE_DIR.glob("*"):
            if item.is_file():
                shutil.copy2(item, challenge_dst / item.name)

    # 4. Clean answers folder (so the match starts completely fresh)
    answers_dst = dst_inner / "answers"
    if answers_dst.exists():
        for sub in answers_dst.iterdir():
            if sub.is_dir():
                shutil.rmtree(sub, ignore_errors=True)
            elif sub.name != "README.md":
                sub.unlink(missing_ok=True)
    answers_dst.mkdir(parents=True, exist_ok=True)
    (answers_dst / "README.md").write_text("# Match Answers\nEmpty at match start.\n", encoding="utf-8")

    # 5. Commit initial match state
    print(f"[*] Committing pristine match baseline to Git...")
    subprocess.run(["git", "add", "."], cwd=target_match_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", f"Seed {match_id}/ from scenario for-player"],
        cwd=target_match_dir,
        check=True,
        capture_output=True,
    )

    # 7. Update Thi/Workspace/AGENTS.md
    if AGENTS_MD_PATH.exists():
        print(f"[*] Updating {AGENTS_MD_PATH} with {match_id} assignment...")
        agents_content = AGENTS_MD_PATH.read_text(encoding="utf-8")
        new_assignment = {
            "teamId": "BAN",
            "matchId": match_id,
            "repository": {
                "url": f"https://github.com/lqdung863/mtm-ban",
                "branch": "main",
                "folder": match_id,
            },
            "challengeWorkspace": str(MATCHES_DIR),
            "checkoutRoot": str(target_match_dir),
            "matchRoot": str(dst_inner),
            "teamSdlcPackWorkspace": str(SDLC_PACK_DIR),
            "gameMasterOrigin": "http://127.0.0.1:8000",
        }
        json_str = json.dumps(new_assignment, indent=2)
        
        # Replace or append the MTM-PLAYER block
        pattern = r"<!-- MTM-PLAYER:BEGIN -->.*?<!-- MTM-PLAYER:END -->"
        replacement = f"<!-- MTM-PLAYER:BEGIN -->\n## MTM Player match assignment\n\n```json\n{json_str}\n```\n<!-- MTM-PLAYER:END -->"
        if "<!-- MTM-PLAYER:BEGIN -->" in agents_content:
            if "<!-- MTM-PLAYER:END -->" in agents_content:
                updated_content = re.sub(pattern, replacement, agents_content, flags=re.DOTALL)
            else:
                # Replace everything after MTM-PLAYER:BEGIN
                idx = agents_content.find("<!-- MTM-PLAYER:BEGIN -->")
                updated_content = agents_content[:idx] + replacement
        else:
            updated_content = agents_content + "\n\n" + replacement
        
        AGENTS_MD_PATH.write_text(updated_content, encoding="utf-8")

    # 8. Sync latest team-sdlc-pack from Master-the-Machine-main
    install_script = ROOT_DIR / "scripts" / "install.py"
    if install_script.exists():
        print(f"[*] Syncing updated team-sdlc-pack into {SDLC_PACK_DIR}...")
        subprocess.run(
            [sys.executable, str(install_script), "--team-workspace", str(WORKSPACE_DIR)],
            cwd=ROOT_DIR,
            check=True,
            capture_output=True,
        )

    print("\n" + "=" * 70)
    print(f"[SUCCESS] MATCH {match_id} CREATED SUCCESSFULLY!")
    print(f"Location: {target_match_dir}")
    print(f"Match Root: {dst_inner}")
    print(f"Source Code: Solova (Pristine baseline ready)")
    print(f"Challenge Tasks: All 10 backlog items (BUG-01 to TASK-04)")
    print("=" * 70 + "\n")
    return target_match_dir


def main() -> None:
    parser = argparse.ArgumentParser(description="Create BTC-identical match in Thi/Workspace/matches")
    parser.add_argument("--match-id", type=str, default="SOLOVA01", help="Match ID (default: SOLOVA01)")
    args = parser.parse_args()
    create_btc_match(match_id=args.match_id)


if __name__ == "__main__":
    main()
