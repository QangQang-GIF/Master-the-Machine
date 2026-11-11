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


def _run_watched(cmd_args: list, workdir: Path, env: dict, timeout: float, idle: float):
    """Run a command and stop its whole process tree when it exceeds `timeout` or prints nothing for `idle`
    seconds. A hung test or server wait otherwise holds a worker for the full timeout (300 s by default)."""
    import threading
    kwargs = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if sys.platform == "win32" else {"start_new_session": True}
    proc = subprocess.Popen(cmd_args, cwd=workdir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding="utf-8", errors="replace", **kwargs)
    chunks = {"out": [], "err": []}
    last = [time.monotonic()]

    def pump(stream, name):
        for line in iter(stream.readline, ""):
            chunks[name].append(line)
            last[0] = time.monotonic()
        stream.close()

    readers = [threading.Thread(target=pump, args=(proc.stdout, "out"), daemon=True),
               threading.Thread(target=pump, args=(proc.stderr, "err"), daemon=True)]
    for r in readers:
        r.start()
    started, why = time.monotonic(), None
    while proc.poll() is None:
        now = time.monotonic()
        if now - started > timeout:
            why = f"timed out after {timeout:.0f}s"
        elif now - last[0] > idle:
            why = f"no output for {idle:.0f}s (looks hung)"
        if why:
            try:
                sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
                from mtm_core.runtime.process import terminate_process_tree
                terminate_process_tree(proc)
            except Exception:
                proc.kill()
            break
        time.sleep(0.2)
    try:
        proc.wait(timeout=10)
    except Exception:
        pass
    for r in readers:
        r.join(timeout=5)
    code = proc.returncode if proc.returncode is not None else -1
    return (124 if why else code), "".join(chunks["out"]), "".join(chunks["err"]), why


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
        # Windows workers hit UnicodeDecodeError in Python checks without UTF-8 mode.
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

    def run(self, argv: Sequence[str], cwd: str | Path | None = None, timeout: float = 180,
            _env_overrides: dict[str, str] | None = None, _setup: bool = False) -> CommandResult:
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
            cmd_args = self.resolve_command(args)
            # Enabled by the installer (MTM_RUN_IDLE_TIMEOUT=90 in the workspace shell environment).
            idle = float(os.environ.get("MTM_RUN_IDLE_TIMEOUT", "0") or 0)
            if idle > 0 and _setup:
                # Preparation (install/build) may be silent for minutes (dependency fetch) and run past 10 min:
                # only a generous hang guard, no worker caps.
                idle = max(idle, float(os.environ.get("MTM_SETUP_IDLE_TIMEOUT", "600")))
            elif idle > 0:
                idle = min(max(idle, 1.0), float(os.environ.get("MTM_RUN_IDLE_MAX", "180")))
                timeout = min(float(timeout), float(os.environ.get("MTM_RUN_TIMEOUT_MAX", "600")))
                code, out, err, why = _run_watched(cmd_args, workdir, env, max(0.1, timeout), idle)
                if why:
                    err = (err + ("\n" if err and not err.endswith("\n") else "")
                           + f"[mtm] stopped: {why}. Re-run with --timeout/--idle-timeout if this command is known to be slow and silent.\n")
                return CommandResult(tuple(self._redact(a) for a in args), str(workdir), code, self._redact(out),
                                     self._redact(err), bool(why))
            result = subprocess.run(cmd_args, cwd=workdir, env=env, shell=False, capture_output=True,
                                    text=True, timeout=max(0.1, timeout), check=False)
            return CommandResult(tuple(self._redact(a) for a in args), str(workdir), result.returncode, self._redact(result.stdout), self._redact(result.stderr))
        except (FileNotFoundError, PermissionError) as exc:
            # A missing program is a command result (exit 127), never a crash of the caller.
            return CommandResult(tuple(self._redact(a) for a in args), str(workdir), 127, "",
                                 self._redact(f"[mtm] cannot start {args[0]!r}: {exc}. Is it installed / on PATH?\n"))
        except subprocess.TimeoutExpired as exc:
            out = exc.stdout or ""
            err = exc.stderr or ""
            if isinstance(out, bytes): out = out.decode(errors="replace")
            if isinstance(err, bytes): err = err.decode(errors="replace")
            return CommandResult(tuple(self._redact(a) for a in args), str(workdir), -1, self._redact(out), self._redact(err), True)

    def resolve_command(self, argv: Sequence[str]) -> list[str]:
        """One resolver for run, session servers and prepare (39PSWT5C/RQ3I4OHX: `session` built `yarn run ...`
        itself, bypassing the bundled-Yarn fix in run(), and CR-07 failed with WinError 2).

        - yarn without a global yarn: the project's bundled release via node (.yarnrc.yml yarnPath or
          .yarn/releases/yarn-*.cjs); otherwise corepack.
        - pnpm/npm/npx/yarn missing on PATH: corepack (pnpm/yarn) when available.
        - a tool from the project's node_modules/.bin (vite, next, tsx, vitest, ...) that is not on PATH.
        - Windows: .cmd/.bat go through cmd.exe /c, .exe by full path (CreateProcess finds neither by bare name).
        """
        cmd = [str(a) for a in argv]
        if not cmd:
            return cmd
        name = Path(cmd[0]).name.lower()
        stem = name[:-4] if name.endswith((".cmd", ".exe", ".bat")) else name
        node = shutil.which("node") or "node"
        if stem == "yarn" and not shutil.which("yarn"):
            bundled = self._bundled_yarn()
            if bundled:
                return [node, str(bundled), *cmd[1:]]
        if stem in ("yarn", "pnpm") and not shutil.which(stem) and shutil.which("corepack"):
            cmd = ["corepack", stem, *cmd[1:]]
        elif not shutil.which(cmd[0]) and not Path(cmd[0]).is_absolute() and "/" not in cmd[0] and "\\" not in cmd[0]:
            for base in (self.app_root, self.match_root, self.match_root / "src"):
                for ext in ((".cmd", ".exe", "") if sys.platform == "win32" else ("",)):
                    local = base / "node_modules" / ".bin" / (stem + ext)
                    if local.is_file():
                        cmd = [str(local), *cmd[1:]]
                        break
                else:
                    continue
                break
        if sys.platform == "win32":
            base_name = cmd[0]
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
                    cmd = ["cmd.exe", "/c", resolved] + cmd[1:]
                elif Path(resolved).suffix.lower() == ".exe":
                    cmd[0] = resolved
        return cmd

    def server_command(self, script: str | None = None, command_line: str | None = None) -> tuple[list[str], str]:
        """The server argv for `session`, plus a note. --server-cmd wins; else the named script, else the first of
        dev/start/serve/preview that package.json declares. Never raises for a missing script: the error names the
        scripts that exist so the worker can choose."""
        if command_line:
            # Windows paths keep their backslashes (posix=False), then the surrounding quotes are dropped.
            parts = shlex.split(command_line, posix=(sys.platform != "win32"))
            return ([t[1:-1] if len(t) >= 2 and t[0] == t[-1] and t[0] in "\"'" else t for t in parts], "")
        scripts = self._manifest().get("scripts", {}) or {}
        chosen = script if script in scripts else next((s for s in ("dev", "start", "serve", "preview") if s in scripts), None)
        if chosen is None:
            raise ValueError(f"No server script {script!r} in {self.app_root / 'package.json'}; scripts: "
                             f"{sorted(scripts)[:30]}. Pass the serve command from env-require/README.md with "
                             f"--server-cmd \"<command>\" (and --server-env KEY=VALUE for its data folders).")
        note = "" if chosen == script else f"[mtm] server script {script!r} not declared; using {chosen!r}.\n"
        server = self._package_cmd("run", chosen)
        manifest = self._manifest()
        body = str(scripts.get(chosen, ""))
        if "next" in {**manifest.get("dependencies", {}), **manifest.get("devDependencies", {})} and "next" in body:
            if self._package_manager() == "npm":
                server.append("--")
            server.extend(["--hostname", "127.0.0.1", "--port", self.env["PORT"]])
        return server, note

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

    def _bundled_yarn(self) -> Path | None:
        """Yarn release committed with the project: .yarnrc.yml yarnPath, else the newest .yarn/releases/*.cjs."""
        for base in (self.app_root, self.match_root, self.match_root / "src"):
            rc = base / ".yarnrc.yml"
            try:
                if rc.is_file():
                    for line in rc.read_text(encoding="utf-8").splitlines():
                        if line.strip().startswith("yarnPath:"):
                            cand = (base / line.split(":", 1)[1].strip().strip("'\"")).resolve()
                            if cand.is_file():
                                return cand
                releases = sorted((base / ".yarn" / "releases").glob("yarn-*.cjs"))
                if releases:
                    return releases[-1]
            except OSError:
                continue
        return None

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

    def prepare(self) -> list[CommandResult]:
        """Install dependencies; only the root worker provisions/migrates/seeds and builds."""
        results: list[CommandResult] = []
        manifest = self._manifest()
        env_dir = self.match_root / "env-require"
        ps1, sh = env_dir / "prepare-source.ps1", env_dir / "prepare-source.sh"
        official = None
        if sys.platform == "win32" and ps1.is_file():
            shell = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
            official = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(ps1)]
        elif sh.is_file():
            official = [shutil.which("bash") or "bash", str(sh)]
        elif ps1.is_file() and shutil.which("pwsh"):
            official = [shutil.which("pwsh"), "-NoProfile", "-File", str(ps1)]
        if self.worker_id == "main" and official:
            # The exam's own documented setup (RQ3I4OHX: bundled Yarn install + build) beats a generic guess.
            results.append(self.run(official, cwd=self.match_root, timeout=1800, _setup=True))
            if not results[-1].ok:
                return results
            doctor = self.doctor_command()
            if doctor:
                results.append(self.run(doctor, cwd=self.doctor_cwd(), timeout=120, _setup=True))
            return results
        if manifest:
            pm = self._package_manager()
            if pm == "pnpm": install = ["pnpm", "install", "--frozen-lockfile"]
            elif pm == "npm": install = ["npm", "ci"] if (self.app_root / "package-lock.json").exists() else ["npm", "install"]
            elif pm == "yarn": install = ["yarn", "install", "--immutable"]
            else: install = [pm, "install"]
            results.append(self.run(install, timeout=600, _setup=True))
            if not results[-1].ok: return results
            if any((self.app_root / p).exists() for p in ("prisma/schema.prisma", "prisma/schema")):
                if self.worker_id != "main":
                    results.append(self.run(self._package_cmd("exec", "prisma", "generate"), timeout=180, _setup=True))
                    return results
                results.extend(self._ensure_scoped_databases())
                for script in ("db:generate",):
                    if script in manifest.get("scripts", {}):
                        results.append(self.run(self._package_cmd("run", script), timeout=180, _setup=True))
                        if not results[-1].ok: return results
                results.append(self.run(self._package_cmd("exec", "prisma", "generate"), timeout=180, _setup=True))
                if not results[-1].ok: return results
                if "db:deploy" in manifest.get("scripts", {}):
                    results.append(self.run(self._package_cmd("run", "db:deploy"), timeout=300, _setup=True))
                elif (self.app_root / "prisma" / "migrations").is_dir():
                    results.append(self.run(self._package_cmd("exec", "prisma", "migrate", "deploy"), timeout=300, _setup=True))
                if results and not results[-1].ok: return results
                if "db:seed" in manifest.get("scripts", {}):
                    results.append(self.run(self._package_cmd("run", "db:seed"), timeout=300, _setup=True))
                    if not results[-1].ok: return results
        if self.worker_id != "main":
            return results
        doctor = self.doctor_command()
        if doctor:
            results.append(self.run(doctor, cwd=self.doctor_cwd(), timeout=120, _setup=True))
            if not results[-1].ok: return results
        build = self.build_command()
        if build:
            results.append(self.run(build, timeout=600, _setup=True))
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
    run_parser.add_argument("--timeout", type=float, default=180)
    run_parser.add_argument("--idle-timeout", type=float, help="Stop after this many seconds without output (capped)")
    run_parser.add_argument("--log", help="Also write the full output to this file (e.g. an evidence log)")
    run_parser.add_argument("--full", action="store_true", help="Print the full output instead of a summary")
    run_parser.add_argument("command", nargs=argparse.REMAINDER)
    session_parser = sub.add_parser("session", help="Run one local server and evidence/check command with shared ephemeral config")
    session_parser.add_argument("--server-script", default="dev", help="package.json script (falls back to start/serve/preview)")
    session_parser.add_argument("--server-cmd", help="Full server command line, e.g. from env-require/README.md (bundled yarn/pnpm resolved)")
    session_parser.add_argument("--server-cwd", help="Server working directory inside match_root (default: app root)")
    session_parser.add_argument("--server-env", action="append", help="KEY=VALUE for the server and the check (repeatable)")
    session_parser.add_argument("--url")
    session_parser.add_argument("--timeout", type=float, default=180)
    session_parser.add_argument("--log", help="Also write the full output to this file")
    session_parser.add_argument("--full", action="store_true", help="Print the full output instead of a summary")
    session_parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    runtime = ProjectRuntime(args.match_root, args.match_id, args.worker_id)
    if args.action == "prepare":
        results = runtime.prepare()
        for result in results:
            print(f"$ {shlex.join(result.argv)} [{result.returncode}{', timeout' if result.timed_out else ''}]")
            emit_output(result.stdout, result.stderr, result.returncode, None, label="prepare", runtime=runtime)
            if not result.ok: return result.returncode or 1
        return 0
    command = list(args.command)
    if command[:1] == ["--"]: command = command[1:]
    started = time.monotonic()
    if args.action == "session":
        try:
            server, note = runtime.server_command(args.server_script, args.server_cmd)
        except ValueError as exc:
            print(f"[mtm] {exc}", file=sys.stderr)
            return 2
        if note:
            print(note, end="", file=sys.stderr)
        for pair in args.server_env or []:
            key, sep, value = pair.partition("=")
            if not sep or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
                print(f"[mtm] --server-env expects KEY=VALUE, got {pair!r}", file=sys.stderr)
                return 2
            if key in ("PORT", "DATABASE_URL", "TEST_DATABASE_URL"):
                continue                     # runtime-owned, worker-isolated values
            runtime.env[key] = value
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from runner.web_session import run_web_session
        result = run_web_session(runtime, server, command, readiness_url=args.url, timeout=args.timeout,
                                 server_cwd=args.server_cwd)
    else:
        if getattr(args, "idle_timeout", None) is not None:
            os.environ["MTM_RUN_IDLE_TIMEOUT"] = str(args.idle_timeout)
        result = runtime.run(command, args.cwd, args.timeout)
    code = result.returncode or (124 if result.timed_out else 0)
    emit_output(result.stdout, result.stderr, code, time.monotonic() - started, label=" ".join(command)[:60],
                runtime=runtime, log_path=args.log, full=args.full, timed_out=result.timed_out)
    return code


# ---- Compact command output --------------------------------------------------------------------
# Everything a worker prints stays in its model context and is re-read on every later turn. A test or
# build run can print thousands of lines; the worker needs the verdict, the failures and where the full
# log is. Short output is printed unchanged. MTM_RUN_FULL_OUTPUT=1 or --full restores the old behaviour.

_SIGNAL = re.compile(r"(error|fail|✗|×|exception|traceback|assert|panic|err!|cannot find|not found|"
                     r"expected|received|timed? ?out|\bTS\d{4}\b)", re.IGNORECASE)


def summarize_output(text: str, max_signal: int = 40, tail: int = 15, width: int = 300) -> str:
    lines = text.splitlines()
    signal, seen = [], set()
    for i, line in enumerate(lines):
        if _SIGNAL.search(line) and not re.search(r"\b0 (failed|errors?)\b", line, re.IGNORECASE):
            key = line.strip()[:width]
            if key and key not in seen:
                seen.add(key)
                signal.append(f"{i + 1}: {key}")
                if len(signal) >= max_signal:
                    break
    out = []
    if signal:
        out.append(f"[mtm] lines with errors/failures (first {len(signal)}, numbered as in the log):")
        out.extend(signal)
    out.append(f"[mtm] last {min(tail, len(lines))} lines:")
    out.extend(line[:width] for line in lines[-tail:])
    return "\n".join(out)


def _run_log_dir(runtime: "ProjectRuntime") -> Path:
    # Outside the repository so a worker's `git add` can never pick it up.
    base = Path(__file__).resolve().parents[1] / "state" / (runtime.match_id or "match") / "run-logs"
    return base / (runtime.worker_id or "root")


def emit_output(stdout: str, stderr: str, code: int, seconds: float | None, label: str, runtime: "ProjectRuntime",
                log_path: str | None = None, full: bool = False, timed_out: bool = False) -> None:
    stdout, stderr = stdout or "", stderr or ""
    combined = stdout + (("\n" if stdout and not stdout.endswith("\n") else "") + stderr if stderr else "")
    limit = int(os.environ.get("MTM_RUN_OUTPUT_LIMIT", "6000"))
    written = None
    if log_path or len(combined) > limit:
        target = Path(log_path) if log_path else _run_log_dir(runtime) / (
            time.strftime("%H%M%S") + f"-{os.getpid()}-" + (re.sub(r"[^a-zA-Z0-9]+", "_", label).strip("_")[:40] or "cmd") + ".log")
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(combined, encoding="utf-8")
            written = target
        except OSError as exc:
            print(f"[mtm] could not write log {target}: {exc}", file=sys.stderr)
    if full or os.environ.get("MTM_RUN_FULL_OUTPUT") == "1" or len(combined) <= limit or written is None:
        print(stdout, end="")
        print(stderr, file=sys.stderr, end="")
        if written is not None:
            print(f"[mtm] full log also saved: {written}")
        return
    took = f" in {seconds:.1f}s" if seconds is not None else ""
    status = "TIMED OUT" if timed_out else f"exit={code}"
    print(f"[mtm] {status}{took} · {len(combined.splitlines())} lines ({len(combined) // 1024} KB) · full log: {written}")
    print(summarize_output(combined))
    print("[mtm] Output summarised to save context. Read a range of the log only if you need more "
          "(e.g. lines around a numbered failure); --full prints everything.")


if __name__ == "__main__":
    raise SystemExit(main())
