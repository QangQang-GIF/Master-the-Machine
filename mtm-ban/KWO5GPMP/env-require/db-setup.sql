-- Creates the application role and database for Solova on a local PostgreSQL 15+ server.
-- Run once, as the postgres superuser, before the match:
--   & "C:\Program Files\PostgreSQL\16\bin\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -v app_password=<password> -f db-setup.sql
-- Then use the same password in src/.env (DATABASE_URL and TEST_DATABASE_URL). Safe to re-run.
--
-- The role is not a superuser. It gets exactly what the existing tests need:
--   CREATEDB  tests/global-setup.ts creates the freelanceos_test database when it is missing.
--   SET ON PARAMETER session_replication_role  tests/helpers.ts disables the append-only
--             ledger triggers while it empties the test database between cases.
\set ON_ERROR_STOP on

\if :{?app_password}
\else
  DO $$ BEGIN RAISE EXCEPTION 'app_password is required: psql ... -v app_password=<password> -f db-setup.sql'; END $$;
\endif

SELECT format('CREATE ROLE freelanceos LOGIN PASSWORD %L CREATEDB', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'freelanceos') \gexec
SELECT format('ALTER ROLE freelanceos LOGIN NOSUPERUSER CREATEDB PASSWORD %L', :'app_password') \gexec

SELECT 'CREATE DATABASE freelanceos OWNER freelanceos'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'freelanceos') \gexec

GRANT SET ON PARAMETER session_replication_role TO freelanceos;

SELECT rolname, rolsuper, rolcreatedb,
       has_parameter_privilege('freelanceos', 'session_replication_role', 'SET') AS can_set_replication_role
FROM pg_roles WHERE rolname = 'freelanceos';
