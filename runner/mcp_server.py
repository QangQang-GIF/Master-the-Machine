"""
runner/mcp_server.py: Thin MCP Helper Server.
Provides official MCP tools via JSON-RPC 2.0 over stdin/stdout for Codex root agent:
- configure_session(descriptor)
- login_match()
- refresh_match()
- start_work(backlogItemKey)
- submit_work(clientSubmissionId, backlogItemKey, gitRevision, answer)
- report_progress(event)
- wait_for_update(timeoutMs)

Stdout is strictly reserved for JSON-RPC messages; all diagnostics go to stderr.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure src/ and pack root are on sys.path dynamically
_current_dir = Path(__file__).resolve().parent
_pack_root = _current_dir.parent
_src_dir = _pack_root / "src"
for _p in [str(_pack_root), str(_src_dir)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from mtm.constants import DEFAULT_GAME_MASTER_ORIGIN
from mtm_competition.adapters.base import validate_match_projection_schema
from mtm_competition.adapters.official_game_master import (
    OfficialGameMasterAdapter,
    build_match_page_url,
)
from mtm_competition.integration.git_guard import PreSubmissionGitGuard
from mtm_competition.tasks.journal import SubmissionJournal
from mtm_competition.scheduling.native import NativeTaskCoordinator
from mtm_knowledge.bootstrap import KnowledgeBootstrap, detect_tech_stack
from runner.telemetry import (
    TelemetryLogger,
    get_telemetry_logger,
    validate_progress_event,
)


_SESSION_MEMORY: Dict[tuple[str, str], Dict[str, Any]] = {}


def _epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) / 1000 if value > 1e12 else float(value)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def normalize_native_projection(projection: Dict[str, Any], adapter: Any) -> Dict[str, Any]:
    """Translate public board aliases without assuming duration or reveal schedules."""
    match = projection.get("match") or {}
    status = projection.get("status") or match.get("status") or "UNKNOWN"
    server = next((_epoch(projection[k]) for k in ("server_epoch", "serverNow", "serverTime") if projection.get(k) is not None), None)
    if server is None and getattr(adapter, "server_epoch", None) is not None:
        server = adapter.server_epoch + max(0, time.monotonic() - adapter.server_clock_monotonic)
    remaining = next((source[k] for source in (projection, match) for k in ("remaining_seconds", "remainingSec", "remainingSeconds") if source.get(k) is not None), None)
    items = []
    for item in projection.get("backlogItems", projection.get("backlog", [])):
        key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
        if not key:
            continue
        deadline = next((_epoch(item[k]) for k in ("deadline_epoch", "deadlineEpoch", "deadlineAt", "closesAt", "closeAt", "dueAt") if item.get(k) is not None), None)
        # Explicit server countdown is pause-adjusted and preferable to wall-clock dates.
        left = next((item[k] for k in ("remainingSec", "remainingSeconds", "deadlineRemainingSec") if item.get(k) is not None), None)
        if left is not None and server is not None:
            deadline = server + max(0, float(left))
        items.append({**item, "key": str(key), "type": item.get("type") or item.get("taskType") or str(key).split("-")[0],
                      "status": item.get("status") or item.get("state") or "VISIBLE", "deadline_epoch": deadline})
    return {"status": status, "server_epoch": server, "remaining_seconds": remaining, "items": items}


def announced_test_ids(details: Dict[str, Any]) -> List[str]:
    """Inventory explicit public scenario IDs, independent of a worker's answer."""
    ids: set[str] = set()
    def walk(value: Any, rows: bool = False) -> None:
        if isinstance(value, list):
            for row in value:
                walk(row, True)
        elif isinstance(value, dict):
            if rows:
                identifier = value.get("id") or value.get("testCaseId") or value.get("scenarioId")
                if isinstance(identifier, str) and identifier.strip():
                    ids.add(identifier.strip())
            for name in ("testResults", "scenarios", "testCases", "cases", "tests", "minimumVerificationScenarios", "mvs"):
                if name in value:
                    walk(value[name], True)
        elif isinstance(value, str):
            ids.update(re.findall(r"\b(?:[A-Z0-9]+-)*(?:TC|MVS|SCENARIO|TEST)-\d+(?:-[A-Za-z0-9]+)*\b", value))
    walk(details.get("submitTemplate", {}))
    walk(details.get("minimumVerificationScenarios", details.get("mvs")), True)
    return sorted(ids)


def resolve_stored_session_credentials(workspace_root: Path, match_code: Optional[str]) -> Dict[str, Any]:
    """Retain credentials only in this MCP process, scoped to the exact match/workspace.

    Recovery receives a fresh trusted Player descriptor; never scan host private
    directories or write competition credentials to disk.
    """
    return dict(_SESSION_MEMORY.get((str(workspace_root.resolve()), str(match_code)), {}))


def save_stored_session_credentials(workspace_root: Path, match_code: str, data: Dict[str, Any]) -> None:
    """Compatibility name for memory-only retention; nothing is persisted."""
    if match_code:
        _SESSION_MEMORY[(str(workspace_root.resolve()), str(match_code))] = dict(data)


class MCPHelperServer:
    def __init__(self, workspace_root: Optional[Path | str] = None):
        raw_root = Path(workspace_root).resolve() if workspace_root is not None else Path.cwd().resolve()
        if raw_root.name.lower() == "team-sdlc-pack":
            raw_root = raw_root.parent
        self.workspace_root = raw_root
        self.descriptor: Dict[str, Any] = {}
        self.match_uuid: Optional[str] = None
        self.match_code: Optional[str] = None
        self.assigned_branch: Optional[str] = None
        self.player_session_id: str = str(uuid.uuid4())
        self.progress_log_file: Optional[Path] = None
        self.bearer_token: str = ""
        self.api_url: str = ""
        self.adapter: Optional[OfficialGameMasterAdapter] = None
        self.session_cookies: Dict[str, str] = {}
        self.journal: Optional[SubmissionJournal] = None
        self.git_guard: Optional[PreSubmissionGitGuard] = None
        self.telemetry: Optional[TelemetryLogger] = None
        self.checkout_root: Path = self.workspace_root
        self.match_root: Path = self.workspace_root
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.match_page_url: Optional[str] = None
        self.allowed_match_origins: Optional[List[str]] = None
        self.repo_descriptor: Dict[str, Any] = {}
        self.current_wave: int = 1
        self.known_backlog_items: Dict[str, Dict[str, Any]] = {}
        self.match_state: str = "IN_PROGRESS"
        self.running: bool = True
        self.native: Optional[NativeTaskCoordinator] = None
        self.latest_projection: Dict[str, Any] = {}
        self.project_runtime = None

    def _get_backlog_title(self, key: str) -> str:
        item = self.known_backlog_items.get(key, {})
        title = item.get("title") or item.get("name") or f"Backlog item {key}"
        return str(title)[:160]

    def configure_session(self, descriptor: Dict[str, Any]) -> Dict[str, Any]:
        """Configure trusted session from MTM Player descriptor."""
        if os.environ.get("MTM_ENV") == "production":
            from mtm_core.config.loader import verify_skill_checksums
            verify_skill_checksums(_pack_root)
        incoming = descriptor.get("matchId") or descriptor.get("matchCode") or descriptor.get("match_code")
        if self.match_code and incoming and self.match_code != str(incoming).strip():
            raise ValueError("A new match requires a fresh MCP session; do not reuse credentials or leases")
        merged = dict(self.descriptor)
        merged.update(descriptor)
        self.descriptor = merged

        new_code = descriptor.get("matchId") or descriptor.get("matchCode") or descriptor.get("match_code")
        if new_code:
            self.match_code = str(new_code).strip()

        new_uuid = descriptor.get("matchUuid") or descriptor.get("match_uuid") or descriptor.get("matchIdUuid")
        if new_uuid:
            self.match_uuid = str(new_uuid).strip()

        session_id = descriptor.get("playerSessionId") or descriptor.get("sessionId")
        if session_id:
            self.player_session_id = str(session_id)

        # Store session credentials and match page URL from descriptor (retaining prior if omitted)
        if descriptor.get("username") or descriptor.get("teamLoginAccount"):
            self.username = descriptor.get("username") or descriptor.get("teamLoginAccount")
        if descriptor.get("password") or descriptor.get("teamPassword"):
            self.password = descriptor.get("password") or descriptor.get("teamPassword")
        if descriptor.get("matchPageUrl") or descriptor.get("match_page_url"):
            self.match_page_url = descriptor.get("matchPageUrl") or descriptor.get("match_page_url")
        if descriptor.get("allowedMatchOrigins") or descriptor.get("allowed_origins") or descriptor.get("allowedOrigins"):
            self.allowed_match_origins = descriptor.get("allowedMatchOrigins") or descriptor.get("allowed_origins") or descriptor.get("allowedOrigins")

        # Fallback to stored session credentials if username/password/cookies/matchUuid are missing
        if not (self.username and self.password) or not self.match_uuid or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                if not self.bearer_token and stored.get("apiKeySecret") and descriptor.get("type") != "PREPARE_GAME":
                    self.bearer_token = stored["apiKeySecret"]

        # Repository descriptor: retain from prior PREPARE_GAME if START_GAME omits repository
        if descriptor.get("repository"):
            self.repo_descriptor = descriptor["repository"]
        repo_info = self.repo_descriptor

        # Assigned branch resolution: require repository.branch, retain verified branch if omitted
        assigned_branch = (
            repo_info.get("branch")
            or descriptor.get("assignedBranch")
            or descriptor.get("branch")
        )
        if assigned_branch:
            self.assigned_branch = str(assigned_branch).strip()
        elif not self.assigned_branch:
            self.assigned_branch = "main" if descriptor.get("type") == "PREPARE_GAME" else None

        # Workspace root from descriptor: update from teamWorkspace or teamSdlcPackWorkspace
        team_ws_str = descriptor.get("teamWorkspace") or descriptor.get("team_workspace")
        if team_ws_str:
            ws_path = Path(team_ws_str).resolve()
            if ws_path.name.lower() == "team-sdlc-pack":
                ws_path = ws_path.parent
            self.workspace_root = ws_path
        elif descriptor.get("teamSdlcPackWorkspace"):
            pack_ws = Path(descriptor["teamSdlcPackWorkspace"]).resolve()
            if pack_ws.name.lower() == "team-sdlc-pack":
                self.workspace_root = pack_ws.parent

        # Paths
        checkout_str = descriptor.get("checkoutRoot")
        if checkout_str:
            self.checkout_root = Path(checkout_str).resolve()

        match_root_str = descriptor.get("matchRoot")
        if match_root_str:
            self.match_root = Path(match_root_str).resolve()
        elif self.match_code:
            potential = self.checkout_root / self.match_code
            self.match_root = potential if potential.exists() else self.checkout_root
        else:
            self.match_root = self.checkout_root

        log_file_str = descriptor.get("progressLogFile")
        if log_file_str:
            self.progress_log_file = Path(log_file_str).resolve()
        elif not self.progress_log_file:
            log_dir = self.workspace_root / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            self.progress_log_file = log_dir / f"{self.match_code or 'active'}.progress.ndjson"

        # Initialize telemetry
        self.telemetry = get_telemetry_logger(
            log_path=self.progress_log_file,
            player_session_id=self.player_session_id,
        )

        # Adapter & Bearer Token Management
        is_prepare = (descriptor.get("type") == "PREPARE_GAME")
        new_token = (
            descriptor.get("apiKeySecret")
            or descriptor.get("bearerToken")
            or descriptor.get("runtimeTeamKey")
            or ""
        )
        if not is_prepare and new_token:
            self.bearer_token = new_token
        elif is_prepare and not self.bearer_token:
            self.bearer_token = ""

        base_from_page = ""
        if self.match_page_url:
            parsed_u = urllib.parse.urlparse(self.match_page_url)
            if parsed_u.scheme and parsed_u.netloc:
                base_from_page = f"{parsed_u.scheme}://{parsed_u.netloc}"

        new_api_url = (
            descriptor.get("gameMasterApiUrl")
            or descriptor.get("gameMasterUrl")
            or descriptor.get("baseUrl")
            or base_from_page
            or descriptor.get("origin")
            or ""
        ).rstrip("/")
        if new_api_url:
            self.api_url = new_api_url
        elif not self.api_url:
            self.api_url = DEFAULT_GAME_MASTER_ORIGIN

        # Update existing adapter instance WITHOUT replacing it (preserving cookies)
        if self.adapter is not None:
            if self.bearer_token:
                self.adapter.auth_token = self.bearer_token
                self.adapter.api_key = self.bearer_token
            if self.api_url:
                self.adapter.base_url = self.api_url
                self.adapter.api_base_url = self.api_url
            if self.match_uuid:
                self.adapter.match_uuid = self.match_uuid
        else:
            self.adapter = OfficialGameMasterAdapter(
                base_url=self.api_url,
                auth_token=self.bearer_token,
                match_uuid=self.match_uuid,
                max_retries=1,
            )
            if self.session_cookies:
                self.adapter.session_cookies.update(self.session_cookies)

        # Auto-login to establish session cookies and resolve match UUID if credentials exist
        if (not self.session_cookies or not (self.adapter and self.adapter.session_cookies)) and self.username and self.password and self.adapter:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: auto-login in configure_session: {e}\n")

        # Attempt to resolve match UUID from public projection if missing
        if not self.match_uuid and self.match_code and self.adapter:
            try:
                proj = self.adapter.get_match_projection(self.match_code)
                resolved_uuid = proj.get("matchUuid") or proj.get("id")
                if resolved_uuid:
                    self.match_uuid = str(resolved_uuid)
                    self.adapter.match_uuid = self.match_uuid
            except Exception:
                pass

        # Journal & Git Guard
        db_path = descriptor.get("journalDbPath") or (self.workspace_root / "team-sdlc-pack" / "state" / str(self.match_code or "active") / "submissions.db")
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        if self.journal is None or str(getattr(self.journal, "db_path", "")) != str(db_path):
            if self.journal is not None:
                try:
                    self.journal.close()
                except Exception:
                    pass
            self.journal = SubmissionJournal(db_path=db_path)
        self.git_guard = PreSubmissionGitGuard(repo_path=self.checkout_root)

        if descriptor.get("type") in ("START_GAME", "RESUME_GAME") and (
            descriptor.get("nativeScheduling") or os.environ.get("MTM_NATIVE_COORDINATION") == "1"
        ):
            folder = self.repo_descriptor.get("folder") or self.match_root.relative_to(self.checkout_root).as_posix()
            self.native = NativeTaskCoordinator(
                state_dir=self.workspace_root / "team-sdlc-pack" / "state" / str(self.match_code) / "native",
                checkout_root=self.checkout_root, match_root=self.match_root,
                repository_folder=folder, match_id=str(self.match_code),
                max_workers=int(os.environ.get("MTM_NATIVE_MAX_WORKERS", "2")),
                normal_workers=int(os.environ.get("MTM_NATIVE_NORMAL_WORKERS", "1")),
            )
            self._reconcile_native_receipts()

        # Record initial items from descriptor
        for item in descriptor.get("backlogItems", []):
            if isinstance(item, dict):
                k = item.get("id") or item.get("backlogItemKey") or item.get("key")
                if k:
                    self.known_backlog_items[str(k)] = item

        # PREPARE_GAME Pipeline (R1 & R5)
        if descriptor.get("type") == "PREPARE_GAME":
            is_offline = bool(descriptor.get("offline"))
            # Player already owns ORCHESTRATOR_STARTED. Retain compatibility for
            # standalone tests without duplicating an existing session milestone.
            existing_started = False
            if self.progress_log_file and self.progress_log_file.exists():
                for line in self.progress_log_file.read_text(encoding="utf-8").splitlines():
                    try:
                        event = json.loads(line)
                        if event.get("type") == "ORCHESTRATOR_STARTED" and event.get("playerSessionId") == self.player_session_id:
                            existing_started = True
                            break
                    except ValueError:
                        continue
            if not existing_started:
                self.telemetry.emit_orchestrator_started("Team Orchestrator started in preparation phase.")

            # 1. Genuine Git Invariants Verification (R2)
            try:
                from mtm_core.git import verify_assigned_repository
                repo_res = verify_assigned_repository(self.checkout_root, repo_info or descriptor)
                head_sha = repo_res.get("head_sha", "0" * 40)
                self.telemetry.emit_preparation_status("GIT_AUTH", "COMPLETED", "Assigned repository access and origin verified.")
                self.telemetry.emit_preparation_status("REPOSITORY", "COMPLETED", "Assigned repository and branch verified.")
            except Exception as exc:
                self.telemetry.emit_preparation_status("REPOSITORY", "FAILED", f"Git repository verification failed: {exc}")
                raise RuntimeError(f"PREPARE_GAME failed: Git verification failed: {exc}") from exc

            self.telemetry.emit_preparation_status("ENVIRONMENT", "IN_PROGRESS", "Bootstrapping knowledge and verifying environment doctor.")

            # 2. Knowledge bootstrap into state/<matchCode>/knowledge/PROJECT_MAP.md (outside repo)
            kb_output_path = (
                self.workspace_root
                / "team-sdlc-pack"
                / "state"
                / (self.match_code or "active")
                / "knowledge"
                / "PROJECT_MAP.md"
            )
            if not kb_output_path.parent.exists():
                kb_output_path.parent.mkdir(parents=True, exist_ok=True)

            # Resolve 3-tier layout: checkoutRoot -> matchRoot -> appRoot (R5)
            app_root = (self.match_root / "src") if (self.match_root / "src" / "package.json").exists() else self.match_root
            from runner.project_runtime import ProjectRuntime
            self.project_runtime = ProjectRuntime(self.match_root, str(self.match_code or "prepare"))
            project_env = self.project_runtime.env

            bootstrap = KnowledgeBootstrap(
                repo_path=self.checkout_root,
                match_code=self.match_code or "PREP",
                branch=self.assigned_branch,
                folder=repo_info.get("folder", self.match_code or "repo"),
                checkout_root=self.checkout_root,
                match_root=self.match_root,
                app_root=app_root,
            )

            # 3. Locate and execute genuine environment doctor or build command (R6)
            tech = detect_tech_stack(app_root, match_root=self.match_root)
            doc_file = None
            if sys.platform.startswith("win"):
                doctor_candidates = [
                    self.match_root / "env-require" / "doctor.ps1",
                    self.match_root / "doctor.ps1",
                    self.checkout_root / "env-require" / "doctor.ps1",
                    self.checkout_root / "doctor.ps1",
                    app_root / "env-require" / "doctor.ps1",
                    app_root / "doctor.ps1",
                    self.match_root / "env-require" / "doctor.sh",
                    self.match_root / "doctor.sh",
                    self.checkout_root / "env-require" / "doctor.sh",
                    self.checkout_root / "doctor.sh",
                    app_root / "env-require" / "doctor.sh",
                    app_root / "doctor.sh",
                ]
            else:
                doctor_candidates = [
                    self.match_root / "env-require" / "doctor.sh",
                    self.match_root / "doctor.sh",
                    self.checkout_root / "env-require" / "doctor.sh",
                    self.checkout_root / "doctor.sh",
                    app_root / "env-require" / "doctor.sh",
                    app_root / "doctor.sh",
                    self.match_root / "env-require" / "doctor.ps1",
                    self.match_root / "doctor.ps1",
                    self.checkout_root / "env-require" / "doctor.ps1",
                    self.checkout_root / "doctor.ps1",
                    app_root / "env-require" / "doctor.ps1",
                    app_root / "doctor.ps1",
                ]

            for cand in doctor_candidates:
                if cand.exists():
                    doc_file = cand
                    break

            build_pass = False
            build_exit_code = 1
            fail_reason = ""

            if doc_file:
                cwd_run = doc_file.parent.parent if doc_file.parent.name == "env-require" else doc_file.parent
                if not cwd_run.resolve().is_relative_to(self.match_root):
                    cwd_run = self.match_root
                if doc_file.suffix == ".ps1":
                    if sys.platform.startswith("win"):
                        cmd_run = ["powershell.exe", "-NoProfile", "-File", str(doc_file)]
                    else:
                        pwsh_bin = shutil.which("pwsh") or shutil.which("powershell")
                        if pwsh_bin:
                            cmd_run = [pwsh_bin, "-NoProfile", "-File", str(doc_file)]
                        else:
                            sh_companion = doc_file.with_suffix(".sh")
                            if sh_companion.exists():
                                doc_file = sh_companion
                                cmd_run = ["bash", str(doc_file)]
                            else:
                                raise RuntimeError("PowerShell doctor requires pwsh or a documented shell companion; never run .ps1 with bash")
                else:
                    cmd_run = ["bash" if not sys.platform.startswith("win") else "sh", str(doc_file)]
                try:
                    res = self.project_runtime.run(cmd_run, cwd=cwd_run, timeout=120)
                    build_exit_code = res.returncode
                    doc_output = (res.stdout or "") + "\n" + (res.stderr or "")
                    has_ready = "Environment status: READY" in doc_output
                    if res.returncode == 0 and has_ready:
                        build_pass = True
                    else:
                        fail_reason = (
                            f"Doctor script exited with code {res.returncode}; "
                            f"'Environment status: READY' in output: {has_ready}"
                        )
                except Exception as exc:
                    build_pass = False
                    build_exit_code = 1
                    fail_reason = f"Execution error running doctor script: {exc}"
            else:
                v_cmd = tech.get("Build command") or tech.get("Typecheck command")
                if v_cmd and not v_cmd.startswith("default "):
                    try:
                        res = subprocess.run(
                            v_cmd,
                            cwd=str(app_root),
                            shell=True,
                            capture_output=True,
                            text=True,
                            timeout=60,
                            check=False,
                            env=project_env,
                        )
                        build_exit_code = res.returncode
                        if res.returncode == 0:
                            build_pass = True
                        else:
                            fail_reason = f"Build command '{v_cmd}' exited with code {res.returncode}"
                    except Exception as exc:
                        build_pass = False
                        build_exit_code = 1
                        fail_reason = f"Execution error running build command: {exc}"
                else:
                    build_pass = False
                    build_exit_code = 1
                    fail_reason = "No doctor script (doctor.ps1/doctor.sh) or valid build command found in repository"

            # Record verified status in PROJECT_MAP.md
            if build_pass and doc_file and self.project_runtime.build_command():
                actual_build = self.project_runtime.run(self.project_runtime.build_command(), cwd=app_root, timeout=300)
                build_pass = actual_build.ok
                build_exit_code = actual_build.returncode
                if not build_pass:
                    fail_reason = f"Declared app build failed with exit {build_exit_code}: {actual_build.stderr[-500:]}"
            bootstrap.generate_project_map(
                output_path=kb_output_path,
                verification_result={"passed": build_pass, "exit_code": build_exit_code},
                run_verification=False,
            )

            if not build_pass:
                self.telemetry.emit_preparation_status("BUILD", "FAILED", f"Environment build verification failed: {fail_reason}")
                raise RuntimeError(f"PREPARE_GAME failed: Environment verification failed: {fail_reason}")

            self.telemetry.emit_preparation_status("BUILD", "COMPLETED", "Environment build verified.")
            self.telemetry.emit_preparation_status("ENVIRONMENT", "COMPLETED", "Project toolchain and doctor checks verified.")

            # 4. Genuine Session Match Authentication (R1)
            username = self.username or descriptor.get("username")
            password = self.password or descriptor.get("password")
            match_page_url = self.match_page_url or descriptor.get("matchPageUrl")
            allowed_origins = self.allowed_match_origins or descriptor.get("allowedMatchOrigins") or descriptor.get("allowedOrigins")

            if is_offline:
                self.telemetry.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Match credentials verified (offline test mode).")
            else:
                if not username or not password or not match_page_url or not self.adapter:
                    missing = [f for f, v in [("username", username), ("password", password), ("matchPageUrl", match_page_url)] if not v]
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Missing required PREPARE_GAME fields: {', '.join(missing)}")
                    raise RuntimeError(f"PREPARE_GAME failed: Missing required authentication fields: {', '.join(missing)}")

                try:
                    target_match_url = build_match_page_url(match_page_url, self.match_code) if self.match_code else match_page_url
                    auth_res = self.adapter.login_session(
                        username=username,
                        password=password,
                        match_page_url=target_match_url,
                        allowed_origins=allowed_origins,
                        match_id=self.match_code,
                    )
                    if auth_res.get("status") != "AUTHENTICATED":
                        raise RuntimeError(f"login_session returned non-authenticated status: {auth_res.get('status')}")

                    cookies_returned = auth_res.get("cookies") or {}
                    self.session_cookies.update(cookies_returned)

                    proj = auth_res.get("projection") or {}
                    if self.match_code:
                        proj_code = proj.get("matchCode")
                        proj_uuid = proj.get("matchUuid") or proj.get("id")
                        code_match = bool(proj_code and str(proj_code).upper() == str(self.match_code).upper())
                        uuid_match = bool(proj_uuid and str(proj_uuid).lower() == str(self.match_code).lower())
                        if not (code_match or uuid_match):
                            raise RuntimeError(
                                f"Match assignment mismatch: expected {self.match_code}, got {proj_code or proj_uuid}"
                            )

                    if proj.get("matchUuid"):
                        self.match_uuid = str(proj["matchUuid"])
                        self.adapter.match_uuid = self.match_uuid
                    if proj.get("matchCode") and not self.match_code:
                        self.match_code = str(proj["matchCode"])
                except Exception as exc:
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Match session login verification failed: {exc}")
                    raise RuntimeError(f"PREPARE_GAME failed: Match session login verification failed: {exc}") from exc

                self.telemetry.emit_preparation_status("MATCH_LOGIN", "COMPLETED", "Match credentials verified.")

            # 5. Emit ENVIRONMENT_READY only when all 3 gates have genuinely passed!
            repo_url_val = repo_info.get("url", "")
            if repo_url_val and not repo_url_val.startswith("https://"):
                clean_name = Path(repo_url_val).name
                if not clean_name.endswith(".git"):
                    clean_name = f"{clean_name}.git"
                repo_url_val = f"https://git.mtm.local/{clean_name}"

            self.telemetry.emit_environment_ready(
                request_id=descriptor.get("requestId", f"prepare-{self.match_code or '1'}"),
                repo_url=repo_url_val,
                branch=self.assigned_branch,
                folder=repo_info.get("folder", self.match_code or ""),
                revision=head_sha,
                build="PASS",
                match_login="PASS",
            )

        return {
            "status": "CONFIGURED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "playerSessionId": self.player_session_id,
            "progressLogFile": str(self.progress_log_file),
        }

    def login_match(self) -> Dict[str, Any]:
        """Verify authentication session and query match state."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # Ensure credentials resolved if missing
        if not (self.username and self.password) or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                    self.adapter.match_uuid = self.match_uuid
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                    self.adapter.session_cookies.update(stored["cookies"])

        # Ensure session authentication with Game Master
        if (not self.session_cookies or not self.adapter.session_cookies) and self.username and self.password:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: login_session in login_match: {e}\n")

        # Actually verify connection and match access against Game Master (fail closed on failure)
        has_credentials = bool(self.bearer_token or self.descriptor.get("apiKeySecret") or self.descriptor.get("gameMasterApiUrl") or self.descriptor.get("baseUrl") or self.session_cookies)
        is_offline = bool(self.descriptor.get("offline") or not has_credentials)

        try:
            state = self.adapter.get_match_projection(self.match_code or self.match_uuid)
            self.match_state = state.get("status", "IN_PROGRESS")
            if not self.match_uuid and state.get("matchUuid"):
                self.match_uuid = state.get("matchUuid")
                self.adapter.match_uuid = self.match_uuid
        except Exception as exc:
            if not is_offline:
                if self.telemetry:
                    self.telemetry.emit_preparation_status("MATCH_LOGIN", "FAILED", f"Match login failed: {exc}")
                raise RuntimeError(f"Match authentication failed for {self.match_code}: {exc}") from exc
            else:
                sys.stderr.write(f"Offline fallback for login_match: {exc}\n")

        return {
            "status": "AUTHENTICATED",
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "matchState": self.match_state,
        }

    def refresh_match(self) -> Dict[str, Any]:
        """Read public match projection, validate schema, and track wave releases (waves 1..3)."""
        if not self.adapter:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # Ensure credentials resolved if missing
        if not (self.username and self.password) or not self.session_cookies:
            stored = resolve_stored_session_credentials(self.workspace_root, self.match_code)
            if stored:
                if not self.username and stored.get("username"):
                    self.username = stored["username"]
                if not self.password and stored.get("password"):
                    self.password = stored["password"]
                if not self.match_page_url and stored.get("matchPageUrl"):
                    self.match_page_url = stored["matchPageUrl"]
                if not self.match_uuid and stored.get("matchUuid"):
                    self.match_uuid = stored["matchUuid"]
                    self.adapter.match_uuid = self.match_uuid
                if not self.session_cookies and stored.get("cookies"):
                    self.session_cookies.update(stored["cookies"])
                    self.adapter.session_cookies.update(stored["cookies"])

        # Ensure session authentication with Game Master
        if (not self.session_cookies or not self.adapter.session_cookies) and self.username and self.password:
            try:
                auth_res = self.adapter.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or "https://masterthemachine.tech/match",
                    allowed_origins=self.allowed_match_origins,
                    match_id=self.match_code,
                )
                if auth_res.get("cookies"):
                    self.session_cookies.update(auth_res["cookies"])
                    self.adapter.session_cookies.update(auth_res["cookies"])
                proj = auth_res.get("projection") or {}
                if proj.get("matchUuid"):
                    self.match_uuid = str(proj["matchUuid"])
                    self.adapter.match_uuid = self.match_uuid
                save_stored_session_credentials(self.workspace_root, self.match_code, {
                    "matchCode": self.match_code,
                    "matchUuid": self.match_uuid,
                    "username": self.username,
                    "password": self.password,
                    "cookies": self.session_cookies,
                    "matchPageUrl": self.match_page_url,
                    "apiKeySecret": self.bearer_token,
                })
            except Exception as e:
                sys.stderr.write(f"Warning: login_session in refresh_match: {e}\n")

        newly_discovered: List[str] = []
        has_credentials = bool(self.bearer_token or self.descriptor.get("apiKeySecret") or self.descriptor.get("gameMasterApiUrl") or self.descriptor.get("baseUrl") or self.match_page_url or self.session_cookies)
        is_offline = bool(self.descriptor.get("offline") or not has_credentials)

        try:
            projection = self.adapter.get_match_projection(self.match_code or self.match_uuid)
            projection = validate_match_projection_schema(projection)
        except Exception as exc:
            if not is_offline:
                raise RuntimeError(f"Failed to refresh match projection for {self.match_code}: {exc}") from exc
            else:
                sys.stderr.write(f"Offline fallback for refresh_match: {exc}\n")
                projection = {
                    "matchUuid": self.match_uuid or "offline-match-uuid",
                    "matchCode": self.match_code or "offline-match-code",
                    "status": self.match_state or "IN_PROGRESS",
                    "wave": self.current_wave,
                    "backlogItems": [],
                }
                projection = validate_match_projection_schema(projection)

        self.match_state = projection.get("status", self.match_state or "IN_PROGRESS")
        if not self.match_uuid and projection.get("matchUuid"):
            self.match_uuid = projection.get("matchUuid")
            self.adapter.match_uuid = self.match_uuid

        self.latest_projection = projection
        if self.native:
            self.native.register_projection(normalize_native_projection(projection, self.adapter))
            self._reconcile_native_receipts()

        # Release schedules belong to the server; never infer them from elapsed time.
        proj_wave = projection.get("wave")
        if proj_wave is not None:
            try:
                self.current_wave = max(self.current_wave, int(proj_wave))
            except (ValueError, TypeError):
                pass

        items = projection.get("backlogItems", [])
        for item in items:
            if isinstance(item, dict):
                item_wave = item.get("wave")
                if item_wave is not None:
                    try:
                        self.current_wave = max(self.current_wave, int(item_wave))
                    except (ValueError, TypeError):
                        pass

                # Always prioritize clean business key (BUG-01, CR-01) over internal server UUID
                key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId") or item.get("id")
                if key and str(key) not in self.known_backlog_items:
                    key_str = str(key)
                    self.known_backlog_items[key_str] = item
                    # Also alias by server UUID so lookups by UUID resolve to the same item
                    if item.get("id") and str(item.get("id")) != key_str:
                        self.known_backlog_items[str(item.get("id"))] = item
                    newly_discovered.append(key_str)
                    # Technical Guide Step 9: MUST emit DISCOVERED for every new item
                    if self.telemetry:
                        self.telemetry.emit_backlog_status(
                            backlog_id=key_str,
                            title=self._get_backlog_title(key_str),
                            status="DISCOVERED",
                            summary=f"Backlog item {key_str} discovered on match board.",
                            revision=None,
                            receipt_id=None,
                        )
                elif key:
                    # Status/deadline/grading transitions must replace stale discovery data.
                    key_str = str(key)
                    self.known_backlog_items[key_str] = item
                    if item.get("id") and str(item["id"]) != key_str:
                        self.known_backlog_items[str(item["id"])] = item

        return {
            "status": self.match_state,
            "matchUuid": self.match_uuid,
            "matchCode": self.match_code,
            "wave": self.current_wave,
            "backlogItems": list({str(i.get("key") or i.get("backlogItemKey") or i.get("backlogId") or i.get("id")): i for i in self.known_backlog_items.values()}.values()),
            "newlyDiscovered": newly_discovered,
            "scheduling": normalize_native_projection(projection, self.adapter),
        }

    def start_work(self, backlog_item_key: str) -> Dict[str, Any]:
        """Start work on an open backlog item with automatic telemetry."""
        if not self.adapter or not self.match_uuid:
            raise RuntimeError("Session not configured. Call configure_session first.")

        if self.native:
            self.refresh_match()
            if backlog_item_key not in [t["key"] for t in self.native.plan_tasks()["selected"]]:
                raise ValueError("Item is not eligible under current server state/capacity/deadline")

        # Translate UUID to clean business key if needed
        if backlog_item_key in self.known_backlog_items:
            item = self.known_backlog_items[backlog_item_key]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                backlog_item_key = str(clean_key)

        origin = self.adapter.api_base_url
        path = f"/api/v1/matches/{self.match_uuid}/backlogs/start"

        # 1. Automatic telemetry: REQUESTED
        if self.telemetry:
            self.telemetry.emit_game_master_api(
                operation="START_WORK",
                phase="REQUESTED",
                backlog_id=backlog_item_key,
                method="POST",
                origin=origin,
                path=path,
                http_status=None,
                request_id=None,
                summary=f"Calling Game Master start work for {backlog_item_key}",
            )

        try:
            result = self.adapter.start_working(
                match_uuid_or_task_id=self.match_uuid,
                backlog_item_key=backlog_item_key,
            )

            if self.native:
                self.native.register_started(backlog_item_key, result)

            req_id = result.get("requestId") or result.get("request_id")
            # 2. Automatic telemetry: SUCCEEDED & IN_PROGRESS
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="START_WORK",
                    phase="SUCCEEDED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=200 if result.get("replay") else 201,
                    request_id=req_id,
                    summary=f"Start work succeeded for {backlog_item_key}",
                )
                self.telemetry.emit_backlog_status(
                    backlog_id=backlog_item_key,
                    title=self._get_backlog_title(backlog_item_key),
                    status="IN_PROGRESS",
                    summary=f"Work started on {backlog_item_key}",
                    revision=None,
                    receipt_id=None,
                )

            # Write template to answers/<key>/submit.json
            submit_template = result.get("submitTemplate")
            if submit_template is not None and not self.native:
                ans_dir = self.match_root / "answers" / backlog_item_key
                ans_dir.mkdir(parents=True, exist_ok=True)
                template_path = ans_dir / "submit.json"
                if not template_path.exists():
                    template_path.write_text(json.dumps(submit_template, indent=2), encoding="utf-8")

            # Write any challenge files delivered with start_work
            delivered_files = result.get("files")
            if isinstance(delivered_files, dict):
                for rel_path, file_content in delivered_files.items():
                    try:
                        target_file = self.match_root / rel_path
                        from mtm_competition.integration.git_guard import is_forbidden_path
                        if not target_file.resolve().is_relative_to(self.match_root.resolve()) or is_forbidden_path(rel_path)[0]:
                            raise ValueError("Delivered path escapes match workspace or is immutable")
                        target_file.parent.mkdir(parents=True, exist_ok=True)
                        if isinstance(file_content, str):
                            target_file.write_text(file_content, encoding="utf-8")
                        elif isinstance(file_content, bytes):
                            target_file.write_bytes(file_content)
                    except Exception as file_err:
                        sys.stderr.write(f"Warning: could not write delivered file {rel_path}: {file_err}\n")

            return result

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            req_id = getattr(exc, "request_id", None)
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="START_WORK",
                    phase="FAILED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=status_code,
                    request_id=req_id,
                    summary=f"Start work failed for {backlog_item_key}: {exc}",
                )
            raise

    def submit_work(
        self,
        client_submission_id: str,
        backlog_item_key: str,
        git_revision: str,
        answer: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Guard, journal, submit with exact body bytes, and emit automatic telemetry."""
        if not self.adapter or not self.match_uuid or not self.git_guard or not self.journal:
            raise RuntimeError("Session not configured. Call configure_session first.")

        # Translate UUID to clean business key if needed
        if backlog_item_key in self.known_backlog_items:
            item = self.known_backlog_items[backlog_item_key]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                backlog_item_key = str(clean_key)

        assigned_branch = self.assigned_branch or self.descriptor.get("assignedBranch")
        if not assigned_branch:
            raise ValueError("No assigned branch configured for submission")

        required_test_ids: List[str] = []
        if self.native:
            self.refresh_match()
            self.native.assert_can_submit(backlog_item_key)
            # Rechecking an existing lock is idempotent; another item cannot push in between.
            self.native.begin_integration(backlog_item_key)
            details = self.native.task_details(backlog_item_key)["data"].get("start_details", {})
            required_test_ids = announced_test_ids(details)

        # 1. Pre-submission Git Invariant Verification (GIT-01..05)
        self.git_guard.validate_pre_submission(
            target_commit_sha=git_revision,
            assigned_branch_ref=assigned_branch,
            backlog_item_key=backlog_item_key,
            answer_payload=answer,
            match_root=self.match_root,
            checkout_root=self.checkout_root,
            require_remote=False if self.descriptor.get("offline") else True,
            raise_on_error=True,
            required_test_ids=required_test_ids,
        )

        # 2. Record In-Flight Submission Journal with canonical serialized bytes (API-06, API-07)
        payload = {
            "clientSubmissionId": client_submission_id,
            "backlogItemKey": backlog_item_key,
            "gitRevision": git_revision,
            "answer": answer,
        }
        serialized_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.journal.record_in_flight(
            match_uuid=self.match_uuid,
            backlog_item_key=backlog_item_key,
            client_submission_id=client_submission_id,
            idempotency_key=client_submission_id,
            request_body_bytes=serialized_bytes,
            git_revision=git_revision,
            remote_ref=assigned_branch,
        )

        origin = self.adapter.api_base_url
        path = f"/api/v1/matches/{self.match_uuid}/submissions"

        # 3. Telemetry: REQUESTED
        if self.telemetry:
            self.telemetry.emit_game_master_api(
                operation="SUBMIT",
                phase="REQUESTED",
                backlog_id=backlog_item_key,
                method="POST",
                origin=origin,
                path=path,
                http_status=None,
                request_id=None,
                summary=f"Submitting work for {backlog_item_key}",
            )

        try:
            # 4. Submit via Official Adapter passing exact serialized bytes
            receipt = self.adapter.submit_work(
                match_uuid=self.match_uuid,
                client_submission_id=client_submission_id,
                idempotency_key=client_submission_id,
                backlog_item_key=backlog_item_key,
                git_revision=git_revision,
                answer=answer,
                serialized_body_bytes=serialized_bytes,
            )

            # 5. Strictly validate receipt: require real submission ID (P0.4)
            submission_obj = receipt.get("submission") if isinstance(receipt.get("submission"), dict) else receipt
            server_sub_id = submission_obj.get("id") or receipt.get("receiptId")
            if not server_sub_id or server_sub_id == "sub-accepted":
                raise RuntimeError(
                    f"Game Master response lacked valid official submission ID (receipt: {receipt}). "
                    "Journal remains IN_FLIGHT."
                )

            # 6. Mark Accepted in Journal
            self.journal.record_receipt(
                match_uuid=self.match_uuid,
                backlog_item_key=backlog_item_key,
                receipt_id=str(server_sub_id),
                http_code=201,
            )

            if self.native:
                self.native.mark_submitted(backlog_item_key, str(server_sub_id))

            req_id = receipt.get("requestId") or submission_obj.get("requestId")

            # 7. Telemetry: SUCCEEDED & SUBMITTED
            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="SUBMIT",
                    phase="SUCCEEDED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=201,
                    request_id=req_id,
                    summary=f"Submission accepted for {backlog_item_key}",
                )
                self.telemetry.emit_backlog_status(
                    backlog_id=backlog_item_key,
                    title=self._get_backlog_title(backlog_item_key),
                    status="SUBMITTED",
                    summary=f"Submission accepted for {backlog_item_key}",
                    revision=git_revision,
                    receipt_id=str(server_sub_id),
                )

            return receipt

        except Exception as exc:
            status_code = getattr(exc, "status_code", None)
            req_id = getattr(exc, "request_id", None)
            err_code = getattr(exc, "error_code", None) or getattr(exc, "code", None)

            # Distinguish deterministic 4xx rejection (excluding 429) from unknown outcome (timeouts, 5xx)
            if status_code is not None and 400 <= status_code < 500 and status_code != 429:
                err_code_str = str(err_code or "")
                if status_code == 409 and ("already_submitted" in err_code_str.lower() or "already submitted" in str(exc).lower()):
                    # R5: 409 ALREADY_SUBMITTED retains in-flight lock, requiring reconciliation against server records
                    self.journal.record_already_submitted(
                        match_uuid=self.match_uuid,
                        backlog_item_key=backlog_item_key,
                        http_code=status_code,
                        error_code=err_code or "ALREADY_SUBMITTED",
                        error_message=str(exc),
                    )
                else:
                    self.journal.record_rejection(
                        match_uuid=self.match_uuid,
                        backlog_item_key=backlog_item_key,
                        http_code=status_code,
                        error_code=err_code,
                        error_message=str(exc),
                    )

            if self.telemetry:
                self.telemetry.emit_game_master_api(
                    operation="SUBMIT",
                    phase="FAILED",
                    backlog_id=backlog_item_key,
                    method="POST",
                    origin=origin,
                    path=path,
                    http_status=status_code,
                    request_id=req_id,
                    summary=f"Submission failed for {backlog_item_key}: {exc}",
                )
            raise

    def report_progress(self, event: Dict[str, Any]) -> Dict[str, Any]:
        """Validate against schema and append progress event."""
        if not self.telemetry:
            log_path = self.progress_log_file or (self.workspace_root / "logs" / "progress.ndjson")
            self.telemetry = get_telemetry_logger(log_path, self.player_session_id)

        # Translate UUID in backlogId if known
        raw_bid = event.get("backlogId")
        if raw_bid and str(raw_bid) in self.known_backlog_items:
            item = self.known_backlog_items[str(raw_bid)]
            clean_key = item.get("key") or item.get("backlogItemKey") or item.get("backlogId")
            if clean_key:
                event["backlogId"] = str(clean_key)

        # Ensure envelope fields
        if "schemaVersion" not in event:
            event["schemaVersion"] = 1
        if "playerSessionId" not in event:
            event["playerSessionId"] = self.player_session_id

        validate_progress_event(event)
        self.telemetry.log_event(event)
        return {"status": "RECORDED"}

    def _native_coordinator(self) -> NativeTaskCoordinator:
        if not self.native:
            raise RuntimeError("Native scheduling requires START_GAME with MTM_NATIVE_COORDINATION=1")
        return self.native

    def _reconcile_native_receipts(self) -> None:
        if not self.native or not self.journal or not self.match_uuid:
            return
        # Bridge the durable receipt/coordinator crash window; never resend accepted work.
        rows = self.journal.conn.execute(
            "SELECT backlog_item_key,receipt_id FROM submission_journal WHERE match_uuid=? AND state='ACCEPTED' AND receipt_id IS NOT NULL",
            (self.match_uuid,),
        ).fetchall()
        for row in rows:
            try:
                phase = self.native.task_details(row["backlog_item_key"])["phase"]
            except ValueError:
                continue
            if phase in ("INTEGRATING", "INTEGRATED", "SUBMITTED"):
                self.native.mark_submitted(row["backlog_item_key"], row["receipt_id"])

    def plan_tasks(self) -> Dict[str, Any]:
        self.refresh_match()
        return self._native_coordinator().plan_tasks()

    def claim_task(self, backlog_item_key: str, worker_id: str, estimated_seconds: int = 420) -> Dict[str, Any]:
        return self._native_coordinator().claim_task(backlog_item_key, worker_id, estimated_seconds)

    def record_worker_result(self, backlog_item_key: str, worker_result: Dict[str, Any]) -> Dict[str, Any]:
        return self._native_coordinator().record_worker_result(backlog_item_key, worker_result)

    def integration_gate(self, backlog_item_key: str, action: str = "begin") -> Dict[str, Any]:
        native = self._native_coordinator()
        if action == "begin":
            self.refresh_match()
            return native.begin_integration(backlog_item_key)
        if action == "failed":
            return native.end_integration(backlog_item_key, success=False)
        raise ValueError("integration_gate action must be begin or failed")

    def reconcile_worker(self, backlog_item_key: str, confirmed_stopped: bool, reason: str, requeue: bool = True) -> Dict[str, Any]:
        return self._native_coordinator().reconcile_worker(
            backlog_item_key, confirmed_stopped=confirmed_stopped, reason=reason, requeue=requeue)

    def wait_for_update(self, timeout_ms: int = 5000) -> Dict[str, Any]:
        """Bounded serial polling detecting wave releases and match status transitions."""
        bounded_ms = min(max(100, timeout_ms), 55000)
        start_time = time.time()
        elapsed_ms = 0
        poll_interval = 2.0

        while elapsed_ms < bounded_ms:
            # Poll match state and waves
            if self.adapter:
                old_wave = self.current_wave
                res = self.refresh_match()
                new_items = res.get("newlyDiscovered", [])
                if new_items or self.current_wave > old_wave:
                    return {
                        "status": "UPDATED",
                        "matchState": self.match_state,
                        "wave": self.current_wave,
                        "newItems": new_items,
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }
                if self.match_state == "FINISHED":
                    return {
                        "status": "FINISHED",
                        "matchState": "FINISHED",
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }
                if self.match_state == "PAUSED":
                    return {
                        "status": "PAUSED",
                        "matchState": "PAUSED",
                        "elapsedMs": int((time.time() - start_time) * 1000),
                    }

            remaining_sec = (bounded_ms - elapsed_ms) / 1000.0
            sleep_sec = min(poll_interval, remaining_sec)
            if sleep_sec > 0:
                time.sleep(sleep_sec)
            elapsed_ms = int((time.time() - start_time) * 1000)

        return {"status": "TIMEOUT", "elapsedMs": elapsed_ms, "matchState": self.match_state}


def get_tool_definitions() -> List[Dict[str, Any]]:
    native_tools = [
        ("plan_tasks", "Refresh server board and return eligible native tasks, reserved urgent capacity, and ready integrations.", {}, []),
        ("claim_task", "Reserve an eligible task after Start Work; return isolated worktree and sanitized TaskEnvelope for native spawn.",
         {"backlogItemKey": {"type": "string"}, "workerId": {"type": "string"}, "estimatedSeconds": {"type": "integer", "minimum": 10, "default": 420}}, ["backlogItemKey", "workerId"]),
        ("record_worker_result", "Validate completed native WorkerResult and required local artifacts before root integration.",
         {"backlogItemKey": {"type": "string"}, "workerResult": {"type": "object"}}, ["backlogItemKey", "workerResult"]),
        ("integration_gate", "Root-only exclusive integration/push/submit lock. Begin before Git mutation; failed releases after reconciliation. Submit receipt releases success.",
         {"backlogItemKey": {"type": "string"}, "action": {"type": "string", "enum": ["begin", "failed"], "default": "begin"}}, ["backlogItemKey"]),
        ("reconcile_worker", "Reconcile a stopped/completed failed native worker. Never confirm stopped while it is still writing.",
         {"backlogItemKey": {"type": "string"}, "confirmedStopped": {"type": "boolean"}, "reason": {"type": "string"}, "requeue": {"type": "boolean", "default": True}}, ["backlogItemKey", "confirmedStopped", "reason"]),
    ]
    return [{"name": name, "description": description, "inputSchema": {"type": "object", "properties": props, "required": required, "additionalProperties": False}}
            for name, description, props, required in native_tools] + [
        {
            "name": "configure_session",
            "description": "Initialize match session credentials, paths, and telemetry logger.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "descriptor": {"type": "object", "description": "Match configuration descriptor"}
                },
                "required": ["descriptor"],
            },
        },
        {
            "name": "login_match",
            "description": "Authenticate website session and verify player identity.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "refresh_match",
            "description": "Retrieve current public match projection and open backlog items.",
            "inputSchema": {
                "type": "object",
                "properties": {},
            },
        },
        {
            "name": "start_work",
            "description": "Start work on an open backlog item. Returns MVS scenarios and submitTemplate.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "backlogItemKey": {"type": "string", "description": "Backlog item key (e.g. TASK-01)"}
                },
                "required": ["backlogItemKey"],
            },
        },
        {
            "name": "submit_work",
            "description": "Submit completed work with git revision and answer payload. Strictly verifies Git invariants before submission.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "clientSubmissionId": {"type": "string", "description": "Client-generated UUID v4"},
                    "backlogItemKey": {"type": "string", "description": "Backlog item key"},
                    "gitRevision": {"type": "string", "description": "Full remote-pushed 40-character Git SHA"},
                    "answer": {"type": "object", "description": "Answer payload matching submitTemplate"},
                },
                "required": ["clientSubmissionId", "backlogItemKey", "gitRevision", "answer"],
            },
        },
        {
            "name": "report_progress",
            "description": "Append a validated progress event to the official progress NDJSON log.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "event": {"type": "object", "description": "Progress event conforming to progress-event.schema.json"}
                },
                "required": ["event"],
            },
        },
        {
            "name": "wait_for_update",
            "description": "Bounded serial sleep/poll waiting for state updates.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "timeoutMs": {"type": "integer", "description": "Timeout in milliseconds (max 55000)", "default": 5000}
                },
            },
        },
    ]


def run_mcp_stdio_server():
    server = MCPHelperServer()
    tools = get_tool_definitions()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception as exc:
            sys.stderr.write(f"Malformed JSON-RPC request: {exc}\n")
            continue

        req_id = req.get("id")
        method = req.get("method")
        params = req.get("params", {})

        if method == "initialize":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "mtm-minimal-helper", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            }
        elif method == "notifications/initialized":
            continue
        elif method == "tools/list":
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": tools},
            }
        elif method == "tools/call":
            tool_name = params.get("name")
            tool_args = params.get("arguments", {})
            try:
                if tool_name == "configure_session":
                    val = server.configure_session(tool_args.get("descriptor", {}))
                elif tool_name == "login_match":
                    val = server.login_match()
                elif tool_name == "refresh_match":
                    val = server.refresh_match()
                elif tool_name == "start_work":
                    val = server.start_work(tool_args.get("backlogItemKey", ""))
                elif tool_name == "submit_work":
                    val = server.submit_work(
                        client_submission_id=tool_args.get("clientSubmissionId", ""),
                        backlog_item_key=tool_args.get("backlogItemKey", ""),
                        git_revision=tool_args.get("gitRevision", ""),
                        answer=tool_args.get("answer", {}),
                    )
                elif tool_name == "report_progress":
                    val = server.report_progress(tool_args.get("event", {}))
                elif tool_name == "wait_for_update":
                    val = server.wait_for_update(tool_args.get("timeoutMs", 5000))
                elif tool_name == "plan_tasks":
                    val = server.plan_tasks()
                elif tool_name == "claim_task":
                    val = server.claim_task(tool_args["backlogItemKey"], tool_args["workerId"], tool_args.get("estimatedSeconds", 420))
                elif tool_name == "record_worker_result":
                    val = server.record_worker_result(tool_args["backlogItemKey"], tool_args["workerResult"])
                elif tool_name == "integration_gate":
                    val = server.integration_gate(tool_args["backlogItemKey"], tool_args.get("action", "begin"))
                elif tool_name == "reconcile_worker":
                    val = server.reconcile_worker(tool_args["backlogItemKey"], tool_args["confirmedStopped"], tool_args["reason"], tool_args.get("requeue", True))
                else:
                    raise ValueError(f"Unknown tool: {tool_name}")

                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps(val)}],
                        "isError": False,
                    },
                }
            except Exception as exc:
                sys.stderr.write(f"Tool {tool_name} error: {exc}\n")
                res = {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": [{"type": "text", "text": json.dumps({"error": str(exc)})}],
                        "isError": True,
                    },
                }
        else:
            res = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }

        sys.stdout.write(json.dumps(res) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    run_mcp_stdio_server()
