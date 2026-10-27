# MTM PLAYER

The standalone Windows application owns setup, launch and the match lifecycle. Build
it with `npm run package:app` on Windows. The output directory
contains `mtm-player.exe`, a bundled Node.js runtime and `app/`; keep these together.
Open the executable and select an installed team workspace, or run
`mtm-player.exe --workspace "D:\mtm\HNKSUPER"`.
Management opens at `http://127.0.0.1:54321/manage`. Codex desktop is not required;
PowerShell 7 and an authenticated compatible CLI provider are required.

MTM PLAYER is the contestant-facing local application for the Master the Machine
Autonomous AI Engineering Championship.

## Boundary

### Default MTM PLAYERlication (0.8+)

`init` starts or reconnects the workspace's local Player application and opens
`http://127.0.0.1:<persisted-port>/init`. After submission the same origin serves
`/<matchId>`. Launcher exit and stdin closure do not stop the service. One owner
polls Game Master and retains exact private recovery state in the organizer profile.
The service directly owns a supervised coding-agent CLI session and continues after
the launching Codex task closes. Codex CLI is the default provider; the supervisor
contract also supports Claude Code CLI for a compatible team pack.
Finished dashboards remain live. Read [MTM PLAYERlication](docs/local-application.md)
for the current transport, restart and uncertain-outcome recovery contract.

The command-loop details below describe the explicit `--legacy` fallback, not the
default lifetime or credential retention policy.

The selected simple design uses one Codex session per match:

1. The organizer invokes `mtm-player init`; Player collects the team/workspace,
   obtains team information from Game Master, and keeps one MTM PLAYERlication for
   the full match.
2. When init returns `READY`, the init page moves to the live dashboard and Player
   polls official match state every five seconds. The response may include the
   server-owned `statuses` definitions. At `PREPARING`, it starts the registered
   `team_orchestrator` through the selected supervised CLI provider with repository data
   and pre-game match-page access. The root clones, installs, builds, logs into the
   match page without acting on backlogs, verifies authenticated access using the
   team's selected tools, and returns `ENVIRONMENT_READY`.
3. Player performs a fast contract validation and calls Game Master Preparation Complete itself.
4. When Game Master returns `PREPARED`, Player waits for official `IN_PROGRESS`, then
   continues the same root with the match page and team API access. The root reloads its retained authenticated
   tab; Player does not repeat the account or password. No second prompt is required.
5. The root performs and submits backlog work and emits semantic progress to Player.
6. Player combines the progress log with organizer-host model, tool, process, and
   network telemetry and shows a localhost dashboard.
   The Network & API activity table uses its own bounded scroll area so long matches
   do not continuously extend the page.
7. Player continuously saves `team-sdlc-pack/logs/<matchId>.dashboard.html` so the
   observed match remains viewable after shutdown.
8. After game start, Player saves a credential-free recovery checkpoint. If the
   Player task closes unexpectedly, `restart` reacquires credentials and resumes from
   the existing checkout and progress log without repeating preparation.
9. `PAUSED` stops the orchestrator tree; a later `IN_PROGRESS` creates a recovery
   root. `FINISHED` and `CANCELLED` stop work permanently while preserving the static dashboard.

The MTM PLAYER writes lifecycle descriptors directly to the CLI stdin pipe and captures
JSONL output without a TTY. PREPARE creates one resumable session, GAME resumes the
same session, and STOP terminates the owned process tree.

Known lifecycle states are handled deterministically. If Game Master adds a new state,
Player accepts it only when the same response defines that state. The Codex host then
classifies its description into one constrained lifecycle behavior: wait, prepare,
run, pause, or end. Until classification succeeds, Player takes no team action.

The target requirements are in
[simple runtime requirements](docs/requirements-simple-runtime.md). The current code
implements the Windows `init` bootstrap, static discovery of an installed
`team-sdlc-pack`, and a fail-closed native spawn contract. It retains an earlier
experimental signed-file/stdio slice as test evidence.

It must remain independent from MTM Master. This source tree must never contain or import:

- graders or scoring engines;
- hidden tests, answer keys, oracle patches, or score manifests;
- organizer-pack assets; or
- contestant challenge source outside its assigned runtime workspace.

## Team Contract

Each team installs `team-sdlc-pack`, which exposes one root entrypoint named
`team-orchestrator`. The team owns all internal workflows, agents/subagents, AI
integrations, tests, Git automation, and recovery logic.

## Initialize a match

Requires Windows and Node.js 20 or newer. First copy the team-specific `.tgz` and
`install.ps1` into the team workspace, then install before opening the contest task:

```powershell
powershell -ExecutionPolicy Bypass -File "D:\mtm\HNK\team-sdlc-pack\install.ps1" -TeamWorkspace "D:\mtm\HNK"
```

The clean installer removes only the previous pack's managed paths, preserves
`challenge` and unrelated team files, and uses `team-sdlc-pack\.npm-cache` rather
than the user's npm cache. After it returns `READY`, open one Codex task at the team
workspace and run:

```powershell
node scripts/player.mjs init
```

`init` verifies the installed pack and shows its version, Team ID, and team name above
a one-time form. Every match asks for Team ID, Match ID, team workspace, Game Master
base URL, and the Game Master bootstrap secret. The base URL defaults to the saved
value or `https://ai-fest-five.vercel.app/` on first use and can be changed directly
for the current match. The form listens only on `127.0.0.1`, expires
after ten minutes, uses a random route, and returns only a redacted success result to
the browser. The `WAITING_FOR_INPUT` frame carries a validated `openInCodex` action;
the host opens that exact URL in the visible Codex browser panel and confirms success
before telling the operator that the form is open. After submission, the browser
automatically moves to the match dashboard.
The full session stays inside the active Player process. The operator-facing result is
a credential-free, length-framed receipt; endpoints and credentials are never copied
from rendered terminal text.

Before every init, Player requires `challenge` to be empty. If it contains any file or
directory, Player stops and asks for explicit permission to delete all contents. Only
after confirmation may init be rerun with `--clear-challenge`; declining leaves every
existing file untouched.

Use the following command only when the organizer explicitly wants to replace the
saved API URL:

```powershell
node scripts/player.mjs init --reconfigure-game-master
```

If the machine cannot open a browser, use `node scripts/player.mjs init --terminal`
for the earlier terminal prompts and hidden secret input.

Team ID, Match ID, team workspace, and bootstrap secret are entered on every `init`.
Player stores the API URL under `%LOCALAPPDATA%\MTM PLAYER\config.json`. Legacy DPAPI
credential cleanup is best effort and never blocks initialization. Player does not
create `.env` files. After game start it persists only a bounded, non-secret checkpoint
containing team/match identity, repository assignment, prepared revision, workspace
and progress-log paths, and restart count. Passwords, API keys, authorization data,
and Preparation Complete metadata are excluded.

If the Player task or process closes after the match has started, open a new Codex task
at the same team workspace and run:

```powershell
node scripts/player.mjs restart
```

The restart form asks for the current Team ID, Match ID, workspace, Game Master URL,
and bootstrap secret. Player gets fresh credentials, validates them against the saved
checkpoint, preserves the checkout and dependencies, reopens the existing dashboard
and log, and starts a replacement orchestrator with `RESUME_GAME_V1`. It does not call
Preparation Complete again. A missing or mismatched checkpoint fails closed.

The selected workspace must be the workspace used to open the current task and must
already contain:

```text
D:\mtm\T01\
├── challenge\          # team Git checkout and submission evidence
└── team-sdlc-pack\     # team-specific package, installer, and installed runtime
```

Player never installs team code during init. It verifies the payload and every managed
registration before contacting Game Master. The entered Team ID must match the pack's
embedded Team ID. Missing, changed, linked, or mismatched files fail initialization
with a sanitized code.

Player passes both canonical child paths to the orchestrator and treats `challenge`
as its only source write boundary. Codex registration metadata remains in hidden
`.codex`, `.agents`, and `.mtm` directories at the team root.

The root runtime write exception is the exact match log at
`team-sdlc-pack\logs\<matchId>.progress.ndjson`. Player validates each line as
untrusted team-reported telemetry and serves a random localhost dashboard route. It
does not use the log for grading, official timing, or proof of acceptance.
Each backlog row shows the latest aggregate verification result and test counts.
Player measures active `IN_PROGRESS` intervals and updates the page every second. The
duration is local observation, resets when the dashboard process restarts, and is not
Game Master time. Player also writes `<matchId>.dashboard.html` beside the log. That
self-contained snapshot needs no running server and remains readable after the match.

The live dashboard includes a collapsed **Team info session** support panel. An
organizer can explicitly reveal the complete validated team-info response, including
the match login, team API key, repository assignment, and Preparation Complete
contract. Player fetches this value only after the click from the random loopback
route and marks the response `no-store`. It does not embed the value in the dashboard
HTML, progress log, transcript, machine receipt, recovery checkpoint, or static
dashboard. The Game Master bootstrap secret is not part of the team-info response and
is never shown by this panel.

When the organizer host publishes its control plane, Player shows per-model token
usage, tool activity, and sanitized method/origin/path network activity from
`HOST_OBSERVED` records. It calculates nominal credit only for models in a versioned
organizer price card. Unknown rates show `N/A`; the number is a comparison metric, not
an invoice or score. Stock-host fallback data is labeled `TEAM_REPORTED` or
`UNAVAILABLE` so missing telemetry cannot be mistaken for no activity.

The root and descendants run under the selected supervised CLI provider. Player does not
grant, narrow, or approve their operations. Preparation and game descriptors carry a
credential-free activity scope for the assigned repository, branch, Game Master
origins, and expected Git/API operations. The team runtime must enforce those rules,
including rejecting force push, remote changes, unrelated repositories, and unrelated
network origins. Missing Git login or credentials remains an organizer intervention;
Player and the team runtime never perform a login silently.

The MTM PLAYER persists match recovery state in its protected local store and owns the
selected CLI process tree. It starts PREPARE directly, records the official `PREPARED`
receipt, waits for `IN_PROGRESS`, and resumes the same CLI session with GAME. The
launching Player task may close without interrupting this sequence. Credentials never
appear in terminal output, dashboard data, or transcript projections.

The manual inspection command remains available and does not run team-owned code:

```powershell
node scripts/player.mjs inspect-runtime --workspace "D:\path\to\team-workspace"
```

Player uses the installed `team_orchestrator` instruction with an empty CLI session and
never falls back to a generic agent. The runtime manifest declares compatible providers;
Player rejects an undeclared provider before dispatch.

At game start Player supplies a credential-free activity scope for the validated Game
Master origins, Start Work/Submit operations, and assigned Git work. It applies to the
orchestrator tree as a validation and monitoring contract; it does not override task
permissions. Provider-level approval failures are reported through the progress log
and dashboard. Player neither approves tools nor
proxies team API requests.

The organizer host may give Player privileged read-only access to agent, tool, process, file,
network, and model-usage events plus the ability to stop the team tree. The team and
its descendants do not receive those observation privileges. Suspected out-of-scope
activity is recorded under **Suspected Violations** for organizer review after the
match; it does not automatically stop or decide the match. See the
[host control-plane contract](docs/host-control-plane.md).

When a stock Codex host does not publish the optional control-plane capability
manifest, Player continues in `NATIVE_COLLABORATION` mode using child lifecycle,
stop control, and the progress log. It reports filesystem/network enforcement as
unverified instead of blocking the match.

The MVP API request is `POST /api/v1/game-player/teams` with the bootstrap secret only
in `Authorization: Bearer ...` and
`{ "teamId": "HNK", "matchId": "PPFYCZBL" }` as the JSON body. Team codes are
case-insensitive. Player requires `Cache-Control: no-store` and `X-Request-Id` on the
response. The response must include the Game Master URL, echoed Match ID, repository
assignment, and Preparation Complete contract. Player validates their identity and
keeps them only in the current session. Production URLs require HTTPS. The response
contract and validation rules are defined in the
[simple runtime requirements](docs/requirements-simple-runtime.md).

Player derives `matchPageUrl` as `/match` on the validated Game Master page origin.
Init also validates the returned repository assignment and Preparation Complete
contract. Player's active-session continuation uses the byte-identical retained
endpoint privately after `ENVIRONMENT_READY`; operators and agents must not reconstruct
it from terminal output or invoke it with PowerShell/curl. Failed requests retry with
that original endpoint. Player then passes
the match page to the root only at game start. The root reads live Start Work and
Submit endpoints there. Match-scoped UUIDs are never copied into configuration.

## Existing experimental local demo

Requires Node.js 20 or newer. The demo also requires Git on PATH. There are no npm
dependencies and no API keys to configure. From this directory:

```powershell
node scripts/player.mjs --help
node scripts/player.mjs demo
npm test
node scripts/check-distribution.mjs
```

The demo creates a fresh `mtm-player-demo-*` directory under your OS temporary
directory. It signs a public sample with an ephemeral mock-issuer key, invokes the
sample team agent, and returns a Git revision and receipt. The private key is never
written. The root path in the output contains `agent.json`, `config.json`, `event.json`,
`workspace/` and `state/state.json`. Retain it to inspect/replay; remove it when done.
The sample agent creates one evidence file and a commit. It is not a Codex adapter.

```powershell
node scripts/player.mjs status --state "<demo-root>/state"
node scripts/player.mjs run --config "<demo-root>/config.json" --event "<demo-root>/event.json"
```

Exact replay returns the same receipt without invoking the agent again. A local
receipt has status `PENDING_GAME_MASTER`; it is not official acceptance or an official
timestamp. The historical sample deadline deliberately demonstrates that Player
does not rule on contest time.

## Legacy process-agent demonstration

Create a candidate manifest using absolute executable and script paths:

```json
{
  "protocolVersion": 1,
  "name": "mtm-team-agent",
  "command": ["C:/Program Files/nodejs/node.exe", "C:/team/agent.mjs"],
  "capabilities": ["tickets-v1", "git", "subagents"]
}
```

The executable is launched directly with literal arguments, never through a shell.
It receives one HEALTH or EVENT object on stdin, and emits NDJSON on stdout. Reserve
stdout for protocol. Implement the [protocol](docs/protocol.md); use the
[sample agent](examples/team-agent.mjs) as a small exchange example. Your agent may
launch tools/subagents or resume a team-owned service without Player dictating how.

```powershell
node scripts/player.mjs register --manifest "C:/team/candidate.json" --workspace "C:/team/public-workspace" --out "C:/team/agent.json"
```

Registration checks health, creates a new file and refuses overwrite. It does not
lock a tournament setup. Keep registration and Player state outside the team workspace.

Provision a config with exactly these fields:

```json
{
  "protocolVersion": 1,
  "teamId": "TEAM-01",
  "agentPath": "C:/team/agent.json",
  "trust": { "gm-key-1": "<Game Master Ed25519 public PEM>" },
  "workspaces": { "workspace-1": "C:/team/public-workspace" },
  "stateDir": "C:/team/player-state",
  "timeoutMs": 30000
}
```

Use Game Master's actual public key and signed ticket, not the demo issuer. All file
paths are absolute. Signed `teamId` and `workspaceRef` must match the config. Unknown
fields and unsupported versions fail closed. The timeout is a local process watchdog,
not a contest deadline. No host provider credentials are inherited by the child.

## Recovery and visibility

Status shows event identity, latest lifecycle, attempts and receipts. State retains
typed lifecycle messages and public signed envelopes, with atomic updates and a
single-writer lock. A nonzero exit, malformed output or timeout cannot create a receipt.
BLOCKED/FAILED and interrupted attempts require explicit `--retry`. The team agent
must deduplicate side effects using eventId. Never remove a stale lock until the old
Player and its children have stopped. This version has one active event per state
directory and a 1 MiB state read limit; it is intended for small local simulations.

Errors avoid echoing child output or ticket contents. Inspect typed state and your
agent's own debugging facilities. Player does not read evidence files or validate Git
revisions; it records portable references for later Game Master verification.

## AI-assisted installation

The packaged release includes `AGENTS.md` and `INSTALL-CODEX.md`. A new AI coding
session can read those files, copy the portable application, preserve existing data,
and verify `/health`. No marketplace registration or MTM Player plugin is required.
The team runtime remains a separate package.

## Distribution and limits

```powershell
npm pack --dry-run --json
npm pack --pack-destination "<existing output directory>"
```

The prepack gate checks allowed package paths, rejects links and restricted filenames,
and checks static module imports against Node built-ins and package-local files.
The npm allowlist ships runtime, sample, public protocol, README and plugin skill.
Development backlog, design exploration, validation evidence and local HiSDK files
are excluded. Review the actual archive inventory as well as the gate output.
This check catches accidental contamination; it is not semantic analysis of arbitrary
code renamed to an innocuous filename.

Player is not an OS sandbox. Production use needs a disposable environment exposing
only public contestant assets and enforcing process-tree, mount and network isolation.
Do not run contestant submissions in this development repository. Signed outbound
polling, official receipts, setup lock, organizer Gateway and remote verification
remain future work. No local grading or timing authority is present.

## Development records

All Player documentation is here, including the [local backlog](docs/backlog/backlog-list.md),
[public contract](docs/protocol.md) and [design exploration](docs/design-options.md).
The parent initiative is MTM-BL-0014. Run HiSDK transitions/index synchronization
against this directory's `.hisdk/project.json`. IDs share the repository's MTM
namespace, so allocate above the maximum across both backlog directories.
# Practice rounds

`mtm-player init` creates a new local practice round after form submission.
`mtm-player restart` recovers the current round. `mtm-player status [matchId]`
lists recorded rounds and their dashboard links for the current team workspace.
The old agent tree must be stopped before a new init; challenge deletion still
requires explicit consent. The reset command is retired. Prior dashboards remain
available at `http://127.0.0.1:<port>/<matchId>/round01`, `round02`, and so on.

## Paired team releases

Build HNK and DLQ together using `npm run package:teams --prefix mtm-player`.
Both archives use the version in `team-sdlc-pack/package.json`. The command checks
that every runtime payload file is byte-identical except `team-pack-info.json`,
then writes separate ZIP installers and checksums for both teams. Never publish a
new HNK package without its matching DLQ package (or vice versa).
