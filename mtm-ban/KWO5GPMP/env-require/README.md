# Environment preparation

Prepare the environment before the match; the clock does not stop for setup problems. You need Node.js 20+, Git, pnpm and a local PostgreSQL server (no Docker). The environment is ready only when doctor.ps1 reports `Environment status: READY`. Never use a production database.

## Required setup

1. Run `setup.ps1 -PostgresPassword <password>`. It installs Git, Node.js and pnpm, and PostgreSQL 16 listening on **port 5433** (the port in src/.env.example). If PostgreSQL 15 or later is already installed, make sure it listens on 5433; the password is then not needed.
2. Create the application role and database, once, as the postgres superuser:

   ```powershell
   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -v app_password=<app password> -f db-setup.sql
   ```

   The last line must show `freelanceos | f | t | t`. Safe to re-run.
3. Copy src/.env.example to src/.env and replace `CHANGE_ME` in `DATABASE_URL` and `TEST_DATABASE_URL` with the app password from step 2. Keep the host, port 5433 and the database names. Set the remaining values as described in src/.env.example.
4. Run `prepare-source.ps1` (installs dependencies and checks the build), then from src/: `pnpm prisma migrate deploy` and `pnpm db:seed`.
5. Run `doctor.ps1`. It must report `READY`.

Every working copy, including agent worktrees, uses the same `DATABASE_URL`. `TEST_DATABASE_URL` must differ per working copy whenever tests can run at the same time: the tests empty the test database before every case, so two runs sharing one test database delete each other's data and fail at random. Change only the database name, for example `.../freelanceos_test_task02`, in that copy's src/.env or in the `TEST_DATABASE_URL` environment variable (it takes precedence over src/.env). The tests create a missing test database themselves (the role has `CREATEDB`), so no other setup is needed.

## Test database privileges

The existing tests need two privileges, which db-setup.sql grants to the `freelanceos` role without making it a superuser:

- `CREATEDB`: src/tests/global-setup.ts creates the `freelanceos_test` database when it is missing.
- `SET ON PARAMETER session_replication_role` (PostgreSQL 15+): src/tests/helpers.ts empties the test database between cases with `SET session_replication_role = replica`, because the append-only accounting ledger refuses deletes. Without it every database test fails with `permission denied to set parameter "session_replication_role"`.

This is environment setup: do not change the tests to work around it.

doctor.ps1 checks both through `TEST_DATABASE_URL` (from the environment or src/.env), after `pnpm install` has run in src. The check `test-database-privileges` fails if a privilege is missing; if it cannot run yet (no `TEST_DATABASE_URL`, no `node_modules`), the status is `NOT_READY` too. Options: `-SourceDir <app folder>` (default `..\src`), `-SkipDatabase` (skips the database check; do not use it to prepare for the match).

## Prisma "Update available" notice

Prisma commands may print a box such as `Update available 6.19.3 -> 8.0.0-rc.27 ... This is a major update`. It is an informational version check, not an error: the command still runs and its exit code is unchanged. Do not follow it. It suggests a pre-release major version with breaking configuration changes, and upgrading Prisma is not needed to run, test or build the application. To hide the notice, set `PRISMA_HIDE_UPDATE_MESSAGE=1` (PowerShell: `$env:PRISMA_HIDE_UPDATE_MESSAGE = "1"`), or `CHECKPOINT_DISABLE=1` to also skip the online version check.
