"""
Offline Git Repositories Fixture and Inspection Helpers.
Initializes a bare remote repository (remote.git) and working clone (checkout_root) in tmp_path.
Provides tree inspection and remote ref verification without external network.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest


def run_git(cwd: Path, args: List[str], check: bool = True, env: Optional[Dict[str, str]] = None) -> subprocess.CompletedProcess:
    """Run git command in specified directory."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    # Ensure standard locale and deterministic output
    full_env["LC_ALL"] = "C"
    full_env["GIT_TERMINAL_PROMPT"] = "0"

    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
        env=full_env,
    )
    if check and result.returncode != 0:
        raise RuntimeError(
            f"Git command failed: git {' '.join(args)}\n"
            f"Directory: {cwd}\n"
            f"Exit code: {result.returncode}\n"
            f"Stdout: {result.stdout}\n"
            f"Stderr: {result.stderr}"
        )
    return result


def verify_git_file_exists_in_tree(repo_path: Path, commit_sha: str, rel_posix_path: str) -> bool:
    """
    Check if a file exists in the git commit object tree.
    Uses git cat-file -e <commit_sha>:<rel_posix_path>.
    """
    clean_path = rel_posix_path.replace("\\", "/").strip("/")
    result = run_git(repo_path, ["cat-file", "-e", f"{commit_sha}:{clean_path}"], check=False)
    return result.returncode == 0


def read_git_file_content_from_tree(repo_path: Path, commit_sha: str, rel_posix_path: str) -> bytes:
    """
    Read the exact content bytes of a file from the git commit tree.
    Uses git show <commit_sha>:<rel_posix_path>.
    """
    clean_path = rel_posix_path.replace("\\", "/").strip("/")
    res = subprocess.run(
        ["git", "show", f"{commit_sha}:{clean_path}"],
        cwd=str(repo_path),
        capture_output=True,
        check=False,
    )
    if res.returncode != 0:
        raise FileNotFoundError(f"File {clean_path} not found in commit {commit_sha}: {res.stderr.decode('utf-8', errors='replace')}")
    return res.stdout


def verify_remote_ref_sha(repo_path: Path, remote_name: str, branch: str, expected_sha: str) -> bool:
    """
    Verify that the remote repository's branch ref matches the expected commit SHA.
    Uses git ls-remote <remote_name> refs/heads/<branch>.
    """
    ref = f"refs/heads/{branch}"
    res = run_git(repo_path, ["ls-remote", remote_name, ref], check=False)
    if res.returncode != 0:
        return False
    output = res.stdout.strip()
    if not output:
        return False
    parts = output.split()
    if not parts:
        return False
    remote_sha = parts[0]
    return remote_sha.lower() == expected_sha.lower()


def set_git_origin_url(repo_path: Path, new_url: str) -> None:
    """Set or update the origin remote URL in a git repository."""
    run_git(repo_path, ["remote", "set-url", "origin", new_url])


@pytest.fixture
def offline_git_env(tmp_path: Path) -> Dict[str, Any]:
    """
    Pytest fixture creating an isolated bare remote and cloned working directory.
    Configures local git identity, adds valid doctor.ps1 in env-require/,
    and initializes main branch with an initial commit.
    """
    base_dir = tmp_path / "git_env"
    base_dir.mkdir(parents=True, exist_ok=True)

    remote_dir = base_dir / "remote.git"
    run_git(base_dir, ["init", "--bare", str(remote_dir)])

    checkout_dir = base_dir / "checkout_root"
    run_git(base_dir, ["clone", str(remote_dir), str(checkout_dir)])

    run_git(checkout_dir, ["config", "user.name", "MTM Robot"])
    run_git(checkout_dir, ["config", "user.email", "bot@mtm.local"])
    # Avoid line-ending conversion issues across platforms
    run_git(checkout_dir, ["config", "core.autocrlf", "false"])

    match_root = checkout_dir / "repo"
    match_root.mkdir(parents=True, exist_ok=True)
    (match_root / "app.py").write_text("print('init')\n", encoding="utf-8")
    (match_root / "README.md").write_text("# Project\nInitial readme\n", encoding="utf-8")

    # Add valid environment doctor in env-require/ emitting 'Environment status: READY'
    match_env_req = match_root / "env-require"
    match_env_req.mkdir(parents=True, exist_ok=True)
    (match_env_req / "doctor.ps1").write_text(
        'Write-Output "Environment status: READY"\nexit 0\n',
        encoding="utf-8",
    )
    (match_env_req / "doctor.sh").write_text(
        '#!/bin/sh\necho "Environment status: READY"\nexit 0\n',
        encoding="utf-8",
    )

    checkout_env_req = checkout_dir / "env-require"
    checkout_env_req.mkdir(parents=True, exist_ok=True)
    (checkout_env_req / "doctor.ps1").write_text(
        'Write-Output "Environment status: READY"\nexit 0\n',
        encoding="utf-8",
    )
    (checkout_env_req / "doctor.sh").write_text(
        '#!/bin/sh\necho "Environment status: READY"\nexit 0\n',
        encoding="utf-8",
    )

    run_git(checkout_dir, ["add", "-A"])
    run_git(checkout_dir, ["commit", "-m", "initial commit with doctor"])
    run_git(checkout_dir, ["branch", "-M", "main"])
    run_git(checkout_dir, ["push", "-u", "origin", "main"])

    initial_sha = run_git(checkout_dir, ["rev-parse", "HEAD"]).stdout.strip()
    remote_url = run_git(checkout_dir, ["remote", "get-url", "origin"]).stdout.strip()

    return {
        "base_dir": base_dir,
        "remote_dir": remote_dir,
        "checkout_dir": checkout_dir,
        "match_root": match_root,
        "branch": "main",
        "initial_sha": initial_sha,
        "remote_url": remote_url,
        "origin_url": remote_url,
        "set_remote_url": lambda url: set_git_origin_url(checkout_dir, url),
    }
