import subprocess
from pathlib import Path
from types import SimpleNamespace

from runner.mcp_server import MCPHelperServer


def _git(cwd, *a):
    return subprocess.run(["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                          capture_output=True, text=True)


def _server(repo):
    srv = MCPHelperServer.__new__(MCPHelperServer)
    srv.checkout_root = Path(repo)
    srv._git_run = lambda cwd, *a, timeout=30: _git(cwd, *a)
    return srv


def test_already_cherry_picked_commits_are_not_picked_again(tmp_path):
    repo = tmp_path / "r"; repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("a"); _git(repo, "add", "."); _git(repo, "commit", "-qm", "base")
    _git(repo, "checkout", "-qb", "worker")
    (repo / "w.txt").write_text("w"); _git(repo, "add", "."); _git(repo, "commit", "-qm", "work")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", "main")
    srv = _server(repo)
    commits = _git(repo, "rev-list", "--reverse", f"main..{head}").stdout.split()
    assert srv._pending_worker_commits(commits, head) == commits       # first attempt: pick it
    assert _git(repo, "cherry-pick", *commits).returncode == 0         # integrated, then gate rejects
    assert srv._pending_worker_commits(commits, head) == []            # retry: nothing left to pick
