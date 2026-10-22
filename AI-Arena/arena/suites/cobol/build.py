#!/usr/bin/env python3
"""COBOL sub-benchmark generator.

    python build.py --out <dir> [--ids ID ...]
        Materialise every task as <dir>/<instance_id>/ (meta.json, repo/,
        tests/, solution/patch.diff). Pure Python and deterministic; the only
        network access is the first download of the pinned COBOLEval data
        (cached in .cache/ or $ARENA_COBOL_CACHE).

    python build.py --validate <dir> [--ids ID ...] [--jobs N]
        For each task run, in Docker (image arena-cobol:1), the same flow as
        the grader: copy repo/ to /app, git init+commit, git apply the patch,
        copy tests/ to /tests, bash /tests/test.sh, read reward.txt.
        "oracle" = gold patch (where one exists, must give 1),
        "nop"    = empty patch (must give 0).

    python build.py --regen [--products carddemo ...] [--jobs N]
        Maintainer step: run the reference programs on every test case in
        Docker and store the expected outputs under products/<p>/expected/
        (also records which cases the pre-change code passes). Run after
        editing items.json, a product or its cases.
"""
import argparse
import concurrent.futures as cf
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.request

SUITE = os.path.dirname(os.path.abspath(__file__))
IMAGE = "arena-cobol:1"
WORKDIR = "/app"

COBOLEVAL_COMMIT = "0bb96c3114bb2bb28e221e9d6000614781f8609d"
COBOLEVAL_URL = ("https://raw.githubusercontent.com/BloopAI/COBOLEval/%s/data/CobolEval.jsonl"
                 % COBOLEVAL_COMMIT)
COBOLEVAL_SHA256 = "b018d90bdc53b32c3c76b9a67462e3f572f894dd98f0f084fc779992c7d21264"

SKIP_DIRS = {"bin", ".git", "__pycache__"}


# ------------------------------------------------------------------ files
def read_text(path):
    with open(path, encoding="utf-8", newline="") as f:
        return f.read().replace("\r\n", "\n")


def write_text(path, text):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def load_tree(root):
    tree = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for fn in sorted(filenames):
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            tree[rel] = read_text(full)
    return tree


def write_tree(root, tree):
    for rel, text in sorted(tree.items()):
        write_text(os.path.join(root, *rel.split("/")), text)


def tree_hash(tree):
    h = hashlib.sha256()
    for rel in sorted(tree):
        h.update(rel.encode() + b"\0" + tree[rel].encode() + b"\0")
    return h.hexdigest()[:16]


def make_diff(old, new):
    """git-style unified diff between two {path: text} trees."""
    out = []
    for rel in sorted(set(old) | set(new)):
        a, b = old.get(rel), new.get(rel)
        if a == b:
            continue
        if b is None:
            raise ValueError("deleting files is not supported: " + rel)
        for t in (a, b):
            if t is not None and t and not t.endswith("\n"):
                raise ValueError("file must end with a newline: " + rel)
        out.append("diff --git a/%s b/%s\n" % (rel, rel))
        b_lines = b.splitlines(True)
        if a is None:
            out.append("new file mode 100644\n--- /dev/null\n+++ b/%s\n" % rel)
            out.append("@@ -0,0 +1,%d @@\n" % len(b_lines) if len(b_lines) != 1 else "@@ -0,0 +1 @@\n")
            out.extend("+" + l for l in b_lines)
            continue
        out.append("--- a/%s\n+++ b/%s\n" % (rel, rel))
        hunks = list(difflib.unified_diff(a.splitlines(True), b_lines, n=3))
        out.extend(hunks[2:])
    return "".join(out)


# ------------------------------------------------------------------ items
def load_items():
    return json.loads(read_text(os.path.join(SUITE, "items.json")))["items"]


def product_dir(name):
    return os.path.join(SUITE, "products", name)


def product_tree(name):
    return load_tree(os.path.join(product_dir(name), "repo"))


def apply_edits(tree, edits, what):
    """Apply find/replace edits. Options per edit: "occurrence" (1-based, else the
    text must be unique) and "delete_line" (remove the whole line containing it)."""
    tree = dict(tree)
    for e in edits:
        text = tree[e["file"]]
        n = text.count(e["find"])
        occ = e.get("occurrence")
        if (occ is None and n != 1) or (occ is not None and n < occ):
            raise ValueError("%s: 'find' occurs %d times in %s: %r" % (what, n, e["file"], e["find"]))
        idx = -1
        for _ in range(occ or 1):
            idx = text.index(e["find"], idx + 1)
        if e.get("delete_line"):
            start = text.rfind("\n", 0, idx) + 1
            end = text.index("\n", idx) + 1
            text = text[:start] + text[end:]
        else:
            start = text.rfind("\n", 0, idx) + 1
            end = text.find("\n", idx)
            end = len(text) if end < 0 else end
            old_line = text[start:end]
            new_line = text[start:idx] + e["replace"] + text[idx + len(e["find"]):end]
            if "\n" not in e["find"] + e["replace"] and old_line != old_line.rstrip(" "):
                # keep the fixed-format line padding (columns 73-80) unchanged
                new_line = new_line.rstrip(" ").ljust(len(old_line))
            text = text[:start] + new_line + text[end:]
        tree[e["file"]] = text
    return tree


def item_trees(item, cache={}):
    """(base_tree, gold_tree) for a BUG/PI/CR item."""
    key = item["id"]
    if key in cache:
        return cache[key]
    original = product_tree(item["product"])
    if item["type"] in ("BUG", "PI"):
        base = apply_edits(original, item["mutation"], item["id"])
        gold = original
    else:
        base = original
        gold = dict(original)
        overlay = os.path.join(product_dir(item["product"]), "changes", item["id"])
        if os.path.isdir(overlay):
            gold.update(load_tree(overlay))
        gold = apply_edits(gold, item.get("edits", []), item["id"])
    if base == gold:
        raise ValueError(item["id"] + ": base and gold are identical")
    cache[key] = (base, gold)
    return base, gold


def gold_key(item):
    return "original" if item["type"] in ("BUG", "PI") else item["id"]


def case_dir(product, case):
    return os.path.join(product_dir(product), "cases", case)


def load_case(product, case, expected_key):
    """{relpath: text} of a case directory including expected outputs."""
    d = case_dir(product, case)
    files = {"cmd": read_text(os.path.join(d, "cmd")), "checks": read_text(os.path.join(d, "checks"))}
    if os.path.isdir(os.path.join(d, "input")):
        for rel, text in load_tree(os.path.join(d, "input")).items():
            files["input/" + rel] = text
    exp = os.path.join(product_dir(product), "expected", expected_key, case)
    if not os.path.isdir(exp):
        raise SystemExit("missing expected outputs %s - run build.py --regen" % exp)
    for f in files["checks"].split():
        files["expected/" + f] = read_text(os.path.join(exp, f))
    return files


def load_status(product):
    p = os.path.join(product_dir(product), "expected", "status.json")
    return json.loads(read_text(p)) if os.path.exists(p) else {}


# ------------------------------------------------------------------ COBOLEval
def coboleval_records():
    cache_dir = os.environ.get("ARENA_COBOL_CACHE", os.path.join(SUITE, ".cache"))
    path = os.path.join(cache_dir, "CobolEval-%s.jsonl" % COBOLEVAL_COMMIT[:12])
    if not os.path.exists(path):
        os.makedirs(cache_dir, exist_ok=True)
        print("fetching", COBOLEVAL_URL, file=sys.stderr)
        with urllib.request.urlopen(COBOLEVAL_URL, timeout=120) as r:
            data = r.read()
        if hashlib.sha256(data).hexdigest() != COBOLEVAL_SHA256:
            raise SystemExit("CobolEval.jsonl checksum mismatch")
        with open(path, "wb") as f:
            f.write(data)
    with open(path, "rb") as f:
        data = f.read()
    if hashlib.sha256(data).hexdigest() != COBOLEVAL_SHA256:
        raise SystemExit("cached CobolEval.jsonl checksum mismatch: " + path)
    return [json.loads(l) for l in data.decode("utf-8").splitlines() if l.strip()]


def swap_sections(src):
    """Port of COBOLEval scripts/generate.py swap_sections: put the
    WORKING-STORAGE SECTION before the LINKAGE SECTION (the dataset prompt has
    them the other way round, which GnuCOBOL rejects)."""
    working_storage, linkage, procedure, begin = [], [], [], []
    current = begin
    for line in src.split("\n"):
        stripped = line.strip().upper()
        if stripped.startswith("WORKING-STORAGE SECTION."):
            current = working_storage
        elif stripped.startswith("LINKAGE SECTION."):
            current = linkage
        elif stripped.startswith("PROCEDURE DIVISION"):
            current = procedure
            line = "       PROCEDURE DIVISION USING LINKED-ITEMS."
        current.append(line)
    return "\n".join(begin + working_storage + linkage + procedure)


def tidy(text):
    lines = [l.rstrip() for l in text.split("\n")]
    while lines and not lines[-1]:
        lines.pop()
    return "\n".join(lines) + "\n"


def spec_comments(prompt):
    lines = []
    for l in prompt.splitlines():
        s = l.strip()
        if s.startswith("*"):
            body = s[1:].strip()
            if body.startswith("Complete the WORKING-STORAGE") or body.startswith("Store the result"):
                continue
            lines.append(s[1:].rstrip()[1:] if s[1:2] == " " else s[1:].rstrip())
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def build_coboleval(out, ids):
    tasks = []
    harness = os.path.join(SUITE, "coboleval")
    test_sh = read_text(os.path.join(harness, "test.sh"))
    check_py = read_text(os.path.join(harness, "check.py"))
    for rec in coboleval_records():
        num = int(rec["task_id"].split("/")[1])
        iid = "cobol-eval-%03d" % num
        if ids and iid not in ids:
            continue
        pid = re.search(r"PROGRAM-ID\.\s+([A-Z0-9-]+)", rec["prompt"]).group(1)
        prog_rel = "src/%s.cbl" % pid
        prompt = rec["prompt"].replace("\r\n", "\n")
        skeleton = tidy(swap_sections(prompt))
        spec = spec_comments(rec["prompt"])
        repo = {
            prog_rel: skeleton,
            "README.md": (
                "# %s\n\n"
                "COBOL subprogram `%s` (source: `%s`).\n\n"
                "It is called by other programs as `CALL \"%s\" USING LINKED-ITEMS`; the\n"
                "layout of `LINKED-ITEMS` is defined in the LINKAGE SECTION and must not change.\n"
                "The result is returned in the `RESULT` field.\n\n"
                "Build (GnuCOBOL 3.2), together with a calling program:\n\n"
                "    cobc -w -fformat=variable -x caller.cbl %s\n"
                % (pid, pid, prog_rel, pid, prog_rel)),
        }
        tests = []
        files = {"test.sh": test_sh, "check.py": check_py}
        for k, t in enumerate(rec["tests"]):
            name = "test_%d" % k
            files["callers/%s.cbl" % name] = t["test"].replace("\r\n", "\n")
            tests.append({"name": name, "caller": "callers/%s.cbl" % name, "result": t["result"]})
        files["tests.json"] = json.dumps({
            "source": "BloopAI/COBOLEval@%s %s" % (COBOLEVAL_COMMIT, rec["task_id"]),
            "entry_point": rec["entry_point"], "program_path": prog_rel,
            "tests": tests, "required": [t["name"] for t in tests]}, indent=1) + "\n"
        description = (
            "Complete the COBOL program `%s`.\n\n"
            "The file contains the program skeleton: the IDENTIFICATION DIVISION and a DATA DIVISION "
            "with an empty WORKING-STORAGE SECTION and the LINKAGE SECTION (`LINKED-ITEMS`) that "
            "callers pass in, followed by comments describing what the program must do. Add the "
            "WORKING-STORAGE items you need and, after the LINKAGE SECTION, the PROCEDURE DIVISION "
            "(`PROCEDURE DIVISION USING LINKED-ITEMS.`); store the answer in `RESULT` and end the "
            "source with `END PROGRAM %s.`. Do not change the layout of the LINKAGE SECTION.\n\n"
            "The program is compiled with GnuCOBOL 3.2 together with a calling program "
            "(`cobc -w -fformat=variable -x caller.cbl %s`) and invoked with "
            "`CALL \"%s\" USING LINKED-ITEMS`.\n\n"
            "Specification:\n\n%s\n" % (prog_rel, pid, prog_rel, pid, spec))
        first = next((l.strip() for l in spec.splitlines() if l.strip() and not l.strip().startswith(">>>")), pid)
        meta = {
            "instance_id": iid, "type": "TASK", "language": "cobol", "repo": "coboleval-" + pid.lower(),
            "title": "Implement %s: %s" % (pid, first.rstrip(".")[:90]),
            "description": description, "pi_description": None, "mvs": None,
            "image": IMAGE, "workdir": WORKDIR,
            "source": {"dataset": "BloopAI/COBOLEval", "commit": COBOLEVAL_COMMIT,
                       "task_id": rec["task_id"], "license": "MIT"},
        }
        tdir = os.path.join(out, iid)
        if os.path.isdir(tdir):
            shutil.rmtree(tdir)
        write_tree(os.path.join(tdir, "repo"), repo)
        write_tree(os.path.join(tdir, "tests"), files)
        sol = os.path.join(harness, "solutions", "%03d.cbl" % num)
        if os.path.exists(sol):
            gold = dict(repo)
            # assembled like COBOLEval's generator: prompt + completion, sections swapped
            gold[prog_rel] = tidy(swap_sections(prompt + "\n" + read_text(sol)))
            write_text(os.path.join(tdir, "solution", "patch.diff"), make_diff(repo, gold))
        write_text(os.path.join(tdir, "meta.json"), json.dumps(meta, indent=1) + "\n")
        tasks.append(iid)
    return tasks


# ------------------------------------------------------------------ product items
def build_product_item(out, item, status):
    base, gold = item_trees(item)
    product = item["product"]
    gk = gold_key(item)
    cases = {c: load_case(product, c, gk) for c in item["tests"]}
    st = status.get(item["id"])
    if st is None or st.get("fingerprint") != item_fingerprint(item):
        raise SystemExit("%s: expected outputs are stale - run build.py --regen" % item["id"])
    passing = [c for c in item["tests"] if st["base"].get(c) and c not in item.get("mvs", [])]
    visible = item.get("visible", passing[:2])

    repo = dict(base)
    for c in visible:
        for rel, text in cases[c].items():
            repo["tests/cases/%s/%s" % (c, rel)] = text
    mvs = None
    if item.get("mvs"):
        with_mvs = dict(repo)
        for c in item["mvs"]:
            for rel, text in cases[c].items():
                with_mvs["tests/cases/%s/%s" % (c, rel)] = text
        mvs = {"tests": list(item["mvs"]), "test_patch": make_diff(repo, with_mvs)}

    tests = {"test.sh": read_text(os.path.join(SUITE, "harness", "test.sh")),
             "run_case.sh": gold["tests/run_case.sh"],
             "tests.txt": "".join(c + "\n" for c in item["tests"])}
    for c, files in cases.items():
        for rel, text in files.items():
            tests["cases/%s/%s" % (c, rel)] = text

    gold_repo = dict(gold)
    for rel in repo:
        gold_repo.setdefault(rel, repo[rel])
    meta = {
        "instance_id": item["id"], "type": item["type"], "language": "cobol",
        "repo": read_json(os.path.join(product_dir(product), "product.json"))["repo"],
        "title": item["title"],
        "description": item["description"] if item["type"] != "PI" else "",
        "pi_description": item.get("pi_description") if item["type"] == "PI" else None,
        "mvs": mvs if item["type"] in ("BUG", "CR") else None,
        "image": IMAGE, "workdir": WORKDIR,
    }
    tdir = os.path.join(out, item["id"])
    if os.path.isdir(tdir):
        shutil.rmtree(tdir)
    write_tree(os.path.join(tdir, "repo"), repo)
    write_tree(os.path.join(tdir, "tests"), tests)
    write_text(os.path.join(tdir, "solution", "patch.diff"), make_diff(repo, gold_repo))
    write_text(os.path.join(tdir, "meta.json"), json.dumps(meta, indent=1) + "\n")


def read_json(p):
    return json.loads(read_text(p))


def item_fingerprint(item):
    base, gold = item_trees(item)
    h = hashlib.sha256()
    h.update(tree_hash(base).encode() + tree_hash(gold).encode())
    for c in item["tests"]:
        d = case_dir(item["product"], c)
        h.update(c.encode() + tree_hash(load_tree(d)).encode())
    return h.hexdigest()[:16]


# ------------------------------------------------------------------ docker helpers
def docker_path(p):
    return os.path.abspath(p)


def docker(args, timeout=3600):
    return subprocess.run(["docker"] + args, capture_output=True, text=True, timeout=timeout,
                          encoding="utf-8", errors="replace")


REGEN_SCRIPT = r"""
set -u
cp -r /variant/. /app
cd /app
if ! bash build.sh > /results/_build.log 2>&1; then touch /results/_BUILD_FAILED; exit 0; fi
for c in /cases/*/; do
  n="$(basename "$c")"
  bash tests/run_case.sh "$c" "/tmp/w/$n" > "/results/_$n.log" 2>&1
  mkdir -p "/results/$n"
  while read -r f; do
    [ -n "$f" ] && [ -f "/tmp/w/$n/data/out/$f" ] && cp "/tmp/w/$n/data/out/$f" "/results/$n/$f"
  done < "$c/checks"
done
"""


def run_variant(product, tree, cases, workroot, label):
    vdir = tempfile.mkdtemp(prefix="v-", dir=workroot)
    write_tree(os.path.join(vdir, "variant"), tree)
    for c in cases:
        src = case_dir(product, c)
        shutil.copytree(src, os.path.join(vdir, "cases", c))
    os.makedirs(os.path.join(vdir, "results"))
    r = docker(["run", "--rm", "--network", "none",
                "-v", docker_path(os.path.join(vdir, "variant")) + ":/variant:ro",
                "-v", docker_path(os.path.join(vdir, "cases")) + ":/cases:ro",
                "-v", docker_path(os.path.join(vdir, "results")) + ":/results",
                IMAGE, "bash", "-c", REGEN_SCRIPT.replace("\r", "")])
    res = os.path.join(vdir, "results")
    if os.path.exists(os.path.join(res, "_BUILD_FAILED")):
        raise SystemExit("%s: build failed:\n%s" % (label, read_text(os.path.join(res, "_build.log"))[-3000:]))
    if r.returncode != 0:
        raise SystemExit("%s: docker failed: %s" % (label, r.stderr[-2000:]))
    outputs = {}
    for c in cases:
        checks = read_text(os.path.join(case_dir(product, c), "checks")).split()
        outputs[c] = {}
        for f in checks:
            p = os.path.join(res, c, f)
            outputs[c][f] = read_text(p) if os.path.exists(p) else None
    return outputs, vdir


def regen(products, jobs):
    items = [i for i in load_items() if i.get("product") in products]
    workroot = tempfile.mkdtemp(prefix="cobol-regen-")
    for product in products:
        pitems = [i for i in items if i["product"] == product]
        if not pitems:
            continue
        variants = {}   # key -> (tree, set(cases))
        for it in pitems:
            base, gold = item_trees(it)
            gk = gold_key(it)
            variants.setdefault(("gold", gk), [gold, set()])[1].update(it["tests"])
            variants.setdefault(("base", it["id"]), [base, set()])[1].update(it["tests"])
        print("[%s] running %d variants" % (product, len(variants)))
        results = {}
        with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
            futs = {ex.submit(run_variant, product, tree, sorted(cs), workroot, "%s/%s" % k): k
                    for k, (tree, cs) in variants.items()}
            for f in cf.as_completed(futs):
                results[futs[f]] = f.result()[0]
        exp_root = os.path.join(product_dir(product), "expected")
        if os.path.isdir(exp_root):
            shutil.rmtree(exp_root)
        status = {}
        problems = []
        for (kind, key), outs in sorted(results.items()):
            if kind != "gold":
                continue
            for c, files in outs.items():
                for f, text in files.items():
                    if text is None:
                        problems.append("gold %s case %s: missing output %s" % (key, c, f))
                        continue
                    write_text(os.path.join(exp_root, key, c, f), text)
                rc = files.get("rc.txt")
                if rc is not None and rc.strip() not in ("0", "4"):
                    problems.append("gold %s case %s: rc=%s" % (key, c, rc.strip()))
        for it in pitems:
            gold_out = results[("gold", gold_key(it))]
            base_out = results[("base", it["id"])]
            st = {c: base_out[c] == gold_out[c] for c in it["tests"]}
            status[it["id"]] = {"fingerprint": item_fingerprint(it), "base": st}
            failing = [c for c, ok in st.items() if not ok]
            line = "  %-26s %-4s base fails %d/%d: %s" % (it["id"], it["type"], len(failing), len(st),
                                                      " ".join(failing))
            print(line)
            if not failing:
                problems.append(it["id"] + ": base passes every test")
            if it["type"] in ("BUG", "CR") and not any(not st[c] for c in it.get("mvs", [])):
                problems.append(it["id"] + ": no MVS test fails on the base version")
        write_text(os.path.join(exp_root, "status.json"), json.dumps(status, indent=1, sort_keys=True) + "\n")
        for p in problems:
            print("PROBLEM:", p)
    shutil.rmtree(workroot, ignore_errors=True)


# ------------------------------------------------------------------ validate
VALIDATE_SCRIPT = r"""
set -u
cp -r /task/repo/. /app
cd /app
git init -q && git add -A && git commit -q -m base
if [ -n "${TEST_PATCH:-}" ]; then
  git apply --check /task/test_patch.diff || echo "TEST_PATCH_DOES_NOT_APPLY"
fi
if [ -n "${PATCH:-}" ]; then
  if ! git apply --whitespace=nowarn "$PATCH"; then echo "PATCH_FAILED"; fi
fi
mkdir -p /tests && cp -r /task/tests/. /tests
bash /tests/test.sh > /tmp/test.out 2>&1
echo "REWARD=$(cat /logs/verifier/reward.txt 2>/dev/null)"
echo "OUTPUT=$(tr -d '\n' < /logs/verifier/output.json 2>/dev/null)"
"""


def validate_one(task_dir, mode):
    meta = read_json(os.path.join(task_dir, "meta.json"))
    env = []
    tmp = None
    if mode == "oracle":
        env += ["-e", "PATCH=/task/solution/patch.diff"]
    mount = task_dir
    if meta.get("mvs") and mode == "oracle":
        tmp = tempfile.mkdtemp(prefix="val-")
        shutil.copytree(task_dir, os.path.join(tmp, "t"))
        write_text(os.path.join(tmp, "t", "test_patch.diff"), meta["mvs"]["test_patch"])
        mount = os.path.join(tmp, "t")
        env += ["-e", "TEST_PATCH=1"]
    r = docker(["run", "--rm", "--network", "none", "-v", docker_path(mount) + ":/task:ro"] + env
               + [meta["image"], "bash", "-c", VALIDATE_SCRIPT.replace("\r", "")], timeout=3600)
    if tmp:
        shutil.rmtree(tmp, ignore_errors=True)
    out = r.stdout
    m = re.search(r"REWARD=(\S*)", out)
    reward = m.group(1) if m else "?"
    notes = [w for w in ("PATCH_FAILED", "TEST_PATCH_DOES_NOT_APPLY") if w in out]
    tests = None
    m = re.search(r"OUTPUT=(.*)", out)
    if m and m.group(1).strip():
        try:
            tests = json.loads(m.group(1))["tests"]
        except ValueError:
            pass
    if tests is not None:
        names = [t["name"] for t in tests]
        if meta.get("mvs"):
            missing = [n for n in meta["mvs"]["tests"] if n not in names]
            if missing:
                notes.append("MVS_NOT_IN_OUTPUT:" + ",".join(missing))
    if reward == "?":
        notes.append("NO_REWARD " + (r.stderr or out)[-300:])
    return meta, mode, reward, notes


def validate(out, ids, jobs):
    tasks = sorted(d for d in os.listdir(out) if os.path.exists(os.path.join(out, d, "meta.json")))
    if ids:
        tasks = [t for t in tasks if t in ids]
    work = []
    for t in tasks:
        td = os.path.join(out, t)
        work.append((td, "nop"))
        if os.path.exists(os.path.join(td, "solution", "patch.diff")):
            work.append((td, "oracle"))
    results = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        futs = [ex.submit(validate_one, td, mode) for td, mode in work]
        for i, f in enumerate(cf.as_completed(futs), 1):
            meta, mode, reward, notes = f.result()
            results.append((meta, mode, reward, notes))
            if i % 25 == 0:
                print("  ... %d/%d runs" % (i, len(work)), file=sys.stderr)
    # summary
    groups = {}
    bad = []
    for meta, mode, reward, notes in results:
        key = (meta["type"], meta["repo"] if meta["type"] != "TASK" else "coboleval")
        g = groups.setdefault(key, {"tasks": set(), "oracle": [0, 0], "nop": [0, 0]})
        g["tasks"].add(meta["instance_id"])
        want = "1" if mode == "oracle" else "0"
        g[mode][1] += 1
        if reward == want and not notes:
            g[mode][0] += 1
        else:
            bad.append("%s %s reward=%s %s" % (meta["instance_id"], mode, reward, " ".join(notes)))
    print("\n%-5s %-16s %6s %12s %12s" % ("type", "repo", "tasks", "oracle=1", "nop=0"))
    for (typ, repo), g in sorted(groups.items()):
        o = "%d/%d" % tuple(g["oracle"]) if g["oracle"][1] else "-"
        print("%-5s %-16s %6d %12s %12s" % (typ, repo, len(g["tasks"]), o, "%d/%d" % tuple(g["nop"])))
    tot_o = [sum(g["oracle"][i] for g in groups.values()) for i in (0, 1)]
    tot_n = [sum(g["nop"][i] for g in groups.values()) for i in (0, 1)]
    print("%-5s %-16s %6d %12s %12s" % ("ALL", "", len(tasks), "%d/%d" % tuple(tot_o), "%d/%d" % tuple(tot_n)))
    if bad:
        print("\nUNEXPECTED:")
        for b in sorted(bad):
            print("  " + b)
    return not bad


# ------------------------------------------------------------------ main
def build(out, ids):
    os.makedirs(out, exist_ok=True)
    built = build_coboleval(out, ids)
    statuses = {}
    for item in load_items():
        if ids and item["id"] not in ids:
            continue
        p = item["product"]
        if p not in statuses:
            statuses[p] = load_status(p)
        build_product_item(out, item, statuses[p])
        built.append(item["id"])
    counts = {}
    for t in built:
        m = read_json(os.path.join(out, t, "meta.json"))
        counts[(m["type"], m["repo"] if m["type"] != "TASK" else "coboleval")] = \
            counts.get((m["type"], m["repo"] if m["type"] != "TASK" else "coboleval"), 0) + 1
    print("wrote %d tasks to %s" % (len(built), out))
    for k in sorted(counts):
        print("  %-5s %-16s %d" % (k[0], k[1], counts[k]))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out")
    ap.add_argument("--validate")
    ap.add_argument("--regen", action="store_true")
    ap.add_argument("--products", nargs="*")
    ap.add_argument("--ids", nargs="*")
    ap.add_argument("--jobs", type=int, default=6)
    a = ap.parse_args()
    ids = set(a.ids) if a.ids else None
    if a.regen:
        prods = a.products or sorted(os.listdir(os.path.join(SUITE, "products")))
        regen(prods, a.jobs)
    elif a.out:
        build(a.out, ids)
    elif a.validate:
        sys.exit(0 if validate(a.validate, ids, a.jobs) else 1)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
