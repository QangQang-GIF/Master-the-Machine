# MTM Player host control plane

MTM Player is organizer-owned and trusted. The contest host may grant Player a privileged,
read-only observation plane over the team runtime plus the ability to stop the team
process tree. The team root and every descendant run inside the Local App supervised
CLI session. Observation remains separate from team execution.

An organizer-managed host can provide these additional observation capabilities:

- agent lifecycle events;
- tool activity;
- CLI and process activity;
- filesystem activity;
- network activity;
- model token usage; and
- stopping the team root and descendants.

When this manifest is available, Player receives the monitoring stream outside team
code, so the team cannot edit or delete it. The team remains responsible for guardrails
that constrain its root and descendants to the registered workspace, declared Game
Master and Git origins, and contest-safe Git operations.

The control plane may publish an organizer-owned NDJSON telemetry file and a versioned
nominal price-card file. Player consumes those paths only from trusted host metadata.
Each host record is bound to the Player session and contains bounded metadata; it does
not contain prompts, source content, file bodies, raw tool output, request/response
bodies, headers, credentials, or full command arguments. Model usage includes model,
input, cached-input, output, and reasoning-token counters. Tool activity includes the
tool name, logical operation, phase, and optional backlog ID. Network activity includes
method, credential-free origin, path without query or fragment, phase, bounded status,
and request ID.

The local trial also runs on stock Codex hosts that do not publish this capability
manifest. Player labels that mode `NATIVE_COLLABORATION`, observes child lifecycle and
the team progress log, and retains stop control. Filesystem and network restrictions
are unverified in that mode, which is an explicit observability limitation rather than
a reason to block preparation or game start.

Official comparisons must use `HOST_OBSERVED` usage. `TEAM_REPORTED` fallback remains
visible but is not authoritative. A missing category appears as `UNAVAILABLE`; absence
of telemetry never proves that no tool, model, or network activity occurred.

Nominal credit is calculated from an organizer price card identified by version and
currency. The calculation prices uncached input, cached input, and output separately.
Reasoning tokens are shown as a subset of output and are not charged twice. A model
without a declared rate displays `N/A`. This value is a comparison metric, not a
provider invoice or an official score.

Semantic events such as `BACKLOG_STARTED` and `SUBMISSION_REPORTED` remain team
reported. Host events independently show which agents, tools, commands, files,
connections, and models were used. Host-measured token counts use
`HOST_CONTROL_PLANE`; team supplied counts remain `TEAM_REPORTED`.

The team runtime must not self-start another coding-agent CLI. The organizer-owned
Local App may host the team orchestrator through a supervised CLI provider. The
provider adapter owns the PID tree, session identity and transcript and translates
the same durable PREPARE/GAME/STOP operations for Codex CLI or Claude Code CLI.
Changing providers does not change team authority or protocol frames.

Normal commit and push are expected only for the repository and branch read from the
authenticated match page. The selected CLI provider permissions are inherited by the
root and every child; agent profiles must not override them. Team guardrails reject
force push, remote mutation, and pushes to any other target. Provider-level approval
failures are reported through the progress log and dashboard.
If the organizer rejects an otherwise allowed Git or Game Master action, the root emits `HOST_APPROVAL_REQUIRED`; missing or
expired Git authentication emits `GIT_AUTHENTICATION_REQUIRED`. Player highlights
either condition for organizer intervention. The contestant never handles approval or
authentication after game start, and the runtime never launches a login flow or
persists new credentials.

At game start Player supplies a credential-free activity scope. It binds expected
activity to one Player session and match, the validated Game Master
origins, team API scopes, exact Start Work/Submit path templates, and ordinary Git
operations. The scope applies to the orchestrator tree. It does not change host policy,
grant access, or make Player an approval broker.

Player classifies out-of-workspace filesystem activity, unexpected network origins,
risky Git or credential operations, integrity access, explicit host policy signals,
and telemetry gaps as **Suspected Violations**. These are evidence for organizer
review, not automatic verdicts or runtime blocks.
