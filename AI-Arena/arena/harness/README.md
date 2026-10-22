# Harness files

Project files that other agent harnesses expect in the run directory, copied in by `runner.py`
before the agent starts.

- `ruflo/`: output of `npx ruflo@latest init --codex --no-codex-detect --no-global --no-signup
  --no-skills-sh --skip-claude` (ruflo 3.59.9, 2026-10-22): its `AGENTS.md` (our item guide is
  appended after a `---`), `.agents/` (4 skills + config) and `.codex/`. The Ruflo MCP server is
  passed to `codex exec` through `codex_config` in `agents.json`, so the user's `~/.codex/config.toml`
  is never touched (`ruflo init` writes `[marketplaces.ruflo]`, `[plugins."ruflo-core@ruflo"]` and
  `[mcp_servers.ruflo]` there even with `--no-codex-detect`; revert them after running it by hand).
- Hermes needs no files: `runner.run_hermes` runs `hermes --in <run_dir> -z ...` and Hermes reads the
  run dir's `AGENTS.md` itself. It adopts the Codex CLI login from `~/.codex/auth.json`.
