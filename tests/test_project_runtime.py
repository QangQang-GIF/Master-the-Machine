import os
import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from runner.project_runtime import ProjectRuntime
from runner.project_runtime import CommandResult


def project(tmp_path, env="DATABASE_URL=postgresql://dev:CHANGE_ME@localhost:5433/appdb\nAUTH_SECRET=CHANGE_ME\n"):
    root = tmp_path / "match"
    app = root / "src"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"packageManager":"pnpm@9.0.0","scripts":{"build":"next build"}}')
    (app / ".env.example").write_text(env)
    return root, app


def test_scopes_databases_and_generates_secret_only_in_memory(tmp_path):
    root, app = project(tmp_path)
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")):
        runtime = ProjectRuntime(root, "MATCH-123", "worker A")
    assert runtime.app_root == app
    assert runtime.env["DATABASE_URL"].endswith("/appdb_match_123")
    assert runtime.env["AUTH_SECRET"] != "CHANGE_ME"
    assert len(runtime.env["AUTH_SECRET"]) > 40
    assert not (app / ".env").exists()
    assert not (root / ".env").exists()


def test_worker_database_names_differ(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev@localhost:5433/appdb\nTEST_DATABASE_URL=postgresql://dev@localhost:5433/testdb\n")
    a = ProjectRuntime(root, "m-1", "worker-a")
    b = ProjectRuntime(root, "m-1", "worker-b")
    assert a.env["DATABASE_URL"] == b.env["DATABASE_URL"]
    assert a.env["TEST_DATABASE_URL"] != b.env["TEST_DATABASE_URL"]
    assert a.env["TEST_DATABASE_URL"].endswith("/testdb_m_1_worker_a")


def test_database_scoping_is_idempotent(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev@localhost:5433/appdb_match_123\n")
    runtime = ProjectRuntime(root, "match-123")
    assert runtime.env["DATABASE_URL"].endswith("/appdb_match_123")


def test_process_environment_overrides_dotenv_and_example(tmp_path, monkeypatch):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://file@localhost:5433/example\nTEST_DATABASE_URL=postgresql://file@localhost:5433/filetest\n")
    monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://process@localhost:5433/processtest")
    runtime = ProjectRuntime(root, "m1", "w1")
    assert runtime.env["DATABASE_URL"].endswith("/example_m1")
    assert runtime.env["TEST_DATABASE_URL"] == "postgresql://process@localhost:5433/processtest_m1_w1"


def test_non_loopback_database_is_rejected(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev@db.example.com/app\n")
    with pytest.raises(ValueError, match="loopback"):
        ProjectRuntime(root, "m1")


def test_placeholder_password_requires_successful_bounded_trust_probe(tmp_path):
    root, _ = project(tmp_path)
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=2, stdout="", stderr="no")) as run:
        with pytest.raises(ValueError, match="trust probe failed"):
            ProjectRuntime(root, "m1")
    assert run.call_args.kwargs["timeout"] == 3
    assert run.call_args.kwargs["shell"] is False


def test_successful_trust_probe_removes_placeholder_password(tmp_path):
    root, _ = project(tmp_path)
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")):
        runtime = ProjectRuntime(root, "m1")
    assert "CHANGE_ME" not in runtime.env["DATABASE_URL"]


def test_cwd_containment_shell_free_and_redaction(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev:supersecret@localhost:5433/db\n")
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")):
        runtime = ProjectRuntime(root, "m1")
    with pytest.raises(ValueError, match="inside match_root"):
        runtime.run(["echo", "x"], cwd=tmp_path)
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="supersecret", stderr="")) as run:
        result = runtime.run(["echo", "hello"])
    assert run.call_args.kwargs["shell"] is False
    assert "supersecret" not in result.stdout
    assert "[REDACTED]" in result.stdout


def test_run_timeout_is_structured(tmp_path):
    root, _ = project(tmp_path, "")
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")):
        runtime = ProjectRuntime(root, "m1")
    with patch("runner.project_runtime.subprocess.run", side_effect=subprocess.TimeoutExpired(["sleep", "9"], 1, output=b"partial")):
        result = runtime.run(["sleep", "9"], timeout=1)
    assert result.timed_out
    assert result.returncode == -1
    assert result.stdout == "partial"
    assert not result.ok


def test_doctor_respects_powershell_and_build_script(tmp_path):
    root, app = project(tmp_path)
    script = app / "scripts" / "doctor.ps1"
    script.parent.mkdir()
    script.write_text("Write-Output ready")
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")):
        runtime = ProjectRuntime(root, "m1")
    assert runtime.doctor_command() == ["pwsh", "-NoProfile", "-File", str(script)]
    assert runtime.build_command() == ["pnpm", "run", "build"]
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="", stderr="")) as run:
        runtime.run(runtime.doctor_command())
    assert str(app / "node_modules" / ".bin") in run.call_args.kwargs["env"]["PATH"].split(os.pathsep)


def test_discovers_match_env_require_doctor_with_app_source_dir(tmp_path):
    root, app = project(tmp_path, "")
    doctor = root / "env-require" / "doctor.ps1"
    doctor.parent.mkdir()
    doctor.write_text("param([string]$SourceDir)")
    runtime = ProjectRuntime(root, "m1")
    assert runtime.doctor_command() == ["pwsh", "-NoProfile", "-File", str(doctor), "-SourceDir", str(app)]
    assert runtime.doctor_cwd() == root


def test_redaction_preserves_public_path_and_username(tmp_path):
    root, _ = project(tmp_path, "API_BASE=https://public.example/path\nDATABASE_URL=postgresql://publicuser:validpw@localhost:5433/app\n")
    runtime = ProjectRuntime(root, "m1")
    output = runtime._redact("https://public.example/path publicuser validpw")
    assert "https://public.example/path" in output
    assert "publicuser" in output
    assert "validpw" not in output


def test_per_match_worker_port_is_stable_and_updates_local_auth_url(tmp_path, monkeypatch):
    monkeypatch.delenv("AUTH_URL", raising=False)
    root, app = project(tmp_path, "AUTH_URL=http://localhost:3000/auth/callback\n")
    a = ProjectRuntime(root, "m1", "worker-a")
    again = ProjectRuntime(root, "m1", "worker-a")
    b = ProjectRuntime(root, "m1", "worker-b")
    assert a.env["PORT"] == again.env["PORT"]
    assert a.env["PORT"] != b.env["PORT"]
    assert 31000 <= int(a.env["PORT"]) < 40000
    assert a.env["AUTH_URL"] == f"http://localhost:{a.env['PORT']}/auth/callback"


def test_database_password_is_passed_via_temporary_pgp_password_env(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev:privatepw@localhost:5433/app\n")
    runtime = ProjectRuntime(root, "m1")
    with patch("runner.project_runtime.subprocess.run", return_value=Mock(returncode=0, stdout="1\n", stderr="")) as run:
        runtime._ensure_scoped_databases()
    assert run.call_args.kwargs["env"]["PGPASSWORD"] == "privatepw"
    assert "privatepw" not in " ".join(run.call_args.args[0])
    assert "PGPASSWORD" not in runtime.env


def test_worker_prepare_installs_and_generates_without_database_mutations(tmp_path):
    root, app = project(tmp_path, "DATABASE_URL=postgresql://dev@localhost:5433/app\n")
    prisma = app / "prisma"
    prisma.mkdir()
    (prisma / "schema.prisma").write_text("datasource db {}")
    runtime = ProjectRuntime(root, "m1", "worker-1")
    calls = []

    def fake_run(argv, cwd=None, timeout=300, _env_overrides=None):
        calls.append(list(argv))
        return CommandResult(tuple(argv), str(cwd or app), 0, "", "")

    with patch.object(runtime, "run", side_effect=fake_run):
        results = runtime.prepare()
    assert all(result.ok for result in results)
    assert calls == [["pnpm", "install", "--frozen-lockfile"], ["pnpm", "exec", "prisma", "generate"]]


def test_valid_database_url_credentials_are_preserved_in_memory(tmp_path):
    root, _ = project(tmp_path, "DATABASE_URL=postgresql://dev:validpw@127.0.0.1:5433/app\n")
    runtime = ProjectRuntime(root, "m1")
    assert "validpw" in runtime.env["DATABASE_URL"]
    assert "validpw" not in runtime._redact(runtime.env["DATABASE_URL"])
