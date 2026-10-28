"""OfficialGameMasterAdapter: Production connector to the official BTC tournament platform."""
from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .base import (
    GameMasterAdapter,
    UUID_REGEX,
    validate_match_projection_schema,
    validate_match_uuid,
)


logger = logging.getLogger("mtm.game_master")

PROHIBITED_HOST_PATTERNS = [
    re.compile(r"^169\.254\."),
    re.compile(r"metadata\.google", re.IGNORECASE),
    re.compile(r"instance-data", re.IGNORECASE),
    re.compile(r"evil", re.IGNORECASE),
    re.compile(r"attacker", re.IGNORECASE),
]

PROHIBITED_PATH_SEGMENTS = [
    "/admin",
    "/grader",
    "/internal",
    "/private",
    "/superadmin",
    "/meta-data",
    "/latest/meta-data",
]


class GameMasterError(Exception):
    """Base exception for Game Master communication errors."""
    pass


class GameMasterConnectionError(GameMasterError):
    """Raised when Game Master server cannot be reached."""
    pass


class GameMasterAPIError(GameMasterError):
    """Raised when Game Master returns an HTTP error status."""

    def __init__(
        self,
        status_code: int,
        message: str,
        payload: Optional[Dict[str, Any]] = None,
        error_code: Optional[str] = None,
        request_id: Optional[str] = None,
    ):
        code_tag = f" [{error_code}]" if error_code else ""
        super().__init__(f"Game Master API Error {status_code}{code_tag}: {message}")
        self.status_code = status_code
        self.message = message
        self.payload = payload or {}
        self.error_code = error_code or ""
        self.request_id = request_id or ""


class GameMasterAuthError(GameMasterAPIError):
    """401/403: Authentication or authorization failed."""
    pass


class GameMasterConflictError(GameMasterAPIError):
    """409: Conflict or duplicate submission."""
    pass


class GameMasterIdempotencyConflictError(GameMasterConflictError):
    """409 IDEMPOTENCY_CONFLICT: Reused Idempotency-Key with differing body."""
    pass


class GameMasterAlreadySubmittedError(GameMasterConflictError):
    """409 ALREADY_SUBMITTED: Item already has an accepted submission."""
    pass


class GameMasterValidationError(GameMasterAPIError, ValueError):
    """422: Validation error (schema, invalid UUID, malformed idempotency key)."""
    pass


class GameMasterRateLimitError(GameMasterAPIError):
    """429: Rate limited by Game Master."""
    pass


class GameMasterSecurityError(GameMasterAPIError, ValueError, PermissionError):
    """SEC-01: Raised when an unauthorized admin, grader, or prohibited origin URL is requested."""

    def __init__(self, message: str, status_code: int = 403, payload: Optional[Dict[str, Any]] = None):
        super().__init__(
            status_code=status_code,
            message=message,
            payload=payload or {},
            error_code="SECURITY_VIOLATION",
        )


class MVSLockedError(GameMasterAPIError):
    """403/423: MVS is locked or not yet unlocked by start_working."""
    pass


def build_match_page_url(match_page_url: str, match_id: str) -> str:
    """Constructs specific match page URL <origin>/match/<matchId> if not already present."""
    if not match_page_url:
        return f"/match/{match_id}" if match_id else "/match"
    if not match_id:
        return match_page_url

    parsed = urllib.parse.urlparse(match_page_url)
    origin = f"{parsed.scheme}://{parsed.netloc}" if parsed.scheme and parsed.netloc else ""
    path = parsed.path.rstrip("/")

    segments = [s for s in path.split("/") if s]
    # Check if match_id is already present in path segments
    if any(s.upper() == match_id.upper() for s in segments):
        return match_page_url

    if path == "/match" or path.endswith("/match"):
        specific_path = f"{path}/{match_id}"
    elif not path or path == "/":
        specific_path = f"/match/{match_id}"
    else:
        specific_path = f"{path}/match/{match_id}"

    query_part = f"?{parsed.query}" if parsed.query else ""
    return f"{origin}{specific_path}{query_part}" if origin else f"{specific_path}{query_part}"


class OfficialGameMasterAdapter(GameMasterAdapter):
    """Production HTTP REST connector for the official BTC tournament platform.

    Strictly conforms to the official Digital Cuder wire contract:
    - Base URL: Defaults to /api/v1 (strips redundant prefixes)
    - Start Work: POST /api/v1/matches/{MATCH_UUID}/backlogs/start
    - Submit Work: POST /api/v1/matches/{MATCH_UUID}/submissions with Idempotency-Key header
    - Prohibits forbidden X-Team-ID header
    - Match UUID format validation (API-03)
    - Structured error envelope parsing (error.code, X-Request-Id)
    - Security URL whitelist blocking unauthorized admin/grader URLs (SEC-01)
    """

    def __init__(
        self,
        api_base_url: Optional[str] = None,
        api_key: Optional[str] = None,
        team_id: Optional[str] = None,
        timeout: float = 15.0,
        max_retries: int = 1,
        base_url: Optional[str] = None,
        auth_token: Optional[str] = None,
        match_uuid: Optional[str] = None,
    ):
        base = (
            api_base_url
            or base_url
            or os.environ.get("MTM_GM_URL")
            or os.environ.get("MTM_GM_STAGING_URL")
            or "http://localhost:8000/api/v1"
        ).rstrip("/")
        self.api_base_url = base
        self.api_key = (
            api_key
            or auth_token
            or os.environ.get("MTM_GM_API_KEY")
            or os.environ.get("MTM_GM_TOKEN")
            or ""
        )
        # team_id retained strictly for client-side logging; never transmitted as X-Team-ID header
        self.team_id = team_id or os.environ.get("MTM_TEAM_ID", "")
        self.timeout = timeout
        self.max_retries = max_retries
        self.match_uuid = match_uuid or os.environ.get("MTM_MATCH_UUID", "")
        self.session_cookies: Dict[str, str] = {}
        self.username: Optional[str] = None
        self.password: Optional[str] = None
        self.match_page_url: Optional[str] = None

        # Pre-validate base URL for SEC-01 security compliance
        self._validate_url_security(self.api_base_url)

    def _validate_url_security(self, url: str) -> None:
        """Enforces SEC-01: Prohibits requests to unauthorized admin, grader, or external URLs."""
        parsed = urllib.parse.urlparse(url)
        path = parsed.path.lower()
        hostname = (parsed.hostname or "").lower()

        # Check prohibited host patterns
        for pattern in PROHIBITED_HOST_PATTERNS:
            if pattern.search(hostname):
                raise GameMasterSecurityError(
                    f"SEC-01: Prohibited host '{hostname}' blocked before network request."
                )

        # Check prohibited path segments
        for seg in PROHIBITED_PATH_SEGMENTS:
            if seg in path:
                raise GameMasterSecurityError(
                    f"SEC-01: Prohibited endpoint access to '{parsed.path}' blocked before network request."
                )

        if parsed.scheme and parsed.scheme not in ("http", "https"):
            raise GameMasterSecurityError(f"SEC-01: Prohibited URL scheme '{parsed.scheme}'.")

    def validate_url(self, url: Optional[str] = None) -> None:
        """Explicit validator for SEC-01 security checks."""
        target = url or self.api_base_url
        self._validate_url_security(target)

    def _build_url(self, path: str) -> str:
        """Builds normalized URL ensuring base ends with /api/v1 for match wire endpoints,
        and stripping redundant /api/v1 prefixes."""
        if path.startswith("http://") or path.startswith("https://"):
            self._validate_url_security(path)
            return path

        base = self.api_base_url.rstrip("/")
        clean_path = "/" + path.lstrip("/")

        if clean_path.startswith("/api/v1/"):
            if base.endswith("/api/v1"):
                base = base[:-7].rstrip("/")
            url = f"{base}{clean_path}"
        elif clean_path.startswith("/matches/"):
            if not base.endswith("/api/v1"):
                url = f"{base}/api/v1{clean_path}"
            else:
                url = f"{base}{clean_path}"
        else:
            url = f"{base}{clean_path}"

        self._validate_url_security(url)
        return url

    def _extract_nextjs_projection(self, resp_body: str) -> Optional[Dict[str, Any]]:
        """Extract embedded Next.js page state from spectator HTML (<script id="__NEXT_DATA__"> or initialProjection)."""
        m = re.search(r'<script\s+id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp_body, re.DOTALL)
        if m:
            try:
                next_data = json.loads(m.group(1))
                page_props = next_data.get("props", {}).get("pageProps", {})
                match_obj = page_props.get("match") or page_props.get("matchData") or page_props.get("state") or {}
                team_obj = page_props.get("team") or {}
                repo_obj = page_props.get("repository") or page_props.get("assignment") or {}
                items = match_obj.get("backlogItems") or page_props.get("backlogItems") or []
                wave_timestamps = match_obj.get("waveTimestamps") or page_props.get("waveTimestamps") or {}

                parsed = {
                    "matchUuid": match_obj.get("matchUuid") or match_obj.get("id") or page_props.get("matchUuid") or page_props.get("id"),
                    "matchCode": match_obj.get("matchCode") or page_props.get("matchCode") or next_data.get("query", {}).get("matchId") or next_data.get("query", {}).get("matchCode"),
                    "status": match_obj.get("status") or page_props.get("status") or "IN_PROGRESS",
                    "backlogItems": items if isinstance(items, list) else [],
                    "waveTimestamps": wave_timestamps,
                    "team": team_obj,
                    "teamId": team_obj.get("id") or team_obj.get("teamId") or page_props.get("teamId"),
                    "teamName": team_obj.get("name") or team_obj.get("teamName") or page_props.get("teamName"),
                    "repository": repo_obj,
                    "pageProps": page_props,
                }
                for k, v in page_props.items():
                    parsed.setdefault(k, v)
                for k, v in match_obj.items():
                    parsed.setdefault(k, v)
                return parsed
            except Exception:
                return None

        if "initialProjection" in resp_body:
            try:
                idx = resp_body.find("initialProjection")
                start_obj = resp_body.find("{", idx)
                if start_obj != -1:
                    cleaned = resp_body[start_obj:].replace('\\\\\"', '\"').replace('\\\"', '\"')
                    proj_obj, _ = json.JSONDecoder().raw_decode(cleaned)
                    match_obj = proj_obj.get("match") or {}
                    team_obj = proj_obj.get("team") or {}
                    repo_obj = proj_obj.get("repo") or proj_obj.get("repository") or {}
                    items = proj_obj.get("backlog") or proj_obj.get("backlogItems") or []
                    parsed = {
                        "matchUuid": match_obj.get("id") or match_obj.get("matchUuid"),
                        "matchCode": match_obj.get("publicId") or match_obj.get("matchCode"),
                        "status": match_obj.get("status") or "IN_PROGRESS",
                        "backlogItems": items if isinstance(items, list) else [],
                        "team": team_obj,
                        "teamId": team_obj.get("id") or team_obj.get("code"),
                        "teamName": team_obj.get("name"),
                        "repository": repo_obj,
                        "projectContext": proj_obj.get("projectContext"),
                        "api": proj_obj.get("api"),
                    }
                    for k, v in proj_obj.items():
                        parsed.setdefault(k, v)
                    for k, v in match_obj.items():
                        parsed.setdefault(k, v)
                    return parsed
            except Exception:
                return None
        return None

    def _request(
        self,
        method: str,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        retries: Optional[int] = None,
        headers: Optional[Dict[str, str]] = None,
        raw_body: Optional[bytes] = None,
    ) -> Any:
        url = self._build_url(path)
        req_headers = {
            "Accept": "application/json",
            "User-Agent": "MTM-Codex-Agent/1.0.0",
        }
        if self.api_key:
            req_headers["Authorization"] = f"Bearer {self.api_key}"

        # Attach session cookies if available
        if self.session_cookies:
            cookie_hdr = "; ".join(f"{k}={v}" for k, v in self.session_cookies.items())
            if "Cookie" in req_headers:
                req_headers["Cookie"] = f"{req_headers['Cookie']}; {cookie_hdr}"
            else:
                req_headers["Cookie"] = cookie_hdr

        # Prohibit forbidden X-Team-ID header (Digital Cuder derives team strictly from Bearer token)
        if headers:
            for k, v in headers.items():
                if k.lower() == "x-team-id":
                    continue
                req_headers[k] = v

        encoded_data = None
        if raw_body is not None:
            req_headers["Content-Type"] = "application/json"
            encoded_data = raw_body
        elif data is not None:
            req_headers["Content-Type"] = "application/json"
            encoded_data = json.dumps(data).encode("utf-8")

        max_attempts = retries if retries is not None else self.max_retries
        last_error: Optional[Exception] = None

        for attempt in range(1, max_attempts + 1):
            try:
                req = urllib.request.Request(
                    url=url,
                    data=encoded_data,
                    headers=req_headers,
                    method=method.upper(),
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    status_code = getattr(resp, "status", getattr(resp, "code", 200))

                    # Parse and store cookies from response
                    if hasattr(resp, "headers") and resp.headers:
                        cookie_hdrs = []
                        if hasattr(resp.headers, "get_all"):
                            cookie_hdrs = resp.headers.get_all("Set-Cookie") or []
                        elif hasattr(resp.headers, "getlist"):
                            cookie_hdrs = resp.headers.getlist("Set-Cookie") or []
                        if not cookie_hdrs:
                            single_sc = resp.headers.get("Set-Cookie")
                            if single_sc:
                                cookie_hdrs = [single_sc]
                        for c in cookie_hdrs:
                            c_part = c.split(";")[0]
                            if "=" in c_part:
                                k, v = c_part.split("=", 1)
                                self.session_cookies[k.strip()] = v.strip()

                    resp_body = resp.read().decode("utf-8")
                    if not resp_body.strip():
                        return {}
                    try:
                        parsed = json.loads(resp_body)
                    except json.JSONDecodeError:
                        # Only allow HTML spectator extraction on /match/ page requests
                        parsed = None
                        if "/match" in url:
                            parsed = self._extract_nextjs_projection(resp_body)
                        if parsed is None or not isinstance(parsed, dict):
                            raise GameMasterError(f"Received non-JSON response from {url}")

                    if isinstance(parsed, dict):
                        if "replay" not in parsed and status_code in (200, 201):
                            parsed["replay"] = (status_code == 200)
                        if hasattr(resp, "headers") and resp.headers:
                            if resp.headers.get("Idempotent-Replay", "").lower() == "true":
                                parsed["idempotent_replay"] = True
                    return parsed

            except urllib.error.HTTPError as e:
                # Capture Set-Cookie headers from HTTP error responses
                if hasattr(e, "headers") and e.headers:
                    cookie_hdrs = []
                    if hasattr(e.headers, "get_all"):
                        cookie_hdrs = e.headers.get_all("Set-Cookie") or []
                    elif hasattr(e.headers, "getlist"):
                        cookie_hdrs = e.headers.getlist("Set-Cookie") or []
                    if not cookie_hdrs:
                        single_sc = e.headers.get("Set-Cookie")
                        if single_sc:
                            cookie_hdrs = [single_sc]
                    for c in cookie_hdrs:
                        c_part = c.split(";")[0]
                        if "=" in c_part:
                            k, v = c_part.split("=", 1)
                            self.session_cookies[k.strip()] = v.strip()

                error_body = e.read().decode("utf-8", errors="replace")
                parsed_error = {}
                try:
                    parsed_error = json.loads(error_body)
                except Exception:
                    pass

                request_id = ""
                if hasattr(e, "headers") and e.headers:
                    request_id = e.headers.get("X-Request-Id") or e.headers.get("x-request-id") or ""

                error_code = ""
                msg = ""
                if isinstance(parsed_error, dict):
                    err_obj = parsed_error.get("error")
                    if isinstance(err_obj, dict):
                        error_code = err_obj.get("code", "")
                        msg = err_obj.get("message", "")
                        if not request_id:
                            request_id = err_obj.get("requestId", "")
                    elif isinstance(err_obj, str):
                        msg = err_obj

                    if not msg:
                        msg = parsed_error.get("detail", "") or parsed_error.get("message", "")

                if not msg:
                    msg = str(e)

                # HTTP error classification & envelope mapping
                if e.code in (401, 403):
                    raise GameMasterAuthError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                elif e.code == 409:
                    if error_code == "IDEMPOTENCY_CONFLICT":
                        raise GameMasterIdempotencyConflictError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                    elif error_code == "ALREADY_SUBMITTED":
                        raise GameMasterAlreadySubmittedError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                    else:
                        raise GameMasterConflictError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                elif e.code == 422:
                    raise GameMasterValidationError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                elif e.code == 429:
                    retry_after = None
                    if hasattr(e, "headers") and e.headers:
                        hdr = e.headers.get("Retry-After") or e.headers.get("retry-after")
                        if hdr:
                            try:
                                retry_after = float(hdr)
                            except (ValueError, TypeError):
                                retry_after = None
                    sleep_time = retry_after if retry_after is not None else (0.5 * (2 ** (attempt - 1)))
                    if attempt < max_attempts:
                        time.sleep(sleep_time)
                        continue
                    raise GameMasterRateLimitError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)
                elif 400 <= e.code < 500:
                    raise GameMasterAPIError(e.code, msg, parsed_error, error_code=error_code, request_id=request_id)

                last_error = GameMasterAPIError(
                    status_code=e.code,
                    message=f"HTTP {e.code}: {e.reason}",
                    payload=parsed_error,
                    error_code=error_code,
                    request_id=request_id,
                )

            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = GameMasterConnectionError(f"Connection to {url} failed: {e}")

            except GameMasterSecurityError:
                raise

            except Exception as e:
                last_error = GameMasterError(f"Unexpected request failure: {e}")

            if attempt < max_attempts:
                # Do not retry unsafe POST operations unless explicitly configured
                if method.upper() == "POST" and max_attempts == 1:
                    break
                backoff = 0.5 * (2 ** (attempt - 1))
                time.sleep(backoff)

        raise last_error or GameMasterError(f"Request to {url} failed after {max_attempts} attempts")

    def get_match_projection(self, match_id_or_code: Optional[str] = None) -> Dict[str, Any]:
        """Fetch public match projection and open backlog items from Game Master.

        Authoritative endpoint: /match/<matchId> per Technical Guide Step 9.
        Secondary fallback: /api/v1/matches/<matchUuid>.
        Strictly validates schema requiring matchUuid (or id), matchCode, and backlogItems list.
        Fails closed on authentication, validation, or connection failure.
        """
        ident = match_id_or_code or self.match_uuid
        if not ident:
            return self.get_match_state()

        clean_ident = str(ident).strip()
        last_error = None

        # 1. Authoritative endpoint: /match/<matchId>
        try:
            res = self._request("GET", f"/match/{clean_ident}")
            if isinstance(res, dict):
                return validate_match_projection_schema(res)
        except GameMasterAuthError:
            raise
        except Exception as e:
            last_error = e

        # 2. If endpoint failed, attempt session auto-login if credentials exist
        if last_error and self.username and self.password and not self.session_cookies:
            try:
                self.login_session(
                    username=self.username,
                    password=self.password,
                    match_page_url=self.match_page_url or f"{self.api_base_url}/match/{clean_ident}",
                    match_id=clean_ident,
                )
                res = self._request("GET", f"/match/{clean_ident}")
                if isinstance(res, dict):
                    return validate_match_projection_schema(res)
            except Exception:
                pass

        # 3. Official API fallback: /api/v1/matches/<matchUuid>
        try:
            res = self._request("GET", f"/api/v1/matches/{clean_ident}")
            if isinstance(res, dict):
                return validate_match_projection_schema(res)
        except GameMasterAuthError:
            raise
        except Exception as e:
            last_error = e

        # 4. If endpoints failed, raise rather than returning unverified default
        if last_error:
            if isinstance(last_error, (GameMasterError, ValueError)):
                raise last_error
            raise GameMasterError(f"Could not retrieve match projection for {clean_ident}: {last_error}") from last_error
        raise GameMasterError(f"Could not retrieve match projection for {clean_ident}")

    def login_session(
        self,
        username: str,
        password: str,
        match_page_url: str,
        allowed_origins: Optional[List[str]] = None,
        match_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Authenticate genuine browser/cookie session for PREPARE_GAME.

        1. Validates match_page_url against SEC-01 and allowed_origins whitelist.
        2. Authenticates against /login or /api/v1/auth/login with username/password, capturing cookies.
        3. Fetches match page /match/<matchId> using session cookies.
        4. Validates match projection schema (matchUuid, matchCode, backlogItems).
        5. Validates projection matches requested matchCode/matchId.
        6. Fails closed: raises GameMasterError/RuntimeError if authentication fails.

        Returns:
            Dict containing {"status": "AUTHENTICATED", "session_token": str, "projection": Dict[str, Any], "cookies": Dict[str, str]}
        """
        if not username or not isinstance(username, str) or not username.strip():
            raise GameMasterAuthError(401, "Username is required for session login")
        if not password or not isinstance(password, str) or not password.strip():
            raise GameMasterAuthError(401, "Password is required for session login")
        if not match_page_url or not isinstance(match_page_url, str) or not match_page_url.strip():
            raise GameMasterValidationError(422, "match_page_url is required for session login")

        # 1. Target URL construction & SEC-01 / allowed_origins validation
        target_url = match_page_url
        if match_id:
            target_url = build_match_page_url(match_page_url, match_id)

        self._validate_url_security(match_page_url)
        if target_url != match_page_url:
            self._validate_url_security(target_url)
        parsed_page = urllib.parse.urlparse(match_page_url)
        page_origin = f"{parsed_page.scheme}://{parsed_page.netloc}".rstrip("/")

        if allowed_origins:
            allowed_normalized = []
            for o in allowed_origins:
                o_str = str(o).strip().lower().rstrip("/")
                if "://" in o_str:
                    allowed_normalized.append(urllib.parse.urlparse(o_str).netloc.lower())
                    allowed_normalized.append(o_str)
                else:
                    allowed_normalized.append(o_str)

            page_netloc = parsed_page.netloc.lower()
            if page_netloc not in allowed_normalized and page_origin.lower() not in allowed_normalized:
                raise GameMasterSecurityError(
                    f"SEC-01: matchPageUrl origin '{parsed_page.netloc}' not in allowedMatchOrigins: {allowed_origins}"
                )

        # 2. Authenticate strictly against /api/v1/auth/login per Digital Cuder contract
        login_url = f"{page_origin}/api/v1/auth/login"
        login_data = {
            "username": username.strip(),
            "password": password.strip(),
        }

        try:
            res = self._request("POST", login_url, data=login_data, retries=1, headers={"Origin": page_origin})
            login_response = res if isinstance(res, dict) else {}
        except Exception as e:
            if isinstance(e, GameMasterError):
                raise
            raise GameMasterAuthError(401, f"Authentication failed: {e}") from e

        # Extract session token from cookies or response body
        session_token = ""
        if "session_token" in self.session_cookies:
            session_token = self.session_cookies["session_token"]
        elif "token" in self.session_cookies:
            session_token = self.session_cookies["token"]
        elif isinstance(login_response, dict):
            session_token = (
                login_response.get("token")
                or login_response.get("session_token")
                or login_response.get("access_token")
                or ""
            )
            if session_token and "session_token" not in self.session_cookies:
                self.session_cookies["session_token"] = session_token

        # Check that session cookies exist or token was returned
        if not self.session_cookies and not session_token:
            raise GameMasterAuthError(
                401,
                "Authentication failed closed: no session cookies or token established by Game Master.",
            )

        # 3. Fetch match page using session cookies
        projection_raw = None
        try:
            projection_raw = self._request("GET", target_url, retries=1)
        except Exception as e:
            # Fallback: extract match identifier and call get_match_projection
            eff_match_id = match_id
            if not eff_match_id:
                parsed_target = urllib.parse.urlparse(target_url)
                if "/match/" in parsed_target.path:
                    eff_match_id = parsed_target.path.split("/match/")[-1].strip("/")
                elif "/match/" in parsed_page.path:
                    eff_match_id = parsed_page.path.split("/match/")[-1].strip("/")
            if eff_match_id:
                projection_raw = self.get_match_projection(eff_match_id)
            else:
                if isinstance(e, GameMasterError):
                    raise
                raise GameMasterError(f"Failed to fetch match page at {target_url}: {e}") from e

        # 4. Strictly validate match projection schema
        validated_projection = validate_match_projection_schema(projection_raw)

        # 5. Confirm matchCode / matchId assignment
        if match_id:
            proj_code = validated_projection.get("matchCode")
            proj_uuid = validated_projection.get("matchUuid") or validated_projection.get("id")
            code_match = bool(proj_code and str(proj_code).upper() == str(match_id).upper())
            uuid_match = bool(proj_uuid and str(proj_uuid).lower() == str(match_id).lower())
            if not (code_match or uuid_match):
                raise GameMasterValidationError(
                    422,
                    f"Match assignment verification failed: projection returned matchCode='{proj_code}' "
                    f"(uuid='{proj_uuid}'), expected '{match_id}'."
                )

        return {
            "status": "AUTHENTICATED",
            "session_token": session_token,
            "projection": validated_projection,
            "cookies": dict(self.session_cookies),
        }




    def start_working(
        self,
        match_uuid_or_task_id: str,
        backlog_item_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Signal to Game Master that work has started on task.

        Official Digital Cuder Contract:
        POST /api/v1/matches/{MATCH_UUID}/backlogs/start
        Payload: {"backlogItemKey": "<KEY>"}

        Returns:
            Dict with parsed MVS scenarios, submitTemplate, files, replay boolean.
            Handles 201 Created (replay: false) and 200 OK (replay: true).
        """
        if backlog_item_key is not None:
            match_uuid = match_uuid_or_task_id
            item_key = backlog_item_key
        elif self.match_uuid:
            match_uuid = self.match_uuid
            item_key = match_uuid_or_task_id
        else:
            validate_match_uuid(match_uuid_or_task_id)
            raise ValueError("backlog_item_key is required when calling start_working")

        validate_match_uuid(match_uuid)

        clean_item_key = item_key.strip() if isinstance(item_key, str) else item_key
        path = f"/api/v1/matches/{match_uuid}/backlogs/start"
        payload = {"backlogItemKey": clean_item_key}
        res = self._request("POST", path, data=payload)
        if isinstance(res, dict):
            res.setdefault("backlogId", clean_item_key)
            res.setdefault("submitTemplate", None)
            res.setdefault("minimumVerificationScenarios", None)
            res.setdefault("files", {})
            res.setdefault("replay", False)
            if isinstance(res.get("submitTemplate"), str):
                try:
                    res["submitTemplate"] = json.loads(res["submitTemplate"])
                except Exception:
                    pass
            if isinstance(res.get("minimumVerificationScenarios"), str):
                try:
                    res["minimumVerificationScenarios"] = json.loads(res["minimumVerificationScenarios"])
                except Exception:
                    pass
        return res

    start_work = start_working

    def submit_work(
        self,
        match_uuid: str,
        client_submission_id: str,
        idempotency_key: str,
        backlog_item_key: str,
        git_revision: str,
        answer: Dict[str, Any],
        artifact_urls: Optional[List[str]] = None,
        serialized_body_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Submit completed solution revision for scoring under Digital Cuder wire contract.

        POST /api/v1/matches/{MATCH_UUID}/submissions
        Headers:
            Idempotency-Key: <UUID or idempotency string>
            Authorization: Bearer <api_key>
        Body:
            clientSubmissionId: <UUID>
            backlogItemKey: <KEY>
            gitRevision: <40-64 hex SHA>
            answer: <dict matching submitTemplate>
            artifactUrls: <optional list>

        Returns:
            Dict containing server receipt:
            submission: {id, matchId, teamId, backlogItemKey, status: "ACCEPTED", scoreStatus: "PENDING", ...}
        """
        validate_match_uuid(match_uuid)

        if not idempotency_key or not isinstance(idempotency_key, str) or not re.match(r"^[A-Za-z0-9._~-]{8,128}$", idempotency_key):
            raise ValueError(f"Invalid idempotency_key '{idempotency_key}': must match /^[A-Za-z0-9._~-]{{8,128}}$/")

        if not client_submission_id or not isinstance(client_submission_id, str):
            raise ValueError("client_submission_id must be a non-empty string")
        if not UUID_REGEX.match(client_submission_id.strip()):
            raise ValueError(f"client_submission_id '{client_submission_id}' must be a 36-char UUID")

        if not git_revision or not isinstance(git_revision, str) or not git_revision.strip():
            raise ValueError("git_revision must be a non-empty commit hash string")

        if not isinstance(answer, dict):
            raise ValueError(f"answer must be a dict matching submitTemplate, got {type(answer).__name__}")

        if artifact_urls is not None:
            if not isinstance(artifact_urls, list) or len(artifact_urls) > 20:
                raise ValueError("artifact_urls must be a list of at most 20 URLs")
            for u in artifact_urls:
                if not isinstance(u, str) or not u.startswith("https://") or len(u) > 2048:
                    raise ValueError(f"Invalid artifact URL: '{u}' (must be https:// and <= 2048 chars)")

        clean_item_key = backlog_item_key.strip() if isinstance(backlog_item_key, str) else backlog_item_key
        clean_git_rev = git_revision.strip()
        headers = {
            "Idempotency-Key": idempotency_key,
        }

        payload = None
        raw_body = None
        if serialized_body_bytes is not None:
            raw_body = serialized_body_bytes
        else:
            payload = {
                "clientSubmissionId": client_submission_id.strip(),
                "backlogItemKey": clean_item_key,
                "gitRevision": clean_git_rev,
                "answer": answer,
            }
            if artifact_urls is not None:
                payload["artifactUrls"] = artifact_urls

        path = f"/api/v1/matches/{match_uuid}/submissions"
        res = self._request("POST", path, data=payload, headers=headers, raw_body=raw_body, retries=1)

        # Ensure standard receipt fields
        if isinstance(res, dict) and "submission" in res:
            sub = res["submission"]
            if isinstance(sub, dict):
                sub.setdefault("status", "ACCEPTED")
                sub.setdefault("scoreStatus", "PENDING")
                sub.setdefault("backlogItemKey", clean_item_key)
        return res

    def submit(
        self,
        task_id: str,
        git_revision: str,
        evidence: Optional[List[str]] = None,
        idempotency_key: Optional[str] = None,
        match_uuid: Optional[str] = None,
        answer: Optional[Dict[str, Any]] = None,
        client_submission_id: Optional[str] = None,
        artifact_urls: Optional[List[str]] = None,
        serialized_body_bytes: Optional[bytes] = None,
    ) -> Dict[str, Any]:
        """Submit completed solution revision for scoring.

        If match_uuid (or self.match_uuid) and answer are provided, delegates to submit_work
        for official Digital Cuder wire contract. Otherwise preserves legacy endpoint
        POST /tasks/{id}/submit for backward test compatibility.
        """
        target_match = match_uuid or self.match_uuid
        if not target_match or answer is None:
            raise ValueError(
                "Digital Cuder wire contract requires 'match_uuid' and 'answer' dictionary. "
                "Legacy endpoint POST /tasks/{id}/submit has been eliminated."
            )

        import uuid
        sub_id = client_submission_id or str(uuid.uuid4())
        idemp = idempotency_key or sub_id
        return self.submit_work(
            match_uuid=target_match,
            client_submission_id=sub_id,
            idempotency_key=idemp,
            backlog_item_key=task_id,
            git_revision=git_revision,
            answer=answer,
            artifact_urls=artifact_urls,
            serialized_body_bytes=serialized_body_bytes,
        )


__all__ = [
    "GameMasterError",
    "GameMasterConnectionError",
    "GameMasterAPIError",
    "GameMasterAuthError",
    "GameMasterConflictError",
    "GameMasterIdempotencyConflictError",
    "GameMasterAlreadySubmittedError",
    "GameMasterValidationError",
    "GameMasterRateLimitError",
    "GameMasterSecurityError",
    "MVSLockedError",
    "OfficialGameMasterAdapter",
    "validate_match_projection_schema",
]

