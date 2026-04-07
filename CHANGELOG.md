# Changelog

All notable changes to `claude-agent-mcp` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.2.0] — 2026-04-07

### Deployment track release

This release focuses entirely on deployment and operability.
No new workflow tools, profiles, or session semantics were added.
All v0.1 MCP tool contracts and response envelopes are unchanged.

### Added

- **Streamable HTTP transport** (`--transport streamable-http`) — exposes the
  same five MCP tools over HTTP using the MCP Streamable HTTP protocol
  (Starlette + uvicorn). Default bind: `127.0.0.1:8000`.
- **Transport abstraction** — `src/claude_agent_mcp/transports/` package with
  `stdio.py` and `streamable_http.py` bootstrap modules. Both share the same
  `build_server()` function; no runtime logic is duplicated per transport.
- **CLI flags** — `--transport`, `--host`, `--port`, `--version` added to the
  `claude-agent-mcp` entry point.
- **Config validation** — `Config.validate()` fails early and clearly on
  invalid transport names, out-of-range ports, or unrecognised log levels.
- **`CLAUDE_AGENT_MCP_` env prefix** — all configuration variables now have a
  canonical `CLAUDE_AGENT_MCP_` prefixed name. Legacy `CLAUDE_AGENT_` names
  still work as fallbacks.
- **New env vars**: `CLAUDE_AGENT_MCP_TRANSPORT`, `CLAUDE_AGENT_MCP_HOST`,
  `CLAUDE_AGENT_MCP_PORT`, `CLAUDE_AGENT_MCP_DB_PATH`,
  `CLAUDE_AGENT_MCP_ARTIFACT_DIR`.
- **Packaging** — `starlette>=0.40.0` and `uvicorn>=0.30.0` added as core
  dependencies. `httpx` added to dev extras.
- **Docs** — `docs/deployment.md`, `docs/transports.md`,
  `docs/troubleshooting.md`.
- **Tests** — `tests/test_transports.py` (13 tests), `tests/test_cli.py`
  (8 tests). Total test count: 81 (up from 60).

### Changed

- `server.py` — refactored `run()` into `run_stdio()` / `run_streamable_http()`
  with shared `_setup_runtime()`. Added `_build_parser()` for CLI arg parsing.
- `config.py` — added `transport`, `host`, `port` fields; added `validate()`;
  added `_env()` helper for primary/fallback env lookup.
- `__init__.py` — version bumped to `0.2.0`.
- `pyproject.toml` — version bumped to `0.2.0`; new dependencies added.
- `.env.example` — updated to document all `CLAUDE_AGENT_MCP_` variables.

### Security notes

- The `streamable-http` transport is **not authenticated**. Default bind is
  `127.0.0.1` (loopback-safe). Do not expose on a non-loopback interface
  without additional access control.
- This remains a single-operator, single-node, local-first system.

---

## [0.1.0] — 2026-04-07

### Initial release

- Sessioned Claude-backed agent runtime exposed over MCP (stdio transport)
- Durable SQLite-backed sessions with single-writer locking and crash recovery
- Five public MCP tools: `agent_run_task`, `agent_continue_session`,
  `agent_get_session`, `agent_list_sessions`, `agent_verify_task`
- Two built-in profiles: `general`, `verification`
- Canonical response envelope enforced on all mutating tools
- Local filesystem artifact storage
- Policy engine with working directory validation, turn caps, read-only rules
- Verification workflow with fail-closed behaviour and evidence-path validation
