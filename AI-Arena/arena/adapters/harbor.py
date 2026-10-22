"""Harbor-style task directories: image + tests/test.sh that writes /logs/verifier/reward.txt.

Used by SWE-bench Pro V2 (scaleapi/SWE-bench_Pro-os, v2/tasks) and by our COBOL suite
(arena/suites/cobol). Grading: apply the patch in the repo, copy tests/ to /tests, run
test.sh, read the reward (1 = resolved) and the per-test statuses in output.json.
"""
import hashlib
import io
import json
import subprocess
import sys
import tarfile
from pathlib import Path

from arena import core
from arena.adapters import Suite

SRC = core.DATA / "src"
PRO_REPO = "https://github.com/scaleapi/SWE-bench_Pro-os.git"
# Most V2 images keep the repo in /app, a few in /testbed.
PRO_WORKDIR = '"$( [ -d /app ] && echo /app || echo /testbed )"'
MVS_TESTS = 2


class HarborSuite(Suite):
    def task_dir(self, row) -> Path:
        return SRC / self.name / row["task_dir"]

    def _verify(self, row, patch):
        tdir = self.task_dir(row)
        with core.Container(row["image"]) as c:
            self.prepare(row, c)
            c.put("/arena", {"patch.diff": patch or ""})
            c.put_dir(tdir / "tests", "/tests")
            code, log = c.sh(f"{core.APPLY}cd {row['workdir']}\napply_patch /arena/patch.diff\n"
                             "mkdir -p /logs/verifier\nbash /tests/test.sh 2>&1", timeout=3600)
            reward = (c.read("/logs/verifier/reward.txt") or "0").strip()
            out = c.read("/logs/verifier/output.json")
        tests = {}
        if out:
            try:
                tests = {t.get("name", ""): t.get("status", "") for t in json.loads(out).get("tests", [])}
            except json.JSONDecodeError:
                pass
        return reward == "1", tests, log

    def check(self, row, patch):
        _, status, log = self._verify(row, patch)
        tests = {t: status.get(t, "MISSING") for t in (row["mvs"] or {}).get("tests", [])}
        return {"ok": bool(tests) and all(s == "PASSED" for s in tests.values()), "tests": tests, "log": log}

    def grade(self, row, patch):
        resolved, _, log = self._verify(row, patch)
        return {"resolved": resolved, "log": log}


def pro_type(specificity: list[str], title: str, text: str) -> str:
    kinds = {s.rsplit("_", 1)[-1] for s in specificity}
    if "bug" in kinds:
        return "PI" if core.INCIDENT.search(f"{title}\n{text[:600]}") else "BUG"
    if "feat" in kinds:
        return "CR"
    return "TASK"


class SwePro(HarborSuite):
    name = "swe-pro"
    title = "SWE-bench Pro (public V2)"
    source = "github.com/scaleapi/SWE-bench_Pro-os (v2/tasks) + huggingface.co/datasets/ScaleAI/SWE-bench_Pro"

    def setup(self, args) -> int:
        from datasets import load_dataset
        clone = SRC / "_SWE-bench_Pro-os.git"
        if not clone.exists():
            print("cloning SWE-bench_Pro-os (no checkout; task files are read from git) ...", flush=True)
            subprocess.run(["git", "clone", "-q", "--bare", "--depth", "1", PRO_REPO, str(clone)], check=True)
        # Task paths are too long for Windows; store each task under a short hash.
        tar = subprocess.run(["git", "-c", "core.autocrlf=false", "-C", str(clone), "archive", "--format=tar", "HEAD", "v2/tasks"],
                             capture_output=True, check=True).stdout
        out = SRC / self.name
        core.rmtree(out)
        dirs = {}
        with tarfile.open(fileobj=io.BytesIO(tar)) as t:
            for m in t:
                parts = m.name.split("/")
                if not m.isfile() or len(parts) < 4 or parts[3] in ("solution", "environment"):
                    continue  # the agent-side never needs the gold patch; keep it out of the data dir
                iid = parts[2]
                short = dirs.setdefault(iid, hashlib.sha1(iid.encode()).hexdigest()[:12])
                dest = out / short / "/".join(parts[3:])
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(t.extractfile(m).read())
        rows = []
        for r in load_dataset("ScaleAI/SWE-bench_Pro", split="test"):
            iid = r["instance_id"]
            if iid not in dirs:
                continue
            instr = (out / dirs[iid] / "instruction.md").read_text(encoding="utf-8")
            title = core.title_of(r["problem_statement"])
            typ = pro_type(core.as_list(r["issue_specificity"]), title, r["problem_statement"])
            f2p = core.as_list(r["fail_to_pass"])
            test_patch = (out / dirs[iid] / "tests" / "test_patch.patch").read_text(encoding="utf-8")
            rows.append({
                "instance_id": iid, "suite": self.name, "repo": r["repo"], "language": r["repo_language"],
                "type": typ, "title": title, "description": instr,
                "pi_description": core.pi_description(r["problem_statement"]),
                "image": r["docker_image"], "workdir": PRO_WORKDIR, "task_dir": dirs[iid],
                "mvs": {"tests": f2p[:MVS_TESTS], "test_patch": test_patch} if typ in ("CR", "BUG") else None,
                "test_patch": test_patch, "patch": r["patch"],
            })
        return core.write_jsonl(core.SOURCES / f"{self.name}.jsonl", rows)


class Cobol(HarborSuite):
    name = "cobol"
    title = "COBOL (COBOLEval + legacy batch programs)"
    source = "arena/suites/cobol (GnuCOBOL)"
    SUITE_DIR = core.ROOT / "suites" / "cobol"
    IMAGE = "arena-cobol:1"

    def setup(self, args) -> int:
        code, log = core.run(["docker", "build", "-t", self.IMAGE, str(self.SUITE_DIR)], timeout=3600)
        if code:
            raise RuntimeError(f"docker build {self.IMAGE} failed:\n{log[-3000:]}")
        out = SRC / self.name
        core.rmtree(out)
        subprocess.run([sys.executable, str(self.SUITE_DIR / "build.py"), "--out", str(out)], check=True)
        rows = []
        for meta_path in sorted(out.glob("*/meta.json")):
            m = json.loads(meta_path.read_text(encoding="utf-8"))
            sol = meta_path.parent / "solution" / "patch.diff"
            rows.append({
                **{k: m.get(k) for k in ("instance_id", "repo", "type", "title", "description", "mvs")},
                "suite": self.name, "language": "cobol",
                # each COBOLEval task is its own mini repo; count them as one product
                **({"repo": "coboleval"} if m["repo"].startswith("coboleval") else {}),
                "pi_description": m.get("pi_description") or core.pi_description(m["description"]),
                "image": m.get("image", self.IMAGE), "workdir": m.get("workdir", "/app"),
                "task_dir": meta_path.parent.name,
                "patch": sol.read_text(encoding="utf-8") if sol.exists() else None,
            })
        return core.write_jsonl(core.SOURCES / f"{self.name}.jsonl", rows)

    def images(self, row):
        return []  # built locally by setup

    def export(self, row, dest):
        import shutil
        shutil.copytree(self.task_dir(row) / "repo", dest, dirs_exist_ok=True)
        return []

    def prepare(self, row, c):
        c.put_dir(self.task_dir(row) / "repo", row["workdir"])
        c.sh(f"cd {row['workdir']} && git init -q && git config user.email a@b && git config user.name a "
             "&& git add -A && git commit -qm base")
