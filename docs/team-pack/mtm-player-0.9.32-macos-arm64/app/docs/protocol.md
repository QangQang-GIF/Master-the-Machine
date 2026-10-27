# MTM Player protocol v1

Status: implemented experimental local vertical-slice contract; superseded as the
product direction by `requirements-simple-runtime.md`. Keep it for compatibility and
test evidence. Do not extend it into a Player-mediated ticket/submission service
without a new product decision.

## Architecture and trust

Game Master signs a public event. Player authenticates it against a provisioned Ed25519
public key, durably records it, and invokes one registered `mtm-team-agent` process.
Only that process's stdout is accepted as lifecycle/submission input. The team owns
all internal delegation, models, tools, testing, Git and recovery. Player has no
dependency on MTM Master or its artifact formats. The existing public submission
format requires organizer-specific identity; protocol v1 deliberately defines a new
transport envelope. Integration belongs in a future Game Master adapter.

Player is a protocol boundary, not an OS sandbox. Run it in a disposable contestant
environment containing only the assigned public workspace and team tools. Process
permissions, network policy and mounted volumes must enforce isolation. Never run
untrusted contestant automation in the development repository. Local state is an
audit aid, not tamper-proof evidence against its OS owner.

## Files

- `agent.json`: `{protocolVersion:1,name:"mtm-team-agent",command:["absolute executable",...args],capabilities:[...]}`.
  The command is operator-provisioned, never supplied by a ticket. Arguments are literal;
  no shell expansion. Capabilities are descriptive strings, not permissions.
- `event.json`: `{protocolVersion:1,keyId,payload,signature}`. Payload is canonical
  base64 of UTF-8 JSON; signature is canonical base64 of the 64-byte Ed25519 signature
  over the exact decoded bytes. Trust configuration maps key IDs to public PEM keys.
  No signing private key or team provider API key is required by Player.
- Ticket payload: `{protocolVersion:1,type:"TICKET",eventId,runId,ticketId,teamId,
  workspaceRef,issuedAt,deadline,ticket:{title,instructions}}`. Times are UTC ISO strings
  assigned by Game Master. Workspace references are opaque IDs resolved by local
  configuration, never paths from an event.
- Local state: versioned `state.json`, written atomically under an exclusive lock;
  contains authenticated envelopes, lifecycle events and an outbox of receipts.
  Keep outside the agent workspace. Do not store secrets in public ticket text.
  State is bound to a team ID and this local version limits reads to 1 MiB.

## Stdio exchange

One UTF-8 JSON object per line, stdout reserved for protocol; stdin ends after one
request. Maximum combined stdout is 64 KiB. Stderr is discarded, never persisted.
Each invocation must exit successfully within the configured local watchdog.

1. Player launches the registered command with `{protocolVersion:1,type:"HEALTH"}`.
   Exactly one response: `{protocolVersion:1,type:"READY",agent:"mtm-team-agent"}`.
2. Player launches the same command with `{protocolVersion:1,type:"EVENT",event,
   envelope,workspace}`. `event` is the verified payload; `envelope` retains its
   signature. IDs remain unchanged. `workspace` is the resolved assigned directory.
3. Every response contains `protocolVersion:1,eventId,runId,ticketId` and `type`.
   Lifecycle: `STARTED`, then zero or more `PROGRESS`, then one terminal
   `SUBMITTED`, `BLOCKED`, or `FAILED`. READY is the health response only.
   BLOCKED/FAILED include a bounded machine-readable `code`; PROGRESS includes
   integer `percent` (0..100). Unknown fields are rejected to avoid retaining logs,
   prompts, model output or secrets as telemetry.
4. SUBMITTED also includes `revision` (full 40/64 hex Git object ID) and `evidence`
   (up to 32 `{path,sha256}` references, relative portable paths and 64 hex digests).
   Player records references only. It does not execute Git, inspect evidence files,
   determine correctness or assert the revision exists remotely.

Lifecycle is persisted as lines arrive. Submission is staged in memory until the
process exits cleanly, so a crash, extra output or timeout cannot create a receipt.
The watchdog uses local elapsed time for process supervision only; it does not
enforce the contest deadline. Environment inheritance is restricted to OS execution
variables, with no provider credentials. Integrate existing Codex/team tooling through
the registered executable; the sample agent only demonstrates the protocol.

## Delivery and receipts

Authenticated events are keyed by eventId and payload digest. Exact completed replay
returns the existing result without invoking the agent. Reusing an eventId with a
different payload fails. A concurrent Player fails closed on the state lock. A crash
leaves RUNNING/PENDING; replay requires explicit `--retry` to avoid silently repeating
team side effects. Failed/blocked attempts likewise require explicit retry. Delivery
is at least once on recovery, so agents must deduplicate by eventId. All attempts
remain in the timeline. After an abrupt process kill, an operator must confirm the
old process and its children are stopped before removing a stale `.lock` directory.

Receipt: `{protocolVersion:1,receiptId,eventId,runId,ticketId,teamId,revision,evidence,
status:"PENDING_GAME_MASTER",localRecordedAt}`. `receiptId` is a deterministic SHA-256
of submission identity and references. LocalRecordedAt is diagnostic only. A local
receipt is neither official acceptance nor an official submission timestamp. No
score, deadline ruling or verified token count is produced locally.

## Reserved future adapters (not implemented)

- Outbound `poll(cursor)` / `stream(cursor)` -> signed events; durable `ack(eventId)`
  only after local persistence. Transport handles retry/backoff and server cursors.
- `submit(receiptId,runId,ticketId,revision,evidence)` -> signed server receipt with
  official timestamp and acceptance status. Retry by receiptId; retain local outbox
  until verified server acknowledgement. Server decides timing and revision validity.
- Gateway: organizer-issued runtime identity, approved model requests and usage
  receipts by run/agent/model/skill; no provider keys supplied to teams. Prompt content
  must not enter Player telemetry; usage is verified only by organizer receipts.
- Remote verification: revision/evidence request, coarse category and signed receipt
  response only. Player never receives implementation details of remote verification.
- Setup locking, credential custody, key rotation, signed lifecycle transport and
  process-tree containment require an organizer-managed deployment boundary.


## Match workspace roots

New matches clone into `TeamWorkspace/matches/<matchId>/` (`checkoutRoot`). The assigned source root is `checkoutRoot/repository.folder` (`matchRoot`). Preparation, game and recovery descriptors carry both absolute paths. Source, public challenge files, answers and evidence belong in matchRoot. Git-relative submissions must use `<repository.folder>/answers/<backlogId>/submit.json` and its evidence directory. Worker worktrees derive matchRoot from their own Git root and the same repository.folder. Init/reset affects only the selected match checkout; sibling matches are preserved. Saved legacy checkouts remain at their original paths. `challengeWorkspace` remains a compatibility alias for the checkout container, never the canonical answer root.
