"""Git repository management: init, diff, status, commit."""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional


def normalize_git_url(url: str) -> str:
    """Normalize git remote URLs for robust comparison.

    Strips trailing .git, trailing slashes, standardizes path separators,
    and standardizes case for reliable comparison across platforms.
    """
    if not url:
        return ""
    u = str(url).strip().replace("\\", "/")
    u = u.rstrip("/")
    if u.lower().endswith(".git"):
        u = u[:-4].rstrip("/")
    return u.lower()


def verify_assigned_repository(
    checkout_root: Path | str,
    repo_descriptor: Dict[str, Any],
) -> Dict[str, Any]:
    """Verify all 5 Git invariants for PREPARE_GAME against host assignment:

    1. (checkout_root / ".git") exists and git rev-parse --is-inside-work-tree == "true".
    2. Git origin remote URL matches repo_descriptor["url"] (normalizing .git suffix).
    3. Current branch ref matches repo_descriptor["branch"].
    4. HEAD commit is a valid 40-character hex SHA and exists on the assigned branch ref (git rev-parse refs/heads/<branch>).
    5. Assigned folder exists under checkout_root without directory traversal.

    Args:
        checkout_root: Path to the local Git repository checkout.
        repo_descriptor: Dictionary containing assigned repository details ('url', 'branch', 'folder').

    Returns:
        Dict with verified details:
        - 'head_sha': 40-character hex commit SHA
        - 'branch': current verified branch name
        - 'remote_url': origin remote URL configured in the repository
        - 'match_folder': Path to the verified assigned folder
        - 'folder_path': Path to the verified assigned folder

    Raises:
        ValueError: If descriptor fields are missing, invalid, or URL/branch/folder mismatch.
        RuntimeError: If repository structure, git commands, or invariant checks fail.
    """
    if not isinstance(repo_descriptor, dict):
        raise ValueError("repo_descriptor must be a dictionary")

    # If caller passed full session descriptor with 'repository' key, unwrap it
    if "url" not in repo_descriptor and isinstance(repo_descriptor.get("repository"), dict):
        repo_descriptor = repo_descriptor["repository"]

    root = Path(checkout_root).resolve()

    # Invariant 1: (checkout_root / ".git") exists and git rev-parse --is-inside-work-tree == "true"
    git_dir = root / ".git"
    if not git_dir.exists():
        raise RuntimeError(f"Invariant 1 violated: .git directory not found in {root}")

    worktree_res = run_git(root, ["rev-parse", "--is-inside-work-tree"])
    if worktree_res.returncode != 0 or worktree_res.stdout.strip() != "true":
        raise RuntimeError(f"Invariant 1 violated: {root} is not inside a valid Git work tree")

    # Invariant 2: Git origin remote URL matches repo_descriptor["url"] (normalizing .git suffix)
    expected_url = repo_descriptor.get("url") or repo_descriptor.get("repositoryUrl")
    if not expected_url:
        raise ValueError("Invariant 2 violated: Missing 'url' in repository descriptor")

    rem_res = run_git(root, ["remote", "get-url", "origin"])
    if rem_res.returncode != 0 or not rem_res.stdout.strip():
        rem_res = run_git(root, ["config", "--get", "remote.origin.url"])
    if rem_res.returncode != 0 or not rem_res.stdout.strip():
        raise RuntimeError(f"Invariant 2 violated: Remote 'origin' is not configured in {root}")

    actual_url = rem_res.stdout.strip().splitlines()[0].strip()
    if normalize_git_url(actual_url) != normalize_git_url(str(expected_url)):
        raise ValueError(
            f"Invariant 2 violated: Origin remote URL mismatch. "
            f"Expected '{expected_url}', but repository is configured with '{actual_url}'"
        )

    # Invariant 3: Current branch ref matches repo_descriptor["branch"]
    expected_branch = repo_descriptor.get("branch") or repo_descriptor.get("branchName")
    if not expected_branch:
        raise ValueError("Invariant 3 violated: Missing 'branch' in repository descriptor")

    expected_branch_str = str(expected_branch).strip()
    if expected_branch_str.startswith("refs/heads/"):
        expected_branch_str = expected_branch_str[len("refs/heads/"):]

    branch_res = run_git(root, ["rev-parse", "--abbrev-ref", "HEAD"])
    if branch_res.returncode != 0 or not branch_res.stdout.strip():
        raise RuntimeError(
            f"Invariant 3 violated: Failed to determine current branch in {root}: {branch_res.stderr}"
        )

    actual_branch = branch_res.stdout.strip()
    if actual_branch == "HEAD":
        raise RuntimeError(
            f"Invariant 3 violated: Repository is in detached HEAD state, expected branch '{expected_branch_str}'"
        )
    if actual_branch != expected_branch_str:
        raise ValueError(
            f"Invariant 3 violated: Current branch '{actual_branch}' does not match assigned '{expected_branch_str}'"
        )

    # Invariant 4: HEAD commit is a valid 40-character hex SHA and exists on assigned branch ref
    sha_res = run_git(root, ["rev-parse", "HEAD"])
    if sha_res.returncode != 0 or not sha_res.stdout.strip():
        raise RuntimeError(f"Invariant 4 violated: Failed to resolve HEAD commit: {sha_res.stderr}")

    head_sha = sha_res.stdout.strip()
    if not re.match(r"^[0-9a-fA-F]{40}$", head_sha) or head_sha == "0" * 40:
        raise RuntimeError(f"Invariant 4 violated: HEAD commit SHA '{head_sha}' is not a valid 40-character hex SHA")

    branch_ref = f"refs/heads/{expected_branch_str}"
    branch_sha_res = run_git(root, ["rev-parse", branch_ref])
    if branch_sha_res.returncode != 0 or not branch_sha_res.stdout.strip():
        raise RuntimeError(f"Invariant 4 violated: Branch ref '{branch_ref}' cannot be resolved: {branch_sha_res.stderr}")

    branch_sha = branch_sha_res.stdout.strip()
    if branch_sha != head_sha:
        # Check if HEAD commit exists on the assigned branch ref
        anc_res = run_git(root, ["merge-base", "--is-ancestor", head_sha, branch_ref])
        if anc_res.returncode != 0:
            raise RuntimeError(
                f"Invariant 4 violated: HEAD commit '{head_sha}' does not exist on branch '{expected_branch_str}'"
            )

    # Invariant 5: Assigned folder exists under checkout_root without directory traversal
    assigned_folder = repo_descriptor.get("folder") or repo_descriptor.get("path")
    if assigned_folder is None:
        raise ValueError("Invariant 5 violated: Missing 'folder' in repository descriptor")

    assigned_folder_str = str(assigned_folder).strip()
    if not assigned_folder_str:
        raise ValueError("Invariant 5 violated: Empty 'folder' in repository descriptor")

    if assigned_folder_str in (".", "./", ".\\"):
        folder_path = root
    else:
        folder_path = (root / assigned_folder_str).resolve()
        try:
            folder_path.relative_to(root)
        except ValueError:
            raise ValueError(
                f"Invariant 5 violated: Assigned folder '{assigned_folder}' traverses outside checkout root {root}"
            )

    if not folder_path.exists():
        raise RuntimeError(
            f"Invariant 5 violated: Assigned folder '{assigned_folder}' does not exist in {root}"
        )
    if not folder_path.is_dir():
        raise RuntimeError(
            f"Invariant 5 violated: Assigned folder '{assigned_folder}' in {root} is not a directory"
        )

    return {
        "head_sha": head_sha,
        "branch": actual_branch,
        "remote_url": actual_url,
        "match_folder": folder_path,
        "folder_path": folder_path,
    }


def run_git(path: Path | str, args: List[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    """Execute a git command within a repository path."""
    cmd = ["git", "-C", str(path)] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )


def git_init(path: Path | str, user_name: str = "mtm-agent", user_email: str = "agent@local") -> None:
    """Initialize a fresh Git repository with an initial base commit."""
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    steps = [
        ["init", "-q"],
        ["config", "core.autocrlf", "false"],
        ["config", "user.name", user_name],
        ["config", "user.email", user_email],
        ["add", "-A"],
        ["commit", "-q", "-m", "base", "--allow-empty"],
        ["branch", "-M", "main"],
    ]
    for args in steps:
        res = run_git(p, args)
        if res.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed in {p}: {res.stderr}")


def git_status(path: Path | str) -> List[str]:
    """Return list of modified/untracked files relative to the repository root."""
    res = run_git(path, ["status", "--porcelain"])
    if res.returncode != 0:
        return []
    changed = []
    for line in res.stdout.splitlines():
        parts = line.strip().split(maxsplit=1)
        if len(parts) == 2:
            changed.append(parts[1])
    return changed


def git_diff(path: Path | str, base: str = "HEAD", cached: bool = False, stage_all: bool = True) -> str:
    """Return unified diff relative to base revision or index.

    If stage_all is True (default), newly created and modified files are staged first
    so the resulting diff captures all workspace changes relative to base.
    """
    if stage_all:
        run_git(path, ["add", "-A"])
        cached = True
    args = ["diff", "--binary"]
    if cached:
        args.append("--cached")
    if base:
        args.append(base)
    res = run_git(path, args)
    return res.stdout


def configure_git_identity(
    path: Path | str,
    user_name: str = "mtm-agent",
    user_email: str = "agent@local",
) -> None:
    """Explicitly configure git user identity and safe line-ending behavior."""
    p = Path(path)
    run_git(p, ["config", "user.name", user_name])
    run_git(p, ["config", "user.email", user_email])
    run_git(p, ["config", "core.autocrlf", "false"])


def git_clone(
    source: str | Path,
    target_dir: Path | str,
    user_name: str = "mtm-agent",
    user_email: str = "agent@local",
) -> None:
    """Clone a repository and configure explicit identity and settings."""
    target = Path(target_dir)
    res = subprocess.run(
        ["git", "clone", str(source), str(target)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode != 0:
        raise RuntimeError(f"git clone failed from {source} to {target}: {res.stderr}")
    configure_git_identity(target, user_name=user_name, user_email=user_email)


def git_commit(path: Path | str, message: str) -> str:
    """Stage all changes and commit, returning the commit SHA."""
    from .revision import get_head_sha
    run_git(path, ["add", "-A"])
    res = run_git(path, ["commit", "-m", message])
    if res.returncode != 0:
        raise RuntimeError(f"git commit failed: {res.stderr}")
    return get_head_sha(path)
