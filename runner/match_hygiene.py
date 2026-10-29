"""Remove local historical task material only while the Player runtime is stopped.

Official Player/GM audit records and host-owned transcripts are never touched.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Any
import urllib.request

CODE = re.compile(r"^[A-Z0-9]{8}$")


def history_candidates(workspace: Path, keep_match_id: str) -> list[Path]:
    workspace = workspace.resolve()
    if not CODE.fullmatch(keep_match_id):
        raise ValueError("Expected a public match code")
    result = []
    for base in (workspace / "matches", workspace / "matches" / ".workers",
                 workspace / "team-sdlc-pack" / "state", workspace / "state"):
        if base.is_symlink() or not base.resolve().is_relative_to(workspace):
            raise ValueError("Unsafe history root")
        if not base.is_dir():
            continue
        for path in base.iterdir():
            if CODE.fullmatch(path.name) and path.name != keep_match_id:
                if path.is_symlink() or not path.resolve().is_relative_to(workspace):
                    raise ValueError("Unsafe history target")
                result.append(path)
    for base in (workspace / ".mtm", workspace / "team-sdlc-pack" / ".mtm"):
        if base.is_symlink():
            raise ValueError("Unsafe credential-cache root")
        if base.is_dir():
            for path in base.iterdir():
                if (re.fullmatch(r"[A-Z0-9]{8}\.session\.json", path.name)
                        and not path.name.startswith(keep_match_id + ".")) or path.name in ("submissions.db", "submissions.db-wal", "submissions.db-shm"):
                    if path.is_symlink() or not path.is_file():
                        raise ValueError("Unsafe historical cache target")
                    result.append(path)
    return sorted(set(result))


def purge_history(workspace: Path, keep_match_id: str, player_status: dict[str, Any]) -> list[str]:
    cli = player_status.get("cliSupervisor") or {}
    if cli.get("status") != "STOPPED" or cli.get("pid"):
        raise ValueError("STOPPED Player runtime with no live process is required")
    if any(row.get("pendingOperations", 0) for row in player_status.get("rounds", []) if row.get("matchId") == player_status.get("activeMatchId")):
        raise ValueError("Pending Player operation prevents history cleanup")
    candidates = history_candidates(workspace, keep_match_id)
    removed = []
    for path in candidates:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        removed.append(str(path.relative_to(workspace.resolve())))
    return removed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--keep-match", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.apply:
        with urllib.request.urlopen("http://127.0.0.1:54321/manage/status", timeout=10) as response:
            status = json.load(response)
        removed = purge_history(args.workspace, args.keep_match, status)
        print(json.dumps({"status": "LOCAL_HISTORY_REMOVED", "removed": removed, "officialAudit": "PRESERVED"}))
    else:
        print(json.dumps({"candidates": [str(p) for p in history_candidates(args.workspace, args.keep_match)]}))


if __name__ == "__main__":
    main()
