# DEV improvement cycle

The supervisor runs separately from the frozen Player-owned competition CLI. It
checks only once per ten minutes. Engine/Skill changes happen between rounds;
unannounced tasks, prior solutions and hidden grader data are never inputs.

## Diagnosed preparation failure

BN1MS5DF stayed PREPARING with no submissions. The CLI reported missing MCP tools
and refused to configure the documented local app environment. A no-turn Codex
app-server probe found all 12 helper tools connected, so a permanent registration
failure was not reproduced. Player's missing MCP readiness check remains a startup
failure hypothesis; the pack now supports the same guarded helper via a persistent
Python stdio bridge when MCP discovery fails.

The local project role already supported passwordless loopback access and the
required test privileges. `runner.project_runtime` derives project configuration,
creates match-scoped local databases through that ordinary role, keeps generated
AUTH_SECRET only in memory, and runs the actual provided doctor and build. A real
baseline run passed install, migrations, seed, doctor READY and build. It does not
rewrite .env, install host services, alter supplied doctor scripts, or fake READY.

## Runtime and isolation

- Native worktrees materialize only the current repository folder using sparse
  checkout. Task envelopes supply the project runtime path and scoped worker ID.
- App databases are isolated by match; test databases and ports by worker. Only
  the root preparation can migrate/seed the shared app database. Worker preparation
  installs dependencies and generates its client without reseeding another worker.
- Missing MCP uses `runner/control.py` in a native PTY, with echo disabled before
  accepting a descriptor. The same Python guards/journal handle every operation.
  No secondary AI CLI or credential files are introduced.
- The coordinator computes task ages from one time sample per plan, avoiding a
  priority flip between planning and claiming equally weighted tasks.
- Submission journals are scoped by match. `runner.match_hygiene` refuses cleanup
  until Player is STOPPED with no live process or pending operation, removes old
  local checkouts/knowledge/answer caches, and preserves official audit records and
  host-owned transcripts.

## Supervisor state

`runner.training_loop` stores only match ID, source revision, statuses, score
metrics and timing in `state/dev-cycle/supervisor.db`. Its atomic reservation gate
allows one observation every 600 seconds, including when multiple wakeups overlap.
The human has authorized immediate retry on errors. On detecting a runtime/input/
evidence failure, the supervisor requests an official Player stop immediately,
verifies STOPPED/no live process, then enters DEV review without waiting for the
old match clock to finish. Skills are never edited while that CLI is running;
answers are never injected and approvals are never bypassed. The next attempt
gets a fresh match and context. Host approval blocks still require the organizer.

Only FINISHED plus finite Game Master awarded points equal to the positive
official maximum sets TARGET_REACHED. Local PASS, ACCEPTED receipts and apparent
task completion are insufficient. Missing official score means the target remains
unconfirmed. Host approval and account limits are reported honestly; no reset or
purchase is automatic.

Read the current match identity from the supervisor ledger rather than a static
ID in documentation. Each later round must use a fresh Player session/context and freshly supplied
assignment, with no previous task patches/answers carried into the prompt.

The next DEV correction resolves only publicly announced release files pushed
after clone, derives deadlines from the server's pause-adjusted elapsed counters,
and runs web evidence in one bounded server/check session with shared ephemeral
configuration and process-group cleanup. No past task solution is part of these
generic fixes.
