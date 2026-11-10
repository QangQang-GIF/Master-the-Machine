"""Portable, bounded local project setup/runtime for MTM workspaces.

Secrets are assembled in memory, never written to the checkout. Database URLs
are constrained to loopback and database names are namespaced per match/worker.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit


_PLACEHOLDERS = {"", "change_me", "changeme", "your_secret", "replace_me", "<secret>"}
_IDENT = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    cwd: str
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.returncode == 0


class ProjectRuntime:
    def __init__(self, match_root: str | Path, match_id: str, worker_id: str | None = None):
        self.match_root = Path(match_root).resolve()
        if not self.match_root.is_dir():
            raise ValueError("match_root must be an existing directory")
        self.match_id = self._slug(match_id)
        self.worker_id = self._slug(worker_id or "main")
        self.app_root = self._discover_app_root()
        self._raw_env = self._read_env()
        self._secrets: set[str] = set()
        self.env = self._build_env()

    @staticmethod
    def _slug(value: str) -> str:
        value = re.sub(r"[^a-zA-Z0-9]+", "_", str(value)).strip("_").lower()
        if not value:
            raise ValueError("match_id and worker_id must contain letters or digits")
        return value[:24]

    def _discover_app_root(self) -> Path:
        candidates = [self.match_root / "src", self.match_root]
        for path in candidates:
            if (path / "package.json").is_file() or (path / "pyproject.toml").is_file():
                return path
        return self.match_root

    def _read_env(self) -> dict[str, str]:
        values: dict[str, str] = {}
        for path in (self.app_root / ".env.example", self.match_root / ".env.example"):
            if not path.is_file():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.removeprefix("export ").strip()
                    value = value.strip().strip("\"'")
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        values[key] = value
            except OSError:
                continue
        for path in (self.match_root / ".env", self.app_root / ".env"):
            if not path.is_file():
                continue
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    key = key.removeprefix("export ").strip()
                    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                        values[key] = value.strip().strip("\"'")
            except OSError:
                continue
        return values

    def _build_env(self) -> dict[str, str]:
        # Shell/process configuration is the caller's final override over files.
        env = {**self._raw_env, **os.environ}
        # Windows workers hit UnicodeDecodeError in Python checks without UTF-8 mode (K2EMUW3I BUG-09 rework).
        env.setdefault("PYTHONUTF8", "1")
        env.setdefault("PYTHONIOENCODING", "utf-8")
        d_temp = Path("D:/Temp")
        if d_temp.is_dir():
            env["TEMP"] = str(d_temp)
            env["TMP"] = str(d_temp)
            env["TMPDIR"] = str(d_temp)
            env.setdefault("PRISMA_ENGINES_CACHE_DIR", str(d_temp / "prisma"))
            env.setdefault("npm_config_cache", str(d_temp / "npm-cache"))
            env.setdefault("UV_CACHE_DIR", str(d_temp / "uv"))
            env.setdefault("PIP_CACHE_DIR", str(d_temp / "pip"))
        if self._placeholder(env.get("AUTH_SECRET", "")):
            env["AUTH_SECRET"] = secrets.token_urlsafe(48)
        port_hash = hashlib.sha256(f"{self.match_id}:{self.worker_id}".encode()).digest()
        port = 31000 + int.from_bytes(port_hash[:4], "big") % 9000
        env["PORT"] = str(port)
        auth_url = env.get("AUTH_URL")
        if auth_url:
            parsed_auth = urlsplit(auth_url)
            if (parsed_auth.hostname or "").lower() in {"localhost", "127.0.0.1", "::1"}:
                netloc = parsed_auth.netloc.rsplit("@", 1)[-1]
                host = f"[{parsed_auth.hostname}]" if ":" in (parsed_auth.hostname or "") else parsed_auth.hostname
                auth = parsed_auth.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed_auth.netloc else ""
                env["AUTH_URL"] = urlunsplit((parsed_auth.scheme, f"{auth}{host}:{port}", parsed_auth.path, parsed_auth.query, parsed_auth.fragment))
        self._secrets.update(
            value for key, value in env.items()
            if value and len(value) >= 4 and any(
                marker in key.upper() for marker in ("SECRET", "PASSWORD", "TOKEN", "API_KEY", "CREDENTIAL", "PRIVATE_KEY")
            )
        )
        for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
            value = env.get(key)
            if value:
                try:
                    parsed = urlsplit(value)
                    self._secrets.update(x for x in (parsed.password,) if x and len(x) >= 4)
                except ValueError:
                    pass
                env[key] = self._scoped_db_url(key, value)
        return env

    @staticmethod
    def _placeholder(value: str) -> bool:
        return value.strip().lower() in _PLACEHOLDERS or "change_me" in value.lower()

    def _scoped_db_url(self, key: str, value: str) -> str:
        try:
            parts = urlsplit(value)
            if parts.scheme not in {"postgres", "postgresql"}:
                raise ValueError
            host = (parts.hostname or "").lower()
            if host not in {"localhost", "127.0.0.1", "::1"}:
                raise ValueError("database host must be loopback")
            port = parts.port or 5432
            user = parts.username or ""
            password = parts.password
            if password and self._placeholder(password):
                # Omit a sample password only after a successful local trust probe.
                if not self._trust_probe(host, port, user):
                    raise ValueError("placeholder password and loopback trust probe failed")
                password = None
            base = (parts.path or "/postgres").lstrip("/")
            if not _IDENT.fullmatch(base):
                raise ValueError("unsupported PostgreSQL database name")
            suffix = self.match_id if key == "DATABASE_URL" else f"{self.match_id}_{self.worker_id}"
            name = base if base.endswith("_" + suffix) else self._bounded_db_name(base, suffix)
            netloc = ""
            if user:
                netloc = quote(user, safe="")
                if password is not None:
                    netloc += ":" + quote(password, safe="")
                netloc += "@"
            netloc += f"[{host}]" if ":" in host else host
            netloc += f":{port}"
            query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
            return urlunsplit((parts.scheme, netloc, "/" + name, query, ""))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"invalid {key}: {exc}") from exc

    @staticmethod
    def _bounded_db_name(base: str, suffix: str) -> str:
        tail = "_" + suffix
        return base[: max(1, 63 - len(tail))].rstrip("_") + tail

    def _trust_probe(self, host: str, port: int, user: str) -> bool:
        if not user:
            return False
        try:
            psql_cmd = "psql"
            if sys.platform == "win32":
                psql_cmd = shutil.which("psql") or "psql"
            result = subprocess.run(
                [psql_cmd, "-w", "-h", host, "-p", str(port), "-U", user, "-d", "postgres", "-Atqc", "SELECT 1"],
                cwd=self.match_root, env=os.environ.copy(), shell=False, capture_output=True,
                text=True, timeout=3, check=False,
            )
            return result.returncode == 0 and result.stdout.strip() == "1"
        except (OSError, subprocess.TimeoutExpired):
            return False

    def _redact(self, value: str) -> str:
        for secret in sorted(self._secrets, key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
        # Mask URL credentials even if a child process reformats them.
        return re.sub(r"(?i)(postgres(?:ql)?://[^:/@\s]+:)[^@/\s]+(@)", r"\1[REDACTED]\2", value)

    def _resolve_cwd(self, cwd: str | Path | None) -> Path:
        path = Path(cwd) if cwd is not None else self.app_root
        if not path.is_absolute():
            path = self.match_root / path
        path = path.resolve()
        if not path.is_relative_to(self.match_root):
            raise ValueError("command cwd must stay inside match_root")
        return path

    def run(self, argv: Sequence[str], cwd: str | Path | None = None, timeout: float = 300,
            _env_overrides: dict[str, str] | None = None) -> CommandResult:
        if not argv:
            raise ValueError("argv cannot be empty")
        workdir = self._resolve_cwd(cwd)
        args = tuple(map(str, argv))
        try:
            env = self.env.copy()
            if _env_overrides:
                env.update(_env_overrides)
            if Path(args[0]).name.lower() in {"pwsh", "pwsh.exe"}:
                bin_dir = self.app_root / "node_modules" / ".bin"
                path_value = env.get("PATH", "")
                if str(bin_dir) not in path_value.split(os.pathsep):
                    env["PATH"] = str(bin_dir) + (os.pathsep + path_value if path_value else "")
            cmd_args = list(args)
            if sys.platform == "win32" and cmd_args:
                base_name = cmd_args[0]
                resolved = None
                for ext in (".cmd", ".exe", ".bat"):
                    cand = shutil.which(base_name + ext if not base_name.lower().endswith(ext) else base_name)
                    if cand and Path(cand).suffix.lower() in {".cmd", ".exe", ".bat"}:
                        resolved = cand
                        break
                if not resolved:
                    resolved = shutil.which(base_name)
                if resolved:
                    if Path(resolved).suffix.lower() in {".cmd", ".bat"}:
                        cmd_args = ["cmd.exe", "/c", resolved] + cmd_args[1:]
                    elif Path(resolved).suffix.lower() == ".exe":
                        cmd_args[0] = resolved
            result = subprocess.run(cmd_args, cwd=workdir, env=env, shell=False, capture_output=True,
                                    text=True, timeout=max(0.1, timeout), check=False)
            return CommandResult(tuple(self._redact(a) for a in args), str(workdir), result.returncode, self._redact(result.stdout), self._redact(result.stderr))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes): out = out.decode(errors="replace")
            if isinstance(err, bytes): err = err.decode(errors="replace")
            return CommandResult(tuple(self._redact(a) for a in args), str(workdir), -1, self._redact(out), self._redact(err), True)

    def _manifest(self) -> dict:
        path = self.app_root / "package.json"
        if not path.is_file(): return {}
        import json
        try: return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError): return {}

    def doctor_command(self) -> list[str] | None:
        candidates = [self.match_root / "env-require" / "doctor.ps1",
                     self.app_root / "scripts" / "doctor.ps1", self.app_root / "doctor.ps1",
                     self.app_root / "scripts" / "doctor.sh", self.app_root / "doctor.sh"]
        for script in candidates:
            if script.is_file():
                if script.suffix.lower() == ".ps1":
                    command = ["pwsh", "-NoProfile", "-File", str(script)]
                    if script.parent.name == "env-require":
                        command.extend(["-SourceDir", str(self.app_root)])
                    return command
                shell = "/bin/bash" if os.name != "nt" else "bash"
                return [shell, str(script)]
        scripts = self._manifest().get("scripts", {})
        if "doctor" in scripts:
            return self._package_cmd("run", "doctor")
        return None

    def doctor_cwd(self) -> Path:
        """Working directory expected by the discovered doctor script."""
        command = self.doctor_command()
        if command and len(command) >= 4 and Path(command[3]).name.lower() == "doctor.ps1":
            return self.match_root
        return self.app_root

    def _package_manager(self) -> str:
        manifest = self._manifest()
        manager = str(manifest.get("packageManager", ""))
        return manager.split("@", 1)[0] if manager else ("pnpm" if (self.app_root / "pnpm-lock.yaml").exists() else "npm")

    def _package_cmd(self, *args: str) -> list[str]:
        mgr = self._package_manager()
        return [mgr, *args]

    def build_command(self) -> list[str] | None:
        scripts = self._manifest().get("scripts", {})
        if "build" in scripts: return self._package_cmd("run", "build")
        return None

    def _ensure_scoped_databases(self) -> list[CommandResult]:
        results = []
        for key in ("DATABASE_URL", "TEST_DATABASE_URL"):
            raw = self.env.get(key)
            if not raw: continue
            parts = urlsplit(raw)
            name = (parts.path or "").lstrip("/")
            if not _IDENT.fullmatch(name): raise ValueError(f"invalid derived database name for {key}")
            user = parts.username
            if not user: raise ValueError(f"{key} must include a database role")
            host = parts.hostname or "localhost"
            port = str(parts.port or 5432)
            password_env = {"PGPASSWORD": parts.password} if parts.password else None
            check = self.run(["psql", "-w", "-h", host, "-p", port, "-U", user, "-d", "postgres", "-Atqc",
                              f"SELECT 1 FROM pg_database WHERE datname = '{name}'"], timeout=5,
                             _env_overrides=password_env)
            if not check.ok: raise RuntimeError(f"database probe failed for {key}: {check.stderr[:300]}")
            if check.stdout.strip() != "1":
                create = self.run(["createdb", "-w", "-h", host, "-p", port, "-U", user, name], timeout=10,
                                  _env_overrides=password_env)
                if not create.ok: raise RuntimeError(f"could not create scoped database for {key}: {create.stderr[:300]}")
                results.append(create)
        return results

    # ---- Per-worker application database -------------------------------------------------
    # Workers used to share one DATABASE_URL (<base>_<match>): every worker's QA seed, dev server
    # and screenshots read and wrote the same rows, so one item's fixture data polluted another's
    # evidence. A worker now gets its own copy, cloned from a pristine template the root prepares.
    # The shared database remains the fallback whenever a private copy cannot be made.

    def worker_db_isolation_enabled(self) -> bool:
        return (self.worker_id != "main" and bool(self.env.get("DATABASE_URL"))
                and os.environ.get("MTM_WORKER_DB_ISOLATION", "1") != "0")

    def _db_target(self, url: str) -> dict:
        parts = urlsplit(url)
        name = (parts.path or "").lstrip("/")
        if not _IDENT.fullmatch(name):
            raise ValueError("invalid derived database name")
        return {"parts": parts, "name": name, "user": parts.username or "",
                "host": parts.hostname or "localhost", "port": str(parts.port or 5432),
                "pw": {"PGPASSWORD": parts.password} if parts.password else None}

    def _with_db_name(self, url: str, name: str) -> str:
        parts = urlsplit(url)
        return urlunsplit((parts.scheme, parts.netloc, "/" + name, parts.query, parts.fragment))

    def template_db_name(self, shared_url: str | None = None) -> str:
        return self._bounded_db_name(self._db_target(shared_url or self.env["DATABASE_URL"])["name"], "tpl")

    def worker_app_db_name(self, shared_url: str | None = None) -> str:
        return self._bounded_db_name(self._db_target(shared_url or self.env["DATABASE_URL"])["name"], f"{self.worker_id}_app")

    def _pg(self, tool: str, t: dict, *args: str, timeout: float = 15) -> CommandResult:
        return self.run([tool, "-w", "-h", t["host"], "-p", t["port"], "-U", t["user"], *args],
                        timeout=timeout, _env_overrides=t["pw"])

    def _db_exists(self, t: dict, name: str) -> bool:
        res = self._pg("psql", t, "-d", "postgres", "-Atqc", f"SELECT 1 FROM pg_database WHERE datname = '{name}'", timeout=5)
        return res.ok and res.stdout.strip() == "1"

    def refresh_template_database(self) -> CommandResult | None:
        """Root only, right after migrate+seed: snapshot the pristine shared DB as the worker template."""
        if self.worker_id != "main" or not self.env.get("DATABASE_URL") or os.environ.get("MTM_WORKER_DB_ISOLATION", "1") == "0":
            return None
        try:
            t = self._db_target(self.env["DATABASE_URL"])
            if not t["user"]:
                return None
            tpl = self.template_db_name()
            self._pg("dropdb", t, "--if-exists", tpl, timeout=20)
            return self._pg("createdb", t, "-T", t["name"], tpl, timeout=60)
        except Exception as exc:  # never fail preparation because of the optional template
            sys.stderr.write(f"[mtm] worker template database not created: {exc}\n")
            return None

    def ensure_worker_app_database(self) -> str:
        """Point DATABASE_URL at this worker's private copy, creating it on first use.

        Order: existing copy -> clone of the pristine template -> clone of the shared DB -> empty DB
        with migrate + seed. If all of these fail the shared database is kept (previous behaviour),
        so a worker is never left without a database. Returns what happened, for diagnostics.
        """
        if not self.worker_db_isolation_enabled():
            return "disabled"
        shared_url = self.env["DATABASE_URL"]
        try:
            t = self._db_target(shared_url)
            if not t["user"]:
                return "shared:no-role"
            name = self.worker_app_db_name(shared_url)
            worker_url = self._with_db_name(shared_url, name)
            if self._db_exists(t, name):
                self.env["DATABASE_URL"] = worker_url
                return "existing"
            for source in (self.template_db_name(shared_url), t["name"]):
                if not self._db_exists(t, source):
                    continue
                for attempt in range(3):
                    if self._pg("createdb", t, "-T", source, name, timeout=60).ok or self._db_exists(t, name):
                        self.env["DATABASE_URL"] = worker_url
                        return f"cloned:{'template' if source != t['name'] else 'shared'}"
                    time.sleep(0.5 * (attempt + 1))  # template briefly in use by a parallel clone
            created = self._pg("createdb", t, name, timeout=30)
            if not (created.ok or self._db_exists(t, name)):
                return "shared:create-failed"
            self.env["DATABASE_URL"] = worker_url
            scripts = self._manifest().get("scripts", {})
            steps = []
            if "db:deploy" in scripts:
                steps.append(self._package_cmd("run", "db:deploy"))
            elif (self.app_root / "prisma" / "migrations").is_dir():
                steps.append(self._package_cmd("exec", "prisma", "migrate", "deploy"))
            if "db:seed" in scripts:
                steps.append(self._package_cmd("run", "db:seed"))
            for step in steps:
                if not self.run(step, timeout=300).ok:
                    self.env["DATABASE_URL"] = shared_url
                    self._pg("dropdb", t, "--if-exists", name, timeout=20)
                    return "shared:migrate-failed"
            return "created:migrated"
        except Exception as exc:
            self.env["DATABASE_URL"] = shared_url
            return f"shared:error:{type(exc).__name__}"

    # ---- prisma generate in workers ------------------------------------------------------
    # Worker worktrees link src/node_modules to the root checkout, so the generated Prisma client
    # is shared and the root already generated it during preparation. Regenerating from a worker
    # rewrites the shared engine DLL while other workers' servers hold it open (EPERM on Windows)
    # and costs time. Workers therefore skip it unless they changed the Prisma schema.

    @staticmethod
    def is_prisma_generate(argv: Sequence[str]) -> bool:
        words = [str(a).lower() for a in argv]
        return "prisma" in words and words.index("prisma") + 1 < len(words) and words[words.index("prisma") + 1] == "generate"

    def worker_schema_changed(self) -> bool:
        try:
            res = subprocess.run(["git", "-C", str(self.app_root), "status", "--porcelain", "--", "prisma"],
                                 capture_output=True, text=True, timeout=10, check=False)
            if res.returncode != 0:
                return True  # cannot tell: be safe and generate
            if res.stdout.strip():
                return True
            # Committed schema changes: compare with the commit this worktree started from
            # (the oldest entry of the worktree's HEAD reflog).
            log = subprocess.run(["git", "-C", str(self.app_root), "reflog", "--format=%H", "HEAD"],
                                 capture_output=True, text=True, timeout=10, check=False)
            entries = log.stdout.split() if log.returncode == 0 else []
            if not entries:
                return False
            diff = subprocess.run(["git", "-C", str(self.app_root), "diff", "--name-only", entries[-1], "HEAD", "--", "prisma"],
                                  capture_output=True, text=True, timeout=10, check=False)
            return diff.returncode != 0 or bool(diff.stdout.strip())
        except (OSError, subprocess.TimeoutExpired):
            return True

    def skip_worker_prisma_generate(self) -> bool:
        return (self.worker_id != "main" and os.environ.get("MTM_WORKER_PRISMA_GENERATE", "0") != "1"
                and not self.worker_schema_changed())

    def prepare(self) -> list[CommandResult]:
        """Install dependencies; only the root worker provisions/migrates/seeds and builds."""
        results: list[CommandResult] = []
        manifest = self._manifest()
        if manifest:
            pm = self._package_manager()
            if pm == "pnpm": install = ["pnpm", "install", "--frozen-lockfile"]
            elif pm == "npm": install = ["npm", "ci"] if (self.app_root / "package-lock.json").exists() else ["npm", "install"]
            elif pm == "yarn": install = ["yarn", "install", "--immutable"]
            else: install = [pm, "install"]
            results.append(self.run(install, timeout=600))
            if not results[-1].ok: return results
            if any((self.app_root / p).exists() for p in ("prisma/schema.prisma", "prisma/schema")):
                if self.worker_id != "main":
                    if self.skip_worker_prisma_generate():
                        print("[mtm] prisma generate skipped: the root already generated the shared client and the schema is unchanged", file=sys.stderr)
                    else:
                        results.append(self.run(self._package_cmd("exec", "prisma", "generate"), timeout=180))
                    return results
                results.extend(self._ensure_scoped_databases())
                for script in ("db:generate",):
                    if script in manifest.get("scripts", {}):
                        results.append(self.run(self._package_cmd("run", script), timeout=180))
                        if not results[-1].ok: return results
                results.append(self.run(self._package_cmd("exec", "prisma", "generate"), timeout=180))
                if not results[-1].ok: return results
                if "db:deploy" in manifest.get("scripts", {}):
                    results.append(self.run(self._package_cmd("run", "db:deploy"), timeout=300))
                elif (self.app_root / "prisma" / "migrations").is_dir():
                    results.append(self.run(self._package_cmd("exec", "prisma", "migrate", "deploy"), timeout=300))
                if results and not results[-1].ok: return results
                if "db:seed" in manifest.get("scripts", {}):
                    results.append(self.run(self._package_cmd("run", "db:seed"), timeout=300))
                    if not results[-1].ok: return results
                tpl = self.refresh_template_database()
                if tpl is not None and not tpl.ok:
                    sys.stderr.write(f"[mtm] worker template database not created: {tpl.stderr[:200]}\n")
        if self.worker_id != "main":
            return results
        doctor = self.doctor_command()
        if doctor:
            results.append(self.run(doctor, cwd=self.doctor_cwd(), timeout=120))
            if not results[-1].ok: return results
        build = self.build_command()
        if build:
            results.append(self.run(build, timeout=600))
        return results


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--match-root", required=True)
    parser.add_argument("--match-id", required=True)
    parser.add_argument("--worker-id")
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("prepare")
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--cwd")
    run_parser.add_argument("--timeout", type=float, default=300)
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    session_parser = sub.add_parser("session", help="Run one local server and evidence/check command with shared ephemeral config")
    session_parser.add_argument("--server-script", default="dev")
    session_parser.add_argument("--url")
    session_parser.add_argument("--timeout", type=float, default=300)
    session_parser.add_argument("command", nargs=argparse.REMAINDER)
    serve_parser = sub.add_parser("serve-start", help="Start (or reuse) one local server for this worker and leave it running")
    serve_parser.add_argument("--server-script", default="dev")
    serve_parser.add_argument("--url")
    serve_parser.add_argument("--timeout", type=float, default=90)
    sub.add_parser("serve-stop", help="Stop this worker's persistent server")
    args = parser.parse_args(argv)
    runtime = ProjectRuntime(args.match_root, args.match_id, args.worker_id)
    if runtime.worker_db_isolation_enabled():
        outcome = runtime.ensure_worker_app_database()
        if outcome != "existing":
            print(f"[mtm] worker app database: {outcome}", file=sys.stderr)
    if args.action == "prepare":
        results = runtime.prepare()
        for result in results:
            print(f"$ {shlex.join(result.argv)} [{result.returncode}{', timeout' if result.timed_out else ''}]")
            if result.stdout: print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
            if result.stderr: print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
            if not result.ok: return result.returncode or 1
        return 0
    def server_command(script: str) -> list[str]:
        scripts = runtime._manifest().get("scripts", {})
        if script not in scripts:
            raise ValueError("Server script must be declared by the current project")
        server = runtime._package_cmd("run", script)
        manifest = runtime._manifest()
        if "next" in {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})}:
            if runtime._package_manager() == "npm":
                server.append("--")
            server.extend(["--hostname", "127.0.0.1", "--port", runtime.env["PORT"]])
        return server

    if args.action in ("serve-start", "serve-stop"):
        import json as _json
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from runner.web_session import start_background_server, stop_background_server
        if args.action == "serve-stop":
            print(_json.dumps(stop_background_server(runtime)))
            return 0
        res = start_background_server(runtime, server_command(args.server_script), readiness_url=args.url,
                                      readiness_timeout=args.timeout)
        print(_json.dumps(res))
        return 0 if res.get("status") == "READY" else 1
    command = list(args.command)
    if command[:1] == ["--"]: command = command[1:]
    if args.action == "session":
        server = server_command(args.server_script)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from runner.web_session import run_web_session
        result = run_web_session(runtime, server, command, readiness_url=args.url, timeout=args.timeout)
    elif runtime.is_prisma_generate(command) and runtime.skip_worker_prisma_generate():
        print("[mtm] prisma generate skipped: the root already generated the shared client and the schema is unchanged; "
              "run it only after changing prisma/ (set MTM_WORKER_PRISMA_GENERATE=1 to force)")
        return 0
    else:
        result = runtime.run(command, args.cwd, args.timeout)
    print(result.stdout, end="")
    print(result.stderr, file=sys.stderr, end="")
    return result.returncode or (124 if result.timed_out else 0)


if __name__ == "__main__":
    raise SystemExit(main())
