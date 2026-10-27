# MTM PLAYER

The installed MTM PLAYERlication binds to `127.0.0.1:54321`. Its management page is
`http://127.0.0.1:54321/manage`; match dashboards use
`http://127.0.0.1:54321/<matchId>/<roundId>`. Startup fails clearly when another
process owns that port and never silently selects a random replacement.

The management page and each active round dashboard include a live transcript viewer
for registered Player, orchestrator, and worker tasks. The viewer verifies the bound
Codex task identity, keeps only a bounded recent window, redacts credential-shaped
content, and displays message text plus tool names without tool arguments or outputs.
Archived static dashboards do not expose transcripts.

The default init/restart launcher starts or reattaches a detached MTM PLAYERlication
for the selected team workspace. One owner holds the lock, polls Game Master and
persists lifecycle state. The dashboard remains available after stdin closes and
after a terminal match. The external service origin is stable: `/init` and
`/<matchId>` share its persisted port. A port conflict is an error, not permission
to kill a process or silently move the bookmark.

## Host boundary

The plugin command `@MTM PLAYER stop` requests full local shutdown. For a supervised
CLI round, MTM PLAYER stops the exact PID tree it created, records the receipt, closes
its dashboard/listeners and releases its lock. It never kills by process name. During
the migration period, a legacy attached-host round still uses APP_SHUTDOWN_REQUEST and
APP_SHUTDOWN_ACK because that host owns the agent tree.

The attached task waits with `HOST_WAIT` (schemaVersion 1, current hostGeneration,
timeoutMs up to 55000). The application releases the request on a lifecycle change
or pending host operation; the wait does not occupy the mutation queue. Renew a
timeout without repeatedly asking the model to inspect state every five seconds.
The official Game Master poll still runs in the local process every five seconds.
Preparation completion retains the original root idle. IN_PROGRESS queues a GAME
followup to that root; recovery spawns only when it is stopped. The task must remain
attached: this is not a mechanism to wake a closed Codex task.

## Practice rounds

Run `init` for a new local round and `restart` to recover the current round.
The reset command is retired. Init always opens a form; it does not silently reuse
a prepared checkout. The old agent tree and pending operations must be stopped
before init can replace the current attempt. Clearing challenge still requires
explicit confirmation for that exact folder.

Dashboard URLs are `/<matchId>/round01`, `round02`, and so on. The `/<matchId>`
alias redirects to the latest round. Existing unnumbered sessions migrate to round01.
`node scripts/player.mjs status [matchId]` lists recorded rounds for the current
team workspace, including local phase, official status, timestamps and dashboard
links. Omit matchId to select the current match. Status reads persisted state without
starting agents; it is last-recorded evidence, not proof an agent is presently alive.

Before a new round replaces the current one, Player archives its sanitized dashboard
snapshot and logs under the MTM PLAYERlication's `rounds/<matchId>/<roundId>/`.
Archived dashboards are read-only and never expose team-info credentials. The active
round continues to use the existing team progress-log contract for compatibility;
Player clears its projection, status cache and checkpoint for the new session.
A failed initialization leaves the prior round selected. Restart and process recovery
do not allocate a round or clear its progress. Server submissions are never reset.

The live dashboard's **Show full details** button requests team information from
`/<matchId>/team-info` on demand with no-store caching. It includes the team's
login and API credentials while retained, but never the Player bootstrap secret.
Expired or erased credentials return TEAM_INFO_UNAVAILABLE. Cross-origin requests
are rejected. Neither dashboard HTML nor snapshots/static exports embed credentials.


Codex native collaboration requires an attached Player task. The MTM PLAYERlication
queues exact private frames; it cannot autonomously call Codex collaboration tools
after task closure. Host disconnection is shown explicitly. HOST_OPERATION_GET
persists delivery before returning the frame. After delivery, reconnect requires
reconciliation against the actual host agent inventory before replay. Stopping a
root is not recursive: the host must interrupt all descendants and confirm the
entire tree. A queued stop is not a confirmed stop.

## Commands and transport

`node scripts/player.mjs init` starts/reconnects the MTM PLAYER, registers the current
Codex task ID and returns LOCAL_APP_READY with form/dashboard URL and a private control
URL. Open the URL visibly in Codex. The launcher exits immediately; this does not stop
polling or the dashboard. When a host operation becomes pending, the MTM PLAYER invokes
`codex queue` with a credential-free lifecycle prompt for the registered task. The task
then runs `node scripts/player.mjs watch` to retrieve one exact frame, dispatch it and
acknowledge the real result. No standing command waits for form submission.
`restart` opens `/restart` for fresh credentials while preserving the checkpoint.
Legacy command-loop implementation remains explicitly available through `--legacy`.

Control messages are JSON POSTs without browser Origin to the capability URL.
Never expose that URL in the public dashboard. Each message has schemaVersion=1.

1. APP_ATTACH with actual codexThreadId obtains the current hostGeneration.
2. HOST_OPERATIONS with that generation lists pending operations and diagnostics.
3. HOST_OPERATION_PEEK with operationId and generation retrieves one exact frame
   without changing it from PENDING. This makes command stdin closure harmless.
4. Execute its native spawn/followup/stop through the attached host and ACK using
   HOST_OPERATION_ACK with result.target or result.treeStopped=true.
5. HOST_OPERATION_GET remains available for legacy retained-session adapters. A
   delivered operation cannot be GET again. Inspect actual host inventory and use
   HOST_OPERATION_RECONCILE with outcome EXECUTED and result, or NOT_EXECUTED.
6. ENVIRONMENT_READY passes the original structured team result. Player validates,
   persists intent, calls its retained endpoint, persists PREPARED, and updates the
   dashboard immediately. Game work is queued only after official IN_PROGRESS.

APP_STOP suspends local dispatch and queues a tree stop; APP_RESUME re-evaluates
fresh server state. Neither changes official Game Master status. APP_FORGET is
allowed only when agents are stopped and no operation is unresolved. It removes
recoverable private match data. REGISTER_CODEX_AGENT and REGISTER_CODEX_TRANSCRIPT
retain the existing host observation contracts.

## Persistence and uncertain outcomes

State is stored under the organizer user profile, separately for each canonical
team workspace. The store uses flushed immutable generations and a single-owner
lock; no external database or vault installation is needed. Windows inherits the
organizer profile ACL. Never put this directory in contestant Git or exports.
Private state includes exact credentials and queued frames to allow process-death
recovery. Maximum credential lifetime is 24 hours; reauthentication preserves
checkpoints. Terminal status plus confirmed tree stop clears credentials and old
private generations. APP_FORGET explicitly removes an idle session.

Preparation HTTP intent is saved before sending. If its outcome is lost, the app
shows RECONCILIATION_REQUIRED instead of blindly posting again. The organizer must
verify Game Master's result and send PREPARATION_RECONCILE with PREPARED plus the
validated receipt, or NOT_ACCEPTED before retry. This is not exactly-once delivery:
the server has not supplied a receipt-lookup/idempotency contract. Team Start Work
and submission remain the team's responsibility.

Tests with injected Game Master responses qualify local behavior, not all external
server or native host behavior. Real-match and second-machine qualification remain
distinct release evidence; do not call them passed based on synthetic tests.

## Dashboard reading order and display preferences

Dashboard order: Match overview, Backlog Status, Preparation, MTM PLAYER,
Codex task observation, Agent & process activity, Network & API activity,
Model usage & nominal credit, Tool activity, Filesystem activity, Open blockers,
Orchestrator console, Suspected Violations, then Team info session.
Blocker indicators remain at the top in Match overview.

Every content block is a keyboard-accessible disclosure. Its summary remains visible
while collapsed. Preparation opens while incomplete and Backlog Status remains open by default; an explicit user choice overrides these defaults. Choices are stored per
browser route (including round), without match credentials. Storage failures do not
prevent rendering live or archived dashboards. Active blocker indicators remain in
the overview summary even when details are collapsed; resolved intervention alerts
are removed on the next snapshot.

Record tables use 12px monospace text and bounded scrolling. Timestamps use local
24-hour time, adding a short date for other days and a year for other years; the
original timestamp is available on hover. UUID labels use a collision-aware prefix
and suffix across the displayed snapshot. Clicking the label copies the exact full
UUID. If clipboard access fails, the full value is shown for manual selection.
Before game start, Backlog Status displays "Wait for game start". Backlogs are ordered blocked, in progress, pending, then submitted. Console follow
can be disabled to read earlier entries without jumping on refresh.

These are Player presentation changes; no additional orchestrator events or fields
are required.
The MTM PLAYERlication owns a provider-neutral coding-agent CLI supervisor. Codex CLI
is the default. Set `MTM_PLAYER_CLI_PROVIDER=claude` before starting a fresh round to
use Claude Code CLI. The provider is persisted and cannot change within that round.
PREPARE starts one session, GAME resumes that exact session, and STOP terminates the
owned process tree. Protocol descriptors are written to process stdin over a pipe.

For Codex, the standalone application resolves `CODEX_CLI_PATH` first and then the
current process PATH. On Windows it can also discover the newest Codex Desktop
executable under the user's installation directory. This fallback is release-neutral:
no Codex build hash is stored in Player. The Init page reports whether model discovery
failed because the CLI was missing, the request timed out, or the app-server response
was invalid, and permits retry without restarting Player. Init always displays the
resolved executable path above the model list. The operator may replace it with an
absolute file path; Player persists and uses it only after model discovery succeeds.

The MTM PLAYER also writes a credential-free hash-chained diagnostic journal outside
the team workspace. Management shows its integrity, lifecycle timeline and stable
error codes. Journal failure is reported but never blocks status polling, preparation,
game start, resume or stop. A corrupted journal produces a Suspected Violation.

The team runtime must not read or modify MTM PLAYER storage, call Player control routes,
or manage Player-owned processes. Because the local trial runs both processes under
one OS user, this boundary is tamper-evident rather than tamper-proof. Strong isolation
requires a separate OS identity or host sandbox.

On Windows, the trusted PowerShell 7 launcher assigns itself to an unnamed Job Object
before starting the CLI. KILL_ON_JOB_CLOSE is enabled and breakaway is not allowed.
Ordinary descendants inherit membership, including detached children. The launcher
holds the job handle and watches the original MTM PLAYER process handle; MTM PLAYER
exit triggers launcher exit and job cleanup. Failure to create or join the job prevents
CLI launch and reports WINDOWS_JOB_LAUNCH_FAILED. Provider readiness is separate from
containment readiness. This does not contain work delegated through an external service,
scheduled task, or remote provider, and is not a filesystem or process-access boundary.
Windows process-tree and crash cleanup tests are required before release.

### Starting another round from the standalone form

If initialization encounters an active round, the web form requests explicit stop confirmation. MTM PLAYER stops its managed CLI runtime before enabling initialization again. This flow does not require a Codex desktop task ID. Legacy host adapters still require host-side stop acknowledgement. A persisted RUNNING value alone is not proof that an external legacy Codex task is alive or stopped.


### CLI round dashboard

Orchestrator activity follows the round-owned CLI session directly, independently of desktop task registration. Transcript, operator chat, tool lifecycle records, and progress events have separate tabs. Chat sends bounded guidance to the exact round-owned session and records the user message in the transcript. A message submitted during an active turn or approval wait is queued until that turn ends. Chat cannot change lifecycle state, grant approval, or replace Game Server state, and it is disabled after the orchestrator stops or the match ends. Runtime diagnostics groups process and polling state; desktop task observation is hidden for CLI rounds. Activity timestamps describe observed events, not proof of continuous useful work. Command arguments and raw tool output remain omitted from the transcript projection.

CLI usage replaces overlapping team/host totals for the same round and is persisted in static snapshots. Cached input is included in input. Each completed Codex turn is counted once until another turn starts. Models are attributed only when reported by the provider; historical unattributed usage remains Unknown and has no nominal price. Missing usage is not zero. Transcript content discarded by older builds cannot be reconstructed.


CLI observability records reported command, MCP tool, lifecycle, and file-change events in a bounded 500-record window. File reads and arbitrary network traffic are not monitored. Team MCP HTTP clients expose bounded metadata through structuredContent.networkActivity; only method, origin/path, status and time are captured, never request bodies, headers or URL queries. Background requests become visible on the next tool result. Tool details are sanitized before persistence; old omitted details remain unavailable. Transcript rendering supports safe text, headings, bold and code blocks without executing HTML.


### Resume a stopped match

The local webserver persists recovery state in its user-profile JSON store: match identity, credentials, round, preparation receipt, CLI session and activity totals. DPAPI is prohibited by company policy; it must never be used, including as a fallback. No browser storage is used for credentials.

Open management and select Resume match. Resume validates the selected match/round and reads fresh Game Server status before dispatch. A PREPARED checkpoint skips environment setup and waits for IN_PROGRESS. Repeated clicks do not create a round or duplicate the active CLI. Expired credentials lead to the restart form; reauthentication preserves the round and releases the operator-stop latch. Finished or cancelled matches cannot resume.


## Server startup and workspace selection

The desktop executable starts the loopback server without a workspace argument. Init loads a pack only after the operator supplies a workspace and selects Load team pack. Verification runs before changing runtime ownership. A running team must be stopped explicitly before switching. Each workspace retains its own store and round history; the last selected workspace is remembered in player-workspace.json for subsequent server starts.

This selection uses a local Windows path entered in the web form; it does not upload the folder. Init revalidates the workspace and discovers its manifest before dispatch. The executable no longer displays a native folder dialog.


## Match workspace roots

New matches clone into `TeamWorkspace/matches/<matchId>/` (`checkoutRoot`). The assigned source root is `checkoutRoot/repository.folder` (`matchRoot`). Preparation, game and recovery descriptors carry both absolute paths. Source, public challenge files, answers and evidence belong in matchRoot. Git-relative submissions must use `<repository.folder>/answers/<backlogId>/submit.json` and its evidence directory. Worker worktrees derive matchRoot from their own Git root and the same repository.folder. Init/reset affects only the selected match checkout; sibling matches are preserved. Saved legacy checkouts remain at their original paths. `challengeWorkspace` remains a compatibility alias for the checkout container, never the canonical answer root.
