"""Multi-SWE-bench (ByteDance-Seed): Java, TS, JS, Go, Rust, C, C++, Kotlin, Python.

Images are `mswebench/<org>_m_<repo>:pr-<n>` with the repo in /home/<repo>, the test patch
baked in and /home/fix-run.sh applying /home/fix.patch before running the tests. Grading is
the harness's default (human mode) flow: put the patch at /home/fix.patch, run fix-run.sh,
parse the log with the repo's own parser from the multi_swe_bench package.
"""
import importlib.abc
import importlib.util
import json
import os
import sys

from arena import core
from arena.adapters import Suite

VARIANTS = {"full": "ByteDance-Seed/Multi-SWE-bench", "mini": "ByteDance-Seed/Multi-SWE-bench_mini",
            "flash": "ByteDance-Seed/Multi-SWE-bench-flash"}
PR_FIELDS = ("org", "repo", "number", "state", "title", "body", "base", "resolved_issues", "fix_patch",
             "test_patch")
MVS_TESTS = 2
_PFX = "multi_swe_bench.harness.repos.python.qiskit"


class _QiskitAlias(importlib.abc.MetaPathFinder):
    """The wheel ships both repos/python/Qiskit and .../qiskit; Windows keeps one. Alias it."""

    def find_spec(self, name, path, target=None):
        if not name.startswith(_PFX):
            return None
        base = os.path.join(os.path.dirname(importlib.util.find_spec("multi_swe_bench").origin),
                            "harness", "repos", "python", "Qiskit")
        rest = name[len(_PFX):].lstrip(".")
        if not rest:
            return importlib.util.spec_from_file_location(name, os.path.join(base, "__init__.py"),
                                                          submodule_search_locations=[base])
        return importlib.util.spec_from_file_location(name, os.path.join(base, rest + ".py"))


def _instance(row):
    if not any(isinstance(f, _QiskitAlias) for f in sys.meta_path):
        sys.meta_path.insert(0, _QiskitAlias())
    from multi_swe_bench.harness.image import Config
    from multi_swe_bench.harness.instance import Instance
    from multi_swe_bench.harness.pull_request import PullRequest
    pr = PullRequest.from_dict({k: row["pr"][k] for k in PR_FIELDS})
    return Instance.create(pr, Config(need_clone=False, global_env=None, clear_env=False))


def _issue_text(r):
    issues = r.get("resolved_issues") or []
    parts = [f"{i.get('title') or ''}\n\n{i.get('body') or ''}".strip() for i in issues]
    text = "\n\n---\n\n".join(p for p in parts if p)
    return text or f"{r.get('title') or ''}\n\n{r.get('body') or ''}".strip()


class MultiSwe(Suite):
    name = "multi-swe"
    title = "Multi-SWE-bench"
    source = "huggingface.co/datasets/ByteDance-Seed/Multi-SWE-bench"

    def setup(self, args) -> int:
        from huggingface_hub import hf_hub_download, list_repo_files
        repo_id = VARIANTS[getattr(args, "msb", "full")]
        files = [f for f in list_repo_files(repo_id, repo_type="dataset") if f.endswith(".jsonl")]
        rows, seen = [], set()
        for f in files:
            print(f"  {f}", flush=True)
            path = hf_hub_download(repo_id, f, repo_type="dataset")
            for src in core.read_jsonl(__import__("pathlib").Path(path)):
                iid = src.get("instance_id") or f"{src['org']}__{src['repo']}-{src['number']}"
                if iid in seen:
                    continue
                seen.add(iid)
                lang = src.get("language") or f.split("/")[0]
                text = _issue_text(src)
                title = core.title_of(text)
                typ = core.classify(title, text)
                row = {
                    "instance_id": iid, "suite": self.name, "repo": f"{src['org']}/{src['repo']}",
                    "language": lang, "type": typ, "title": title, "description": text,
                    "pi_description": core.pi_description(text), "workdir": f"/home/{src['repo']}",
                    "pr": {k: src.get(k) for k in PR_FIELDS},
                    "required": sorted({t for k in ("f2p_tests", "p2p_tests", "s2p_tests", "n2p_tests")
                                        for t in (src.get(k) or {})}),
                    "patch": src["fix_patch"],
                }
                f2p = list(src.get("f2p_tests") or {})
                row["mvs"] = ({"tests": f2p[:MVS_TESTS], "test_patch": src["test_patch"]}
                              if typ in ("CR", "BUG") and f2p else None)
                try:
                    row["image"] = _instance(row).name()
                except Exception as e:  # repo without a harness class: cannot be graded
                    print(f"    skip {iid}: {e}", flush=True)
                    continue
                rows.append(row)
        return core.write_jsonl(core.SOURCES / f"{self.name}.jsonl", rows)

    def _run(self, row, patch):
        inst = _instance(row)
        dep = inst.dependency()
        fix_path = dep.fix_patch_path() if hasattr(dep, "fix_patch_path") else "/home/fix.patch"
        with core.Container(row["image"]) as c:
            c.put(os.path.dirname(fix_path), {os.path.basename(fix_path): patch or ""})
            code, log = c.sh(inst.fix_patch_run() + " 2>&1", timeout=3600)
        res = inst.parse_log(log)
        return res, log

    def check(self, row, patch):
        res, log = self._run(row, patch)
        tests = {}
        for t in (row["mvs"] or {}).get("tests", []):
            tests[t] = ("PASSED" if t in res.passed_tests else "FAILED" if t in res.failed_tests
                        else "SKIPPED" if t in res.skipped_tests else "MISSING")
        return {"ok": bool(tests) and all(s == "PASSED" for s in tests.values()), "tests": tests, "log": log}

    def grade(self, row, patch):
        res, log = self._run(row, patch)
        req = set(row["required"])
        missing = sorted(req - set(res.passed_tests))
        resolved = bool(req) and not missing and not (req & set(res.failed_tests))
        return {"resolved": resolved, "log": log,
                "detail": json.dumps({"required": len(req), "missing": missing[:20]})}
