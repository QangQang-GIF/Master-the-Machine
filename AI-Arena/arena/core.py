"""Shared pieces: paths, the catalog schema, item typing, points and Docker helpers.

Every suite adapter turns its source rows into the same normalised row:

  instance_id, suite, repo, language, type (TASK|CR|BUG|PI), title, description,
  pi_description, image, workdir, mvs ({"tests": [...], "test_patch": "..."} or None),
  plus adapter-specific fields the grader needs.

`data/sources/<suite>.jsonl` holds the full rows; `data/catalog.jsonl` holds the light
fields only (what `stats` and `sample` need).
"""
import io
import json
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parent
DATA = ROOT / "data"
SOURCES = DATA / "sources"
CATALOG = DATA / "catalog.jsonl"
SETS = DATA / "sets"
RESULTS = DATA / "results"
RUNS = DATA / "runs"

TYPES = ("BUG", "CR", "TASK", "PI")
# Points per item type, from the practice match layout (a 40-minute match is worth 1,000).
POINTS = {"PI": 250, "CR": 300, "BUG": 200, "TASK": 150}
CATALOG_FIELDS = ("id", "suite", "instance_id", "repo", "language", "type", "title", "image")

LANGUAGE_NAMES = {"js": "JavaScript", "ts": "TypeScript", "c++": "C++", "cpp": "C++", "py": "Python",
                  "python": "Python", "go": "Go", "java": "Java", "rust": "Rust", "c": "C", "php": "PHP",
                  "ruby": "Ruby", "kotlin": "Kotlin", "cobol": "COBOL", "javascript": "JavaScript",
                  "typescript": "TypeScript"}


def language_name(code: str) -> str:
    return LANGUAGE_NAMES.get(code.lower(), code)


# Stacks: what a match's product is built with. The five FPT stacks come first (most of the company's
# projects, so the most likely match product); every eval set keeps a quota of each. A stack is matched by
# repo (frameworks) or by language (the rest). Derived when the catalog is loaded, so no setup rerun.
FPT_STACKS = ("java", "dotnet", "react", "angular", "cobol")
STACK_REPOS = {
    "react": {"mui/material-ui", "preactjs/preact", "facebook/docusaurus", "element-hq/element-web",
              "protonmail/webclients", "Kong/insomnia"},
    "angular": set(),  # no public SWE-style Angular app benchmark yet: see suites/angular when it exists
}
STACK_LANGUAGES = {"java": "java", "cobol": "cobol", "dotnet": "cs", "python": "python", "go": "go",
                   "rust": "rust", "ruby": "ruby", "php": "php", "kotlin": "kotlin"}
STACK_NAMES = {"java": "Java", "dotnet": ".NET", "react": "React", "angular": "Angular", "cobol": "COBOL",
               "python": "Python", "go": "Go", "rust": "Rust", "c-cpp": "C/C++", "ruby": "Ruby", "php": "PHP",
               "kotlin": "Kotlin", "js-other": "JS/TS (other)"}
# Products rather than libraries: closest to the match, where the customer hands over a running product.
APP_REPOS = {"element-hq/element-web", "protonmail/webclients", "Kong/insomnia", "NodeBB/NodeBB",
             "elastic/logstash", "apache/druid", "carddemo", "course-labs", "internetarchive/openlibrary",
             "qutebrowser/qutebrowser", "navidrome/navidrome", "gravitational/teleport", "flipt-io/flipt",
             "cli/cli", "ansible/ansible", "future-architect/vuls", "jellyfin/jellyfin", "ThreeMammals/Ocelot",
             "ardalis/CleanArchitecture"}


# Suites whose MVS check runs the repo's whole test suite (their harness has no focused run).
HEAVY_SUITES = {"multi-swe"}
# Stacks whose Docker images are not published (Multi-SWE-bench Kotlin: mswebench/pinterest_m_ktlint and
# ankidroid images are missing from Docker Hub, 2026-10-22), so no item of theirs can run.
UNAVAILABLE_STACKS = {"kotlin"}


def stack_of(row) -> str:
    for stack, repos in STACK_REPOS.items():
        if row["repo"] in repos:
            return stack
    lang = row["language"].lower()
    if lang in ("c", "cpp", "c++"):
        return "c-cpp"
    if lang in ("js", "ts", "javascript", "typescript"):
        return "js-other"
    for stack, l in STACK_LANGUAGES.items():
        if lang == l:
            return stack
    return lang


def stack_name(stack: str) -> str:
    return STACK_NAMES.get(stack, stack)


# ---------------------------------------------------------------- item typing

FEATURE = re.compile(r"\b(add(ing)?|support|allow|feature|implement|introduce|new option|enable|"
                     r"should be able|would be nice|enhancement|proposal|request)\b", re.I)
CHORE = re.compile(r"\b(refactor|clean ?up|deprecat\w*|rename|docs?|documentation|typo|lint|"
                   r"upgrade|bump|migrate|chore|performance|speed up|simplif\w*)\b", re.I)
INCIDENT = re.compile(r"\b(crash\w*|traceback|exception|panic\w*|segfault|segmentation fault|hang\w*|"
                      r"freez\w*|regression|stack overflow|out of memory|500|core dump\w*|"
                      r"internal server error|data loss|infinite loop)\b", re.I)
BUGWORD = re.compile(r"\b(bug|fix|wrong|incorrect|error|fail\w*|broken|unexpected|doesn'?t work|"
                     r"not work\w*|issue)\b", re.I)


def classify(title: str, body: str) -> str:
    """Keyword rules: incident-like bugs -> PI, features -> CR, chores -> TASK, else BUG."""
    head = f"{title}\n{body[:600]}"
    if INCIDENT.search(head):
        return "PI"
    if FEATURE.search(title) or (FEATURE.search(head) and not BUGWORD.search(title)):
        return "CR"
    if CHORE.search(title):
        return "TASK"
    return "BUG"


def pi_description(text: str) -> str:
    """A production incident carries little information: keep only the first paragraph."""
    first = re.split(r"\n\s*\n", text.strip())[0]
    return first[:400]


def title_of(text: str) -> str:
    for line in text.strip().splitlines():
        line = line.strip().lstrip("#").strip()
        if line:
            return line[:120]
    return ""


def as_list(v):
    if isinstance(v, str):
        try:
            return json.loads(v)
        except json.JSONDecodeError:
            import ast
            return ast.literal_eval(v)
    return list(v or [])


# ---------------------------------------------------------------- jsonl io

def read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
            n += 1
    return n


def load_catalog():
    rows = list(read_jsonl(CATALOG)) if CATALOG.exists() else []
    for r in rows:
        r["stack"] = stack_of(r)
    return rows


def load_row(suite: str, instance_id: str) -> dict:
    for r in read_jsonl(SOURCES / f"{suite}.jsonl"):
        if r["instance_id"] == instance_id:
            return r
    raise KeyError(f"{suite}/{instance_id} not in {SOURCES / (suite + '.jsonl')}")


# ---------------------------------------------------------------- docker

def run(cmd, inp: bytes | None = None, timeout=None):
    p = subprocess.run(cmd, input=inp, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")


def image_exists(image: str) -> bool:
    return run(["docker", "image", "inspect", image])[0] == 0


def ensure_image(image: str, log=print):
    if image_exists(image):
        return
    log(f"pulling {image} ...")
    code, out = run(["docker", "pull", image], timeout=3600)
    if code:
        raise RuntimeError(f"docker pull {image} failed:\n{out[-2000:]}")


def _lf(text: str) -> bytes:
    return text.replace("\r\n", "\n").encode("utf-8")


APPLY = """apply_patch() {
  [ -s "$1" ] || return 0
  git apply --verbose "$1" || git apply --verbose --reject "$1" || patch --batch --fuzz=5 -p1 -i "$1" || {
    echo ">>>>> Patch Apply Failed"; exit 97; }
}
"""


class Container:
    """A throwaway container kept alive with `sleep`, so files can be copied in and out."""

    def __init__(self, image: str):
        self.image = image
        self.name = f"arena-{uuid.uuid4().hex[:10]}"

    def __enter__(self):
        code, out = run(["docker", "run", "-d", "--name", self.name, "--entrypoint", "",
                         self.image, "sleep", "infinity"], timeout=600)
        if code:
            raise RuntimeError(f"cannot start {self.image}: {out[-1500:]}")
        return self

    def __exit__(self, *exc):
        run(["docker", "rm", "-f", self.name], timeout=300)

    def put(self, dest: str, files: dict[str, str | bytes]):
        """Copy {relative path: content} into dest (text is written with LF endings)."""
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for rel, content in files.items():
                data = content if isinstance(content, bytes) else _lf(content)
                info = tarfile.TarInfo(rel)
                info.size, info.mode = len(data), 0o755
                tar.addfile(info, io.BytesIO(data))
        self.sh(f"mkdir -p {dest}")
        code, out = run(["docker", "cp", "-", f"{self.name}:{dest}"], inp=buf.getvalue(), timeout=600)
        if code:
            raise RuntimeError(f"docker cp into {dest} failed: {out}")

    def put_dir(self, src: Path, dest: str):
        self.sh(f"mkdir -p {dest}")
        code, out = run(["docker", "cp", f"{src}{'/.' if src.is_dir() else ''}", f"{self.name}:{dest}"],
                        timeout=600)
        if code:
            raise RuntimeError(f"docker cp {src} failed: {out}")

    def sh(self, script: str, timeout=None, workdir: str | None = None):
        cmd = ["docker", "exec", "-i"] + (["-w", workdir] if workdir else []) + [self.name, "bash", "-s"]
        try:
            return run(cmd, inp=_lf(script), timeout=timeout)
        except subprocess.TimeoutExpired:
            return 124, f">>>>> Tests Timed Out after {timeout}s"

    def read(self, path: str) -> str | None:
        code, out = run(["docker", "exec", self.name, "cat", path])
        return out if code == 0 else None


def run_with_patch(image: str, workdir: str, patch: str, script: str, timeout=1800,
                   files: dict | None = None, setup=None) -> tuple[int, str]:
    """Apply `patch` in workdir of a fresh container, then run `script`. Returns (exit, log)."""
    with Container(image) as c:
        if setup:
            setup(c)
        c.put("/arena", {"patch.diff": patch or "", "run.sh": script, **(files or {})})
        code, log = c.sh(f"set -o pipefail\n{APPLY}cd {workdir}\napply_patch /arena/patch.diff\n"
                         "bash /arena/run.sh 2>&1", timeout=timeout)
        return code, log


def export_repo(image: str, workdir: str, dest: Path):
    """Copy the tracked files at HEAD of the repo inside the image to dest (no git history)."""
    p = subprocess.run(["docker", "run", "--rm", "--entrypoint", "", image, "bash", "-c",
                        f"cd {workdir} && git config --global --add safe.directory '*' && "
                        "git archive --format=tar HEAD"], capture_output=True, timeout=1800)
    if p.returncode:
        raise RuntimeError(f"export of {image}:{workdir} failed: {p.stderr.decode('utf-8', 'replace')[-1500:]}")
    dest.mkdir(parents=True, exist_ok=True)
    skipped = []
    with tarfile.open(fileobj=io.BytesIO(p.stdout)) as tar:
        for m in tar:
            try:  # names Windows cannot hold (aux.c, "a:b", case clashes) are left out
                tar.extract(m, dest, filter="data")
            except (OSError, tarfile.TarError):
                skipped.append(m.name)
    return skipped


def git_init_base(path: Path):
    """Make path a fresh repo whose only commit is the handed-over code."""
    for args in (["init", "-q"], ["config", "core.autocrlf", "false"], ["config", "user.name", "arena"],
                 ["config", "user.email", "arena@local"], ["add", "-A"], ["commit", "-q", "-m", "base"]):
        code, out = run(["git", "-C", str(path), *args])
        if code:
            raise RuntimeError(f"git {' '.join(args)} failed in {path}: {out}")


def git_diff(path: Path) -> str:
    run(["git", "-C", str(path), "add", "-A"])
    p = subprocess.run(["git", "-C", str(path), "diff", "--cached", "--binary", "HEAD"], capture_output=True)
    return p.stdout.decode("utf-8", "replace")


def tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="arena-"))


def rmtree(path: Path):
    shutil.rmtree(path, ignore_errors=True)
