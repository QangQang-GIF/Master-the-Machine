#!/usr/bin/env python3
# Usage: python3 -I scripts/replay_submission_gate.py <code_root> <native.sqlite3> <answers_dir> <json {key:[evidence names]}>
"""Replay CWQNKAPC submissions through the submission gate of a given code version (structural replay).

Real data: submit.json written by the workers, MVS/template from Start Work (native sqlite).
Evidence files: same names as on the user's machine, placeholder bytes (content is not graded here).
"""
import json, sqlite3, subprocess, sys, tempfile
from pathlib import Path

code_root, db_path, answers_root = Path(sys.argv[1]), sys.argv[2], Path(sys.argv[3])
sys.path[:0] = [str(code_root), str(code_root / "src")]
from runner.mcp_server import announced_test_ids, MCPHelperServer  # noqa: E402
from mtm_competition.integration.git_guard import PreSubmissionGitGuard  # noqa: E402

EVIDENCE = json.loads(sys.argv[4])
c = sqlite3.connect(db_path)
for key in EVIDENCE:
    details = json.loads(c.execute("select data from tasks where key=?", (key,)).fetchone()[0]).get("start_details", {})
    with tempfile.TemporaryDirectory() as td:
        repo = Path(td)
        subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
        (repo / "challenge").mkdir()
        mvs = details.get("minimumVerificationScenarios")
        if isinstance(mvs, str):
            (repo / "challenge" / f"{key}-minimum-verification-scenarios.md").write_text(mvs, encoding="utf-8")
        ad = repo / "answers" / key
        (ad / "evidence").mkdir(parents=True)
        for name in EVIDENCE[key]:
            (ad / "evidence" / name).write_bytes(b"placeholder")
        answer = json.loads((answers_root / key / "submit.json").read_text(encoding="utf-8"))
        normalize = getattr(MCPHelperServer, "normalize_submit_answer", None)
        if normalize:
            answer = normalize(answer, ad)
            answer.pop("_notRun", None)
        (ad / "submit.json").write_text(json.dumps(answer), encoding="utf-8")
        g = lambda *a: subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *a],
                                      capture_output=True, text=True)
        g("add", "-A"); g("commit", "-qm", key)
        sha = g("rev-parse", "HEAD").stdout.strip()
        declares_partial = str(answer.get("status", "")).upper() == "PARTIAL"
        kwargs = dict(target_commit_sha=sha, backlog_item_key=key, answer_payload=answer, match_root=repo,
                      checkout_root=repo, require_remote=False, raise_on_error=True,
                      required_test_ids=announced_test_ids(details))
        try:
            PreSubmissionGitGuard(repo).validate_pre_submission(allow_partial=declares_partial, **kwargs)
            verdict = "ACCEPTED"
        except TypeError:
            verdict = "n/a"
        except Exception as exc:
            verdict = f"REJECTED: {str(exc)[:150]}"
        print(f"{key:7} status={answer.get('status'):8} {verdict}")
