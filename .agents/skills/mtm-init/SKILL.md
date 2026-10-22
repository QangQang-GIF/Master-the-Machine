---
name: mtm-init
description: Master the Machine foundational operating principles, token discipline, repo exploration rules, and worker contract boundaries.
---

# MTM-INIT: Foundational Agent Operating Policy

This skill establishes the baseline operational policy for all autonomous Codex workers participating in Master The Machine (MTM) 2026.

## 1. Core Operating Principles

1. **Deterministic Contracts**:
   - Every task execution is bounded by a `TaskEnvelope` and must conclude with a compliant `worker_result.json`.
   - Never replace deterministic test verification or git checks with natural language affirmations.

2. **Token & Context Economy (Lean2 Rules)**:
   - **Do not dump entire directory trees**: Use targeted `ripgrep` (`rg`) and file path lookups.
   - **Bounded reads**: Read only relevant function slices, never dump thousands of lines into context.
   - **Cap tool output**: Limit command outputs to ~2,000 tokens. If a test suite produces large logs, capture summary or tail output.
   - **Low verbosity**: Provide zero chit-chat or introductory narration. Execute the task directly.

3. **Minimal Surgical Changes**:
   - Implement the smallest, cleanest change that completely solves the issue and satisfies the acceptance criteria.
   - Do not refactor unrelated code, reformat styles, or update unrequested dependencies.

4. **Mandatory Verification**:
   - Run tests before concluding.
   - Check against Minimum Viable Solution (MVS) criteria when declared.
   - Record test outputs and exit codes.

5. **Strict Evidence Gathering**:
   - When evidence is declared in `evidence_required` (e.g. before/after screenshots, test logs), produce and save the artifact in the workspace.
   - Add the artifact path to `evidence_paths` in `worker_result.json`.

6. **Forbidden Actions**:
   - Never modify hidden benchmark tests or grading fixtures.
   - Never attempt to escape the designated workspace directory.
   - Never modify `.git/` internals directly or commit broken code to production branches.
