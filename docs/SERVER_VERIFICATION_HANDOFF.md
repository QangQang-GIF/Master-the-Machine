# Server verification handoff — Codex / Antigravity

**Date:** 2026-10-25
**Status:** PENDING LIVE SERVER VALIDATION. No server-side PASS is claimed.
**Scope:** Multi-backend agent-profile, source-knowledge, training and safety upgrade.

## Prerequisites

Isolated staging server, Python 3.12+, working `codex` and `agy` CLIs with authenticated access, authorised Game Master **staging** credentials, Git test remote, model credits and restricted workspaces. Never commit credentials. Record exact commit, CLI versions, container image and redacted logs.

## SV-01: Full offline regression

```bash
python -m pip install -e '.[dev]'
python -m pytest tests/ -q -m 'not live_cli and not staging_game_master'
python -m pytest tests/test_agent_upgrade.py tests/test_agent_upgrade_integration.py -q
```

PASS only with zero failures. Skips and version details must be recorded.

## SV-02: Both real CLI backends

```bash
command -v codex && codex --version
command -v agy && agy --version
```

Confirm actual authenticated models and effort settings for each backend; configured model names are **not** proof of availability. Test each backend pinned separately, not only whichever CLI is found first.

## SV-03: Native custom-agent config smoke

```bash
python -c "from mtm_agents import export_native_agents; print(*export_native_agents('/tmp/mtm-agent-profiles'), sep='\n')"
```

Test generated Codex `.codex/agents/*.toml` and Antigravity `.agents/agents/*.md` in an isolated disposable repo. Record whether noninteractive native subagent delegation, parent-child output collection, cancellation, structured traces, quotas and permissions actually work. Native delegation is **not** wired into production; failure means retain isolated process fallback.

## SV-04: Skills and opt-in knowledge context

```bash
MTM_LIVE_CLI=1 python -m pytest tests/test_smoke_worker.py -m live_cli -q
MTM_AGENT_CONTEXT_ENABLED=1 MTM_LIVE_CLI=1 python -m pytest tests/test_smoke_worker.py -m live_cli -q
```

Run backend-specific pinned variants for **both** CLIs. Verify TASK.md contains actual trusted SKILL.md excerpts and correct indexed repo paths; ensure no customer secrets or leaked hidden tests. Current knowledge index is a bounded in-memory source map; persistent incremental indexing is pending.

## SV-05: Game Master staging

```bash
python -m pytest tests/e2e/test_e2e_d_staging_gm.py -m staging_game_master -q
```

Do not use a real match to test duplicate submissions. Verify MVS unlock, server time, 429/5xx handling, ACCEPTED→PASSED reconciliation, crash/restart, exactly-once submit intent and remote SHA. A skipped test means NOT TESTED.

## SV-06: Production verification-command compatibility

```bash
MTM_ENV=production python -m pytest tests/test_agent_upgrade_integration.py -q
```

Test actual per-stack and official MVS commands against shell-free command policy. Unsupported legitimate commands need reviewed templates. Test malicious shell operators, timeout and forbidden interpreter execution; no untrusted command may run on canonical repo.

## SV-07: Git integration and 40-minute contest simulator

Run on a disposable remote. Inject concurrent edits, process cancellation, network interruption after push and failed verification. Assert no overwritten changes or duplicate submissions. Compare Codex vs Antigravity across repeated paired 40-minute simulations: confirmed PASSED points, tokens/credits, median/P95 latency and PI response time.

## SV-08: DEV learning and freeze

Create matched held-out evaluation records; run `evaluate_paired_runs`, then `propose_from_failures` and inspect proposed diffs without altering live SKILL.md. Use promotion only after independent review. Confirm production skill checksums and rollback.

## Remaining implementation work

| ID | Remaining task | Requires server/proof |
|---|---|---|
| R-01 | Real headless native subagent dispatcher for both backends | SV-03 |
| R-02 | Persistent incremental source index/revision invalidation | SV-04 performance |
| R-03 | Backend-pinned fixture E2Es + normalized credit accounting | SV-02 / SV-04 |
| R-04 | Three-way conflict-aware patch integration | SV-07 |
| R-05 | Full offline training dataset/A-B automation and promotion pipeline | SV-08 |
| R-06 | Expand per-stack safe verification templates | SV-06 |
| R-07 | Replace Antigravity broad permission bypass with policy-aware sandbox | SV-02 |
| R-08 | Official Game Master staging E2E sign-off | SV-05 |
| R-09 | Real process pause/resume deadline behavior | SV-02 |
| R-10 | Pin profile/context hashes in release manifest | SV-04 / SV-08 |

## Sign-off template

For each SV task record: Git SHA; UTC timestamp; operator; server OS/container; backend binary/version; model; command; redacted log and trace; outcome **PASS / FAIL / BLOCKED**; reviewer; next action.

**Release policy:** Keep `MTM_AGENT_CONTEXT_ENABLED` disabled in live competition until SV-04 is green. Do not claim native multi-agent parity before SV-03. Maintain the known-good isolated CLI backend as rollback.
