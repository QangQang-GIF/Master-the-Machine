"""Each worker gets a private application database (no cross-item fixture pollution)."""
from runner.project_runtime import CommandResult, ProjectRuntime


def project(tmp_path, scripts='"db:seed":"tsx prisma/seed.ts"'):
    root = tmp_path / "match"
    app = root / "src"
    app.mkdir(parents=True)
    (app / "package.json").write_text('{"packageManager":"pnpm@9.0.0","scripts":{%s}}' % scripts)
    (app / ".env.example").write_text("DATABASE_URL=postgresql://dev@localhost:5433/appdb\n")
    return root


class FakePg:
    """Minimal stand-in for psql/createdb/dropdb plus package-manager steps."""

    def __init__(self, existing=(), clone_fails=(), steps_fail=False):
        self.dbs = set(existing)
        self.clone_fails = set(clone_fails)
        self.steps_fail = steps_fail
        self.calls = []

    def __call__(self, runtime):
        def run(argv, cwd=None, timeout=300, _env_overrides=None):
            argv = list(map(str, argv))
            self.calls.append((argv, runtime.env.get("DATABASE_URL")))
            tool = argv[0]
            ok = lambda out="": CommandResult(tuple(argv), "", 0, out, "")
            bad = CommandResult(tuple(argv), "", 1, "", "boom")
            if tool == "psql":
                name = argv[-1].split("datname = '")[1].split("'")[0]
                return ok("1" if name in self.dbs else "")
            if tool == "createdb":
                if "-T" in argv:
                    src = argv[argv.index("-T") + 1]
                    if src in self.clone_fails or src not in self.dbs:
                        return bad
                self.dbs.add(argv[-1])
                return ok()
            if tool == "dropdb":
                self.dbs.discard(argv[-1])
                return ok()
            return bad if self.steps_fail else ok()
        runtime.run = run
        return runtime


def test_two_workers_get_different_app_databases(tmp_path):
    root = project(tmp_path)
    pg = FakePg(existing={"appdb_m1", "appdb_m1_tpl"})
    a = pg(ProjectRuntime(root, "m1", "w-BUG-02-1"))
    b = pg(ProjectRuntime(root, "m1", "w-CR-01-1"))
    assert a.ensure_worker_app_database() == "cloned:template"
    assert b.ensure_worker_app_database() == "cloned:template"
    assert a.env["DATABASE_URL"].endswith("/appdb_m1_w_bug_02_1_app")
    assert b.env["DATABASE_URL"].endswith("/appdb_m1_w_cr_01_1_app")
    assert a.env["DATABASE_URL"] != b.env["DATABASE_URL"]


def test_second_call_reuses_the_existing_copy(tmp_path):
    root = project(tmp_path)
    pg = FakePg(existing={"appdb_m1", "appdb_m1_tpl"})
    assert pg(ProjectRuntime(root, "m1", "w1")).ensure_worker_app_database() == "cloned:template"
    again = pg(ProjectRuntime(root, "m1", "w1"))
    assert again.ensure_worker_app_database() == "existing"
    assert again.env["DATABASE_URL"].endswith("/appdb_m1_w1_app")


def test_falls_back_to_cloning_the_shared_db_without_template(tmp_path):
    root = project(tmp_path)
    rt = FakePg(existing={"appdb_m1"})(ProjectRuntime(root, "m1", "w1"))
    assert rt.ensure_worker_app_database() == "cloned:shared"


def test_busy_shared_db_falls_back_to_migrate_and_seed(tmp_path, monkeypatch):
    monkeypatch.setattr("runner.project_runtime.time.sleep", lambda s: None)
    root = project(tmp_path)
    pg = FakePg(existing={"appdb_m1"}, clone_fails={"appdb_m1"})
    rt = pg(ProjectRuntime(root, "m1", "w1"))
    assert rt.ensure_worker_app_database() == "created:migrated"
    seeds = [url for argv, url in pg.calls if argv[:2] == ["pnpm", "run"]]
    assert seeds and all(u.endswith("/appdb_m1_w1_app") for u in seeds)  # seeded the private copy, not the shared one


def test_any_failure_keeps_the_shared_database(tmp_path, monkeypatch):
    monkeypatch.setattr("runner.project_runtime.time.sleep", lambda s: None)
    root = project(tmp_path)
    pg = FakePg(existing={"appdb_m1"}, clone_fails={"appdb_m1"}, steps_fail=True)
    rt = pg(ProjectRuntime(root, "m1", "w1"))
    assert rt.ensure_worker_app_database() == "shared:migrate-failed"
    assert rt.env["DATABASE_URL"].endswith("/appdb_m1")
    assert "appdb_m1_w1_app" not in pg.dbs  # half-built copy removed


def test_root_and_disabled_flag_keep_old_behaviour(tmp_path, monkeypatch):
    root = project(tmp_path)
    main = FakePg(existing={"appdb_m1"})(ProjectRuntime(root, "m1"))
    assert main.ensure_worker_app_database() == "disabled"
    assert main.env["DATABASE_URL"].endswith("/appdb_m1")
    monkeypatch.setenv("MTM_WORKER_DB_ISOLATION", "0")
    w = FakePg(existing={"appdb_m1", "appdb_m1_tpl"})(ProjectRuntime(root, "m1", "w1"))
    assert w.ensure_worker_app_database() == "disabled"
    assert w.env["DATABASE_URL"].endswith("/appdb_m1")


def test_root_snapshots_template_after_seed(tmp_path):
    root = project(tmp_path)
    pg = FakePg(existing={"appdb_m1", "appdb_m1_tpl"})
    main = pg(ProjectRuntime(root, "m1"))
    res = main.refresh_template_database()
    assert res is not None and res.ok and "appdb_m1_tpl" in pg.dbs
    created = [argv for argv, _ in pg.calls if argv[0] == "createdb"]
    assert created and created[-1][-3:] == ["-T", "appdb_m1", "appdb_m1_tpl"]


# ---- prisma generate in workers -------------------------------------------------------------
import subprocess as _sp


def _git_app(tmp_path):
    root = project(tmp_path)
    app = root / "src"
    (app / "prisma").mkdir()
    (app / "prisma" / "schema.prisma").write_text("model A { id Int @id }\n")
    for cmd in (["init", "-q"], ["config", "user.email", "t@example.invalid"], ["config", "user.name", "T"],
                ["add", "."], ["commit", "-qm", "base"]):
        _sp.run(["git", "-C", str(app), *cmd], check=True, capture_output=True)
    return root, app


def test_detects_prisma_generate_commands():
    assert ProjectRuntime.is_prisma_generate(["pnpm", "exec", "prisma", "generate"])
    assert ProjectRuntime.is_prisma_generate(["npx", "prisma", "generate", "--no-hints"])
    assert not ProjectRuntime.is_prisma_generate(["pnpm", "exec", "prisma", "migrate", "deploy"])
    assert not ProjectRuntime.is_prisma_generate(["pnpm", "test"])


def test_worker_skips_generate_when_schema_unchanged(tmp_path):
    root, app = _git_app(tmp_path)
    assert ProjectRuntime(root, "m1", "w1").skip_worker_prisma_generate()
    assert not ProjectRuntime(root, "m1").skip_worker_prisma_generate()        # root always generates


def test_worker_generates_after_changing_schema(tmp_path):
    root, app = _git_app(tmp_path)
    (app / "prisma" / "schema.prisma").write_text("model A { id Int @id\n  name String }\n")
    assert not ProjectRuntime(root, "m1", "w1").skip_worker_prisma_generate()  # uncommitted change
    _sp.run(["git", "-C", str(app), "commit", "-qam", "schema"], check=True, capture_output=True)
    assert not ProjectRuntime(root, "m1", "w1").skip_worker_prisma_generate()  # committed change


def test_force_flag_restores_generation(tmp_path, monkeypatch):
    root, app = _git_app(tmp_path)
    monkeypatch.setenv("MTM_WORKER_PRISMA_GENERATE", "1")
    assert not ProjectRuntime(root, "m1", "w1").skip_worker_prisma_generate()
