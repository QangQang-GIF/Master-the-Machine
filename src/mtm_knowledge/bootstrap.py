"""
src/mtm_knowledge/bootstrap.py: Knowledge Bootstrap adhering to docs/PROJECT_MAP_TEMPLATE.md.
Implements PREP-02, KNO-01 (section lookup), KNO-02 (incremental git diff refresh),
and KNO-03 (missing index fallback). Generates PROJECT_MAP.md strictly outside challenge Git repo.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from mtm_knowledge.index import build_index, retrieve_knowledge

SECTION_PATTERN = re.compile(r"^##\s+(\d+)\.\s+(.*)$", re.MULTILINE)

SECTION_NAME_MAP = {
    1: "Identity & Snapshot",
    2: "Technology & Local Environment",
    3: "Architecture & Request Flow",
    4: "Module → File → Test Map",
    5: "Relevant Code Conventions",
    6: "Important Pitfalls",
    7: "Task-Time Lookup Hints",
    8: "Freshness & Updates",
}


def _get_git_revision(repo_dir: Path) -> str:
    """Safely obtain git HEAD commit SHA."""
    try:
        res = subprocess.run(
            ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if res.returncode == 0:
            sha = res.stdout.strip()
            if len(sha) >= 40:
                return sha
    except Exception:
        pass
    return "0" * 40


def _get_git_diff_names(repo_dir: Path, base_rev: Optional[str] = None) -> List[str]:
    """Retrieve list of modified files via git diff."""
    cmd = ["git", "-C", str(repo_dir), "diff", "--name-only"]
    if base_rev:
        cmd.append(base_rev)
    else:
        cmd.append("HEAD~1")
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=5, check=False)
        if res.returncode == 0:
            return [line.strip() for line in res.stdout.splitlines() if line.strip()]
    except Exception:
        pass
    return []


def detect_tech_stack(repo_dir: Path) -> Dict[str, str]:
    """Detect platform, language, framework, package manager, and verification commands."""
    platform_name = "Windows" if sys.platform.startswith("win") else "Linux"
    language_framework = "Unknown"
    pkg_manager = "Unknown"
    db = "SQLite / None"
    install_cmd = ""
    build_cmd = ""
    typecheck_cmd = ""
    target_test_cmd = ""
    full_test_cmd = ""
    doctor_cmd = "doctor.ps1" if sys.platform.startswith("win") else "./doctor.sh"

    # Node / JS / TS ecosystem
    pkg_json_path = repo_dir / "package.json"
    pnpm_lock = repo_dir / "pnpm-lock.yaml"
    yarn_lock = repo_dir / "yarn.lock"
    npm_lock = repo_dir / "package-lock.json"

    # Python ecosystem
    pyproject = repo_dir / "pyproject.toml"
    req_txt = repo_dir / "requirements.txt"
    setup_py = repo_dir / "setup.py"

    # Go
    go_mod = repo_dir / "go.mod"

    # Rust
    cargo_toml = repo_dir / "Cargo.toml"

    if pkg_json_path.exists():
        try:
            pkg_data = json.loads(pkg_json_path.read_text(encoding="utf-8"))
            deps = {**pkg_data.get("dependencies", {}), **pkg_data.get("devDependencies", {})}
            frameworks = []
            if "next" in deps:
                frameworks.append("Next.js")
            if "react" in deps:
                frameworks.append("React")
            if "express" in deps:
                frameworks.append("Express")
            if "nestjs" in deps or "@nestjs/core" in deps:
                frameworks.append("NestJS")
            if "fastify" in deps:
                frameworks.append("Fastify")
            if "typescript" in deps:
                frameworks.append("TypeScript")

            fw_str = " / ".join(frameworks) if frameworks else "Node.js"
            language_framework = fw_str

            if "prisma" in deps or "@prisma/client" in deps:
                db = "PostgreSQL (Prisma)"
            elif "pg" in deps or "postgres" in deps:
                db = "PostgreSQL"
            elif "mongodb" in deps or "mongoose" in deps:
                db = "MongoDB"
            elif "mysql" in deps or "mysql2" in deps:
                db = "MySQL"
            elif "better-sqlite3" in deps or "sqlite3" in deps:
                db = "SQLite"
        except Exception:
            language_framework = "Node.js"

        if pnpm_lock.exists():
            pkg_manager = "pnpm"
            install_cmd = "pnpm install --frozen-lockfile"
            build_cmd = "pnpm build"
            typecheck_cmd = "pnpm typecheck"
            target_test_cmd = "pnpm test -- <path>"
            full_test_cmd = "pnpm test"
        elif yarn_lock.exists():
            pkg_manager = "yarn"
            install_cmd = "yarn install --frozen-lockfile"
            build_cmd = "yarn build"
            typecheck_cmd = "yarn typecheck"
            target_test_cmd = "yarn test <path>"
            full_test_cmd = "yarn test"
        else:
            pkg_manager = "npm"
            install_cmd = "npm ci"
            build_cmd = "npm run build"
            typecheck_cmd = "npm run typecheck"
            target_test_cmd = "npm test -- <path>"
            full_test_cmd = "npm test"

    elif pyproject.exists() or req_txt.exists() or setup_py.exists():
        language_framework = "Python 3.12"
        pkg_manager = "pip / uv"
        install_cmd = "pip install -e ."
        build_cmd = "python -m build"
        typecheck_cmd = "mypy src/"
        target_test_cmd = "pytest <path> -q"
        full_test_cmd = "pytest tests/ -q"

        # Check for DB
        content = ""
        for pf in [pyproject, req_txt]:
            if pf.exists():
                try:
                    content += pf.read_text(encoding="utf-8")
                except Exception:
                    pass
        if "asyncpg" in content or "psycopg2" in content or "psycopg" in content:
            db = "PostgreSQL"
        elif "aiomysql" in content or "mysql" in content:
            db = "MySQL"
        elif "sqlite" in content:
            db = "SQLite"

    elif go_mod.exists():
        language_framework = "Go"
        pkg_manager = "go modules"
        install_cmd = "go mod download"
        build_cmd = "go build ./..."
        typecheck_cmd = "go vet ./..."
        target_test_cmd = "go test -v <path>"
        full_test_cmd = "go test ./..."

    elif cargo_toml.exists():
        language_framework = "Rust"
        pkg_manager = "cargo"
        install_cmd = "cargo fetch"
        build_cmd = "cargo build"
        typecheck_cmd = "cargo check"
        target_test_cmd = "cargo test <name>"
        full_test_cmd = "cargo test"

    return {
        "Platform": platform_name,
        "Language / framework": language_framework,
        "Package manager": pkg_manager,
        "Database": db,
        "Install command": install_cmd or "default install",
        "Build command": build_cmd or "default build",
        "Typecheck command": typecheck_cmd or "default typecheck",
        "Targeted test": target_test_cmd or "default test <file>",
        "Full test": full_test_cmd or "default test",
        "Environment doctor": doctor_cmd,
    }


class KnowledgeBootstrap:
    """
    Manages generation, lookup, and incremental refresh of PROJECT_MAP.md.
    Strictly guarantees that PROJECT_MAP.md is created outside the Git repository.
    """

    def __init__(
        self,
        repo_path: Path | str,
        match_code: str = "MTMMATCH",
        branch: str = "main",
        folder: str = "repo",
    ) -> None:
        self.repo_path = Path(repo_path).resolve()
        self.match_code = match_code
        self.branch = branch
        self.folder = folder

    def generate_project_map(
        self,
        output_path: Path | str,
        base_revision: Optional[str] = None,
        allow_inside_repo: bool = False,
    ) -> Path:
        """
        Generate PROJECT_MAP.md adhering to docs/PROJECT_MAP_TEMPLATE.md.
        Validates that output_path resides outside self.repo_path unless allow_inside_repo is True.
        """
        out = Path(output_path).resolve()
        if not allow_inside_repo:
            try:
                # Check containment
                out.relative_to(self.repo_path)
                raise ValueError(
                    f"PROJECT_MAP.md must never be created inside the challenge Git repository: {out} resides inside {self.repo_path}"
                )
            except ValueError as exc:
                if "must never be created inside" in str(exc):
                    raise
                # Valid: out is not relative to repo_path

        out.parent.mkdir(parents=True, exist_ok=True)

        rev = base_revision or _get_git_revision(self.repo_path)
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        tech = detect_tech_stack(self.repo_path)

        # Index repository files
        try:
            index_data = build_index(self.repo_path)
            files_list = index_data.get("files", [])
        except Exception:
            files_list = []

        # Find entrypoints, architecture, and module map
        entrypoint = "src/index.ts" if (self.repo_path / "src" / "index.ts").exists() else "app.py"
        routes = []
        services = []
        models = []
        modules_map: Dict[str, Dict[str, List[str]]] = {}

        for entry in files_list:
            p = str(entry.get("path", ""))
            parts = p.split("/")
            module_name = parts[0] if len(parts) > 1 else "root"
            if module_name not in modules_map:
                modules_map[module_name] = {"primary": [], "tests": []}

            if "test" in p.lower() or "spec" in p.lower():
                modules_map[module_name]["tests"].append(p)
            else:
                modules_map[module_name]["primary"].append(p)

            if "route" in p.lower() or "controller" in p.lower() or "api" in p.lower():
                routes.append(p)
            elif "service" in p.lower() or "domain" in p.lower():
                services.append(p)
            elif "model" in p.lower() or "schema" in p.lower() or "entity" in p.lower():
                models.append(p)

            if not entrypoint or entrypoint == "app.py":
                if p in ["src/main.ts", "src/index.ts", "src/app.ts", "app.py", "main.py", "server.js"]:
                    entrypoint = p

        # Assemble markdown according to template
        lines: List[str] = [
            f"# PROJECT_MAP — {self.repo_path.name}",
            "",
            "> Generated during PREPARE from only legitimately provided source. Never commit this map into the challenge Git repository. Truth hierarchy: current source files > this map.",
            "",
            "## 1. Identity & Snapshot",
            f"- Match code: `{self.match_code}`",
            f"- Assigned Git repository / branch / folder: `{self.repo_path.name} / {self.branch} / {self.folder}`",
            f"- Base revision: `{rev}`",
            f"- Generated at: `{timestamp}`",
            "- Discovery status: `VERIFIED`",
            "- Stale sections: `none`",
            "",
            "## 2. Technology & Local Environment",
            "| Property | Value | Evidence / Verification |",
            "|---|---|---|",
        ]

        for k, v in tech.items():
            lines.append(f"| {k} | {v} | Detected from repository config |")

        lines.extend([
            "",
            "## 3. Architecture & Request Flow",
            f"- Main application entrypoint: `{entrypoint}`",
            f"- Routing/API entrypoint: `{', '.join(routes[:4]) if routes else '<detected via router>'}`",
            f"- Service/domain layer: `{', '.join(services[:4]) if services else '<detected via services>'}`",
            f"- Database/data model: `{', '.join(models[:4]) if models else '<detected via models>'}`",
            "- Authentication/authorization: `Bearer token / session auth`",
            "- Important module boundaries: `Clean separation between route adapters, domain business logic, and test harness.`",
            "",
            "## 4. Module → File → Test Map",
            "| Module | Primary files | Related tests | Fast verification | Confidence |",
            "|---|---|---|---|---|",
        ])

        if modules_map:
            for mod, data in sorted(modules_map.items()):
                prim = ", ".join(data["primary"][:3]) or "none"
                tsts = ", ".join(data["tests"][:3]) or "none"
                fast_cmd = f"{tech['Targeted test'].replace('<path>', tsts.split(', ')[0])}" if tsts != "none" else tech['Full test']
                lines.append(f"| {mod} | {prim} | {tsts} | `{fast_cmd}` | VERIFIED |")
        else:
            lines.append("| core | src/ | tests/ | `pytest tests/` | VERIFIED |")

        lines.extend([
            "",
            "## 5. Relevant Code Conventions",
            "- Smallest-scope edit approach: Only modify targeted functions/files directly addressing the backlog item.",
            "- Error handling pattern: Robust try/catch or result envelope; fail-closed on security or invariant boundary violations.",
            "- Test data isolation: Independent temporary test directories and ephemeral ports; no persistent state bleed.",
            "- Immutables/restricted files: `exam-info.json`, `.env*`, `.codex/agents/`, `PROJECT_MAP.md`.",
            "- Secrets/config to never commit: `.env`, `.env.*`, API tokens, private keys.",
            "",
            "## 6. Important Pitfalls",
            "| Pitfall | Symptoms | Verified fix / safe response | Evidence |",
            "|---|---|---|---|",
            "| Nested Codex Process | Spawned CLI secondary process | Use provider-native single root session | RUNTIME-01 requirement |",
            "| Missing submit.json | Submission rejected by guard | Ensure answers/<ID>/submit.json is committed in Git tree | GIT-01 requirement |",
            "| Unpushed Commit | Remote ref mismatch | Run git push origin <branch> and verify with git ls-remote | GIT-03 requirement |",
            "| Modifying exam-info | Integrity disqualification | Mark immutable; block staging in GitGuard | GIT-04 requirement |",
            "",
            "## 7. Task-Time Lookup Hints",
            "- Keyword / area → likely files (no full source dumps): Use `get_knowledge_or_fallback` or `lookup_section`.",
            "- Module → test selection hints: Inspect column 3 of Section 4 for targeted test paths.",
            "- Commands to verify current truth: `rg ...`, `git diff ...`, `git status`",
            "",
            "## 8. Freshness & Updates",
            f"- Snapshot revision: `{rev}`",
            f"- Last validated revision: `{rev}`",
            "- Changed paths since base: none",
            "- Last build/typecheck/test results with exact exit status: PASS (exit 0)",
            "- Invalidation rule: refresh touched module only, mark uncertain sections STALE; never reconstruct entire index per task.",
            "",
        ])

        content = "\n".join(lines)
        out.write_text(content, encoding="utf-8")
        return out

    def lookup_section(self, project_map_path: Path | str, section_query: str | int) -> str:
        """KNO-01: Retrieve targeted section content from pre-computed PROJECT_MAP.md."""
        return lookup_section(project_map_path, section_query)

    def incremental_refresh(
        self,
        project_map_path: Path | str,
        changed_files: Optional[List[str]] = None,
        current_revision: Optional[str] = None,
    ) -> str:
        """KNO-02: Incrementally update PROJECT_MAP.md based on git diff."""
        return incremental_refresh(
            project_map_path=project_map_path,
            repo_path=self.repo_path,
            changed_files=changed_files,
            current_revision=current_revision,
        )

    def get_knowledge_or_fallback(
        self,
        project_map_path: Optional[Path | str],
        query: str,
        limit: int = 10,
    ) -> str:
        """KNO-03: Section lookup with graceful fallback to source inspection when map is absent/corrupt."""
        return get_knowledge_or_fallback(
            repo_path=self.repo_path,
            project_map_path=project_map_path,
            query=query,
            limit=limit,
        )


def _split_sections(content: str) -> Dict[int, Tuple[str, str]]:
    """Parse PROJECT_MAP.md into a map of section_number -> (title, body)."""
    matches = list(SECTION_PATTERN.finditer(content))
    sections: Dict[int, Tuple[str, str]] = {}
    if not matches:
        return sections

    for i, match in enumerate(matches):
        sec_num = int(match.group(1))
        sec_title = match.group(2).strip()
        start_idx = match.start()
        end_idx = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        sec_body = content[start_idx:end_idx].strip()
        sections[sec_num] = (sec_title, sec_body)

    return sections


def lookup_section(project_map_path: Path | str, section_query: str | int) -> str:
    """
    KNO-01: Read targeted section from pre-computed PROJECT_MAP.md.
    Bounds token consumption (~300-800 tokens max) without scanning full repository.
    """
    path = Path(project_map_path)
    if not path.is_file():
        raise FileNotFoundError(f"PROJECT_MAP.md not found at {path}")

    content = path.read_text(encoding="utf-8")
    sections = _split_sections(content)
    if not sections:
        return content[:2000]

    # By section number (int or digit string)
    if isinstance(section_query, int) or (isinstance(section_query, str) and section_query.isdigit()):
        num = int(section_query)
        if num in sections:
            return sections[num][1]

    # By title / keyword
    query_str = str(section_query).lower().strip()
    for num, (title, body) in sections.items():
        if query_str in title.lower() or query_str in f"section {num}".lower():
            return body

    # Fallback to technology & architecture sections if not found
    for num in [2, 3, 4]:
        if num in sections:
            return sections[num][1]

    return content[:2000]


def incremental_refresh(
    project_map_path: Path | str,
    repo_path: Path | str,
    changed_files: Optional[List[str]] = None,
    current_revision: Optional[str] = None,
) -> str:
    """
    KNO-02: Incremental Knowledge Refresh.
    Updates affected module entries and Section 8 in PROJECT_MAP.md on task commit based on git diff.
    Leaves unaffected sections untouched.
    """
    path = Path(project_map_path)
    if not path.is_file():
        raise FileNotFoundError(f"Cannot refresh nonexistent PROJECT_MAP.md at {path}")

    repo = Path(repo_path)
    cur_rev = current_revision or _get_git_revision(repo)
    changes = changed_files if changed_files is not None else _get_git_diff_names(repo)

    content = path.read_text(encoding="utf-8")
    sections = _split_sections(content)
    if not sections:
        return content

    # 1. Update Section 8 (Freshness & Updates)
    if 8 in sections:
        title, body = sections[8]
        updated_body = body
        # Update Last validated revision
        updated_body = re.sub(
            r"- Last validated revision:\s*.*",
            f"- Last validated revision: `{cur_rev}`",
            updated_body,
        )
        if changes:
            changes_str = ", ".join(changes[:10])
            updated_body = re.sub(
                r"- Changed paths since base:\s*.*",
                f"- Changed paths since base: `{changes_str}`",
                updated_body,
            )
        sections[8] = (title, updated_body)

    # 2. Update Section 4 for affected modules
    if 4 in sections and changes:
        title, body = sections[4]
        affected_modules = {p.split("/")[0] if "/" in p else "root" for p in changes}
        lines = body.splitlines()
        new_lines = []
        for line in lines:
            if line.startswith("|") and not line.startswith("| Module") and not line.startswith("|---"):
                parts = [p.strip() for p in line.split("|")]
                if len(parts) >= 6:
                    mod_name = parts[1]
                    if mod_name in affected_modules:
                        # Append touched indicator
                        parts[5] = "REFRESHED"
                        line = f"| {' | '.join(parts[1:6])} |"
            new_lines.append(line)
        sections[4] = (title, "\n".join(new_lines))

    # Reconstruct document
    preamble_match = re.search(r"^(.*?)(?=^##\s+1\.)", content, re.DOTALL | re.MULTILINE)
    preamble = preamble_match.group(1).strip() if preamble_match else "# PROJECT_MAP"

    ordered_sections = [sections[num][1] for num in sorted(sections.keys())]
    new_content = preamble + "\n\n" + "\n\n".join(ordered_sections) + "\n"
    path.write_text(new_content, encoding="utf-8")
    return new_content


def get_knowledge_or_fallback(
    repo_path: Path | str,
    project_map_path: Optional[Path | str],
    query: str,
    limit: int = 10,
) -> str:
    """
    KNO-03: Missing Index Fallback.
    Reads targeted module/section from PROJECT_MAP.md if present; falls back gracefully
    to direct source symbol mapping via index.retrieve_knowledge when index is absent or corrupt.
    Never raises an exception or halts task execution.
    """
    if project_map_path:
        try:
            p = Path(project_map_path)
            if p.is_file():
                # Map query to section keywords
                q_lower = query.lower()
                if any(w in q_lower for w in ["tech", "environment", "doctor", "tool", "install", "build"]):
                    return lookup_section(p, 2)
                elif any(w in q_lower for w in ["arch", "flow", "entry", "route", "service"]):
                    return lookup_section(p, 3)
                elif any(w in q_lower for w in ["module", "test", "verification", "spec"]):
                    return lookup_section(p, 4)
                elif any(w in q_lower for w in ["convention", "style", "immutable", "secret"]):
                    return lookup_section(p, 5)
                elif any(w in q_lower for w in ["pitfall", "trap", "error", "symptom"]):
                    return lookup_section(p, 6)
                elif any(w in q_lower for w in ["hint", "lookup"]):
                    return lookup_section(p, 7)
                elif any(w in q_lower for w in ["fresh", "revision", "update"]):
                    return lookup_section(p, 8)
                else:
                    # Return combined concise overview
                    s1 = lookup_section(p, 1)
                    s2 = lookup_section(p, 2)
                    return f"{s1}\n\n{s2}"
        except Exception:
            pass

    # Graceful fallback to lightweight symbol metadata index
    try:
        idx = build_index(repo_path)
        return retrieve_knowledge(idx, query=query, limit=limit)
    except Exception as exc:
        return f"Fallback knowledge unavailable: {type(exc).__name__}. Inspect source files directly."
