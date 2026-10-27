#!/usr/bin/env python3
"""
check-distribution.py: Read-only distribution verification script.
Verifies registrations/payload parity, identity/version, schema/manifest,
agent skills, package imports, and executes an active MCP Server smoke test.
Outputs "READY" and exits 0 on success; non-zero on failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


def fail(msg: str) -> None:
    sys.stderr.write(f"DISTRIBUTION_CHECK_FAILED: {msg}\n")
    sys.exit(1)


def verify_distribution(base_dir: Path) -> None:
    # Resolve pack directory and workspace directory (R3 layout parity)
    if (base_dir / "team-sdlc-pack" / "team-runtime.json").exists():
        ws_dir = base_dir
        pack_dir = base_dir / "team-sdlc-pack"
    elif (base_dir / "team-runtime.json").exists():
        pack_dir = base_dir
        ws_dir = base_dir
    elif base_dir.name == "team-sdlc-pack":
        pack_dir = base_dir
        ws_dir = base_dir.parent
    else:
        pack_dir = base_dir
        ws_dir = base_dir

    # 1. team-runtime.json
    runtime_path = pack_dir / "team-runtime.json"
    if not runtime_path.exists():
        fail(f"Missing team-runtime.json at {runtime_path}")
    try:
        runtime = json.loads(runtime_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid JSON in team-runtime.json: {exc}")

    if runtime.get("schemaVersion") != 1:
        fail(f"team-runtime.json schemaVersion must be 1, got {runtime.get('schemaVersion')}")
    if runtime.get("kind") != "mtm-team-runtime":
        fail(f"team-runtime.json kind must be 'mtm-team-runtime', got '{runtime.get('kind')}'")
    if runtime.get("name") != "team-sdlc-pack":
        fail(f"team-runtime.json name must be 'team-sdlc-pack', got '{runtime.get('name')}'")
    if runtime.get("protocolVersion") != 4:
        fail(f"team-runtime.json protocolVersion must be 4, got {runtime.get('protocolVersion')}")
    if runtime.get("provider") != "codex":
        fail(f"team-runtime.json provider must be 'codex', got '{runtime.get('provider')}'")
    if not isinstance(runtime.get("entrypoint"), dict) or runtime["entrypoint"].get("rootAgentType") != "team_orchestrator":
        fail("team-runtime.json entrypoint.rootAgentType must be 'team_orchestrator'")

    # 1b. Parity check with .mtm/team-runtime.json
    mtm_runtime = ws_dir / ".mtm" / "team-runtime.json"
    if mtm_runtime.exists():
        try:
            mtm_data = json.loads(mtm_runtime.read_text(encoding="utf-8"))
            if mtm_data.get("version") != runtime.get("version"):
                fail(f"Manifest version mismatch between team-sdlc-pack and .mtm/ ({runtime.get('version')} vs {mtm_data.get('version')})")
        except Exception as exc:
            fail(f"Invalid JSON in .mtm/team-runtime.json: {exc}")

    # 2. team-pack-info.json
    info_path = pack_dir / "team-pack-info.json"
    if not info_path.exists():
        fail(f"Missing team-pack-info.json at {info_path}")
    try:
        info = json.loads(info_path.read_text(encoding="utf-8"))
    except Exception as exc:
        fail(f"Invalid JSON in team-pack-info.json: {exc}")

    required_keys = {"schemaVersion", "version", "teamId", "teamName"}
    info_keys = set(info.keys())
    if info_keys != required_keys:
        fail(f"team-pack-info.json must contain exactly 4 keys ({required_keys}), got: {info_keys}")
    if info.get("schemaVersion") != 1:
        fail("team-pack-info.json schemaVersion must be 1")
    if info.get("version") != runtime.get("version"):
        fail(f"Version mismatch: team-runtime ({runtime.get('version')}) vs team-pack-info ({info.get('version')})")
    if not isinstance(info.get("teamId"), str) or not info["teamId"].strip():
        fail("Invalid teamId in team-pack-info.json")
    if not isinstance(info.get("teamName"), str) or not info["teamName"].strip():
        fail("Invalid teamName in team-pack-info.json")

    # 2b. Parity check with .mtm/team-pack-info.json
    mtm_info = ws_dir / ".mtm" / "team-pack-info.json"
    if mtm_info.exists():
        try:
            mtm_info_data = json.loads(mtm_info.read_text(encoding="utf-8"))
            if mtm_info_data.get("teamId") != info.get("teamId"):
                fail("teamId mismatch between team-sdlc-pack and .mtm/")
        except Exception as exc:
            fail(f"Invalid JSON in .mtm/team-pack-info.json: {exc}")

    # 2c. Root package.json check (R3)
    if ws_dir != pack_dir and (ws_dir / "team-sdlc-pack").exists():
        ws_pkg = ws_dir / "package.json"
        if not ws_pkg.exists():
            fail(f"Missing root package.json in TeamWorkspace at {ws_pkg}")
        try:
            json.loads(ws_pkg.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"Invalid root package.json at {ws_pkg}: {exc}")

    # 3. Static Provider Profiles
    required_profiles = [
        "team_orchestrator",
        "team_repo_scout",
        "team_feature_worker",
        "team_quality_checker",
    ]

    for prof in required_profiles:
        candidates = [
            pack_dir / "agents" / f"{prof}.toml",
            pack_dir / "agents" / f"{prof.replace('_', '-')}.toml",
            pack_dir / "profiles" / "agents" / f"{prof}.toml",
            pack_dir / "profiles" / "agents" / f"{prof.replace('_', '-')}.toml",
            pack_dir / ".codex" / "agents" / f"{prof}.toml",
            ws_dir / ".codex" / "agents" / f"{prof}.toml",
            ws_dir / "agents" / f"{prof}.toml",
        ]
        found = next((p for p in candidates if p.exists()), None)
        if not found:
            fail(f"Missing provider profile for '{prof}'. Checked: {[str(c) for c in candidates]}")

        content = found.read_text(encoding="utf-8")
        match = re.search(r'^name\s*=\s*["\']([^"\']+)["\']', content, re.MULTILINE)
        if not match or match.group(1) != prof:
            fail(f"Profile at {found} must declare name = '{prof}'")

    # 4. Progress Event Schema
    schema_path = pack_dir / "schemas" / "progress-event.schema.json"
    if not schema_path.exists():
        schema_path = ws_dir / "schemas" / "progress-event.schema.json"
    if schema_path.exists():
        try:
            json.loads(schema_path.read_text(encoding="utf-8"))
        except Exception as exc:
            fail(f"Invalid schema JSON at {schema_path}: {exc}")

    # 5. Root Skill Verification (team-orchestrator/SKILL.md)
    skill_candidates = [
        pack_dir / "skills" / "team-orchestrator" / "SKILL.md",
        pack_dir / ".agents" / "skills" / "team-orchestrator" / "SKILL.md",
        ws_dir / ".agents" / "skills" / "team-orchestrator" / "SKILL.md",
        ws_dir / "skills" / "team-orchestrator" / "SKILL.md",
    ]
    skill_found = next((s for s in skill_candidates if s.exists() and s.stat().st_size > 50), None)
    if not skill_found:
        fail(f"Missing team-orchestrator/SKILL.md. Checked: {[str(s) for s in skill_candidates]}")

    # 6. Runtime Package Import Verification (P0.3)
    src_dir = pack_dir / "src"
    if not src_dir.exists():
        src_dir = ws_dir / "src"
    if src_dir.exists():
        test_import_code = "import mtm_core; import mtm_competition; import mtm_knowledge; import runner.telemetry; print('OK')"
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{pack_dir}{os.pathsep}{ws_dir}{os.pathsep}{env.get('PYTHONPATH', '')}"
        res = subprocess.run([sys.executable, "-c", test_import_code], capture_output=True, text=True, env=env)
        if res.returncode != 0:
            fail(f"Runtime packages in src/ failed import test:\n{res.stderr}")

    # 7. MCP Server Active Smoke Test (P1)
    runner_script = pack_dir / "runner" / "mcp_server.py"
    if not runner_script.exists():
        runner_script = ws_dir / "runner" / "mcp_server.py"

    if runner_script.exists():
        smoke_env = os.environ.copy()
        if src_dir.exists():
            smoke_env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{pack_dir}{os.pathsep}{ws_dir}{os.pathsep}{smoke_env.get('PYTHONPATH', '')}"

        proc = subprocess.Popen(
            [sys.executable, str(runner_script)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=smoke_env,
            cwd=str(pack_dir),
        )

        try:
            # 7a. Send initialize
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize"}) + "\n")
            proc.stdin.flush()
            init_res_line = proc.stdout.readline()
            if not init_res_line:
                err_out = proc.stderr.read()
                fail(f"MCP Helper failed to respond to initialize request:\n{err_out}")
            init_res = json.loads(init_res_line)
            if init_res.get("result", {}).get("serverInfo", {}).get("name") != "mtm-minimal-helper":
                fail(f"MCP serverInfo name mismatch: {init_res}")

            # 7b. Send tools/list
            proc.stdin.write(json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}) + "\n")
            proc.stdin.flush()
            tools_res_line = proc.stdout.readline()
            if not tools_res_line:
                fail("MCP Helper failed to respond to tools/list request")
            tools_res = json.loads(tools_res_line)
            tool_names = {t.get("name") for t in tools_res.get("result", {}).get("tools", [])}
            expected_tools = {
                "configure_session",
                "login_match",
                "refresh_match",
                "start_work",
                "submit_work",
                "report_progress",
                "wait_for_update",
            }
            missing_tools = expected_tools - tool_names
            if missing_tools:
                fail(f"MCP Helper missing official tools: {missing_tools}")

        finally:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

    print("Distribution check passed: ALL_VERIFIED")
    print("READY")


def main() -> None:
    parser = argparse.ArgumentParser(description="Check MTM player pack distribution")
    parser.add_argument("--team-workspace", dest="team_workspace", default=".", help="Path to team workspace")
    args = parser.parse_args()
    verify_distribution(Path(args.team_workspace).resolve())


if __name__ == "__main__":
    main()
