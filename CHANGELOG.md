# Changelog

All notable changes to `claude-agent-mcp` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.3.0] — 2026-04-07

### Federation track release

This release adds governed downstream MCP federation.
No new public workflow tools, profiles, or session semantics were added.
All v0.2 MCP tool contracts and response envelopes are unchanged.

### Added

- **Downstream MCP federation** — operators can statically configure downstream
  MCP servers that expose a controlled subset of tools to Claude-backed sessions.
  Federation is **disabled by default** (`CLAUDE_AGENT_MCP_FEDERATION_ENABLED=false`).
- **`federation/` package** — new internal module:
  - `registry.py` — `DownstreamRegistry` loads and validates downstream server
    configs from a JSON file; fails early on invalid config.
  - `connections.py` — `DownstreamConnectionManager` connects to enabled downstream
    servers at startup and discovers their tools.
  - `catalog.py` — `ToolCatalog` normalises discovered tools into collision-safe
    names (`{server_name}__{tool_name}`) and applies allowlist filtering.
  - `visibility.py` — `ToolVisibilityResolver` gates tool exposure by active profile.
  - `invoker.py` — `DownstreamToolInvoker` validates tool selection, executes via
    the connection layer, normalises results, and records session audit events.
  - `models.py` — `DownstreamServerConfig`, `DiscoveredTool`, `DownstreamToolCallResult`.
  - `__init__.py` — `FederationManager` startup coordinator.
- **New config fields** — `CLAUDE_AGENT_MCP_FEDERATION_ENABLED` and
  `CLAUDE_AGENT_MCP_FEDERATION_CONFIG` (path to JSON federation config file).
- **Tool-use loop** — `ClaudeAdapter.run_with_tools()` implements the Anthropic
  Messages API tool-use loop for federation-enabled sessions.
- **Federation session events** — three new `EventType` values:
  `downstream_tool_catalog_resolved`, `downstream_tool_invocation`,
  `downstream_tool_result`. Invocation events record input keys only (not values).
- **Federation error taxonomy** — six new error classes: `DownstreamServerConfigError`,
  `DownstreamDiscoveryError`, `DownstreamToolNotAllowedError`,
  `DownstreamToolNotVisibleError`, `DownstreamInvocationError`,
  `DownstreamSchemaValidationError`.
- **Docs** — `docs/federation.md` (operator guide), `docs/downstream-tool-policy.md`
  (four-gate policy model reference).
- **Tests** — `tests/test_federation_registry.py` (22 tests),
  `tests/test_federation_visibility.py` (17 tests),
  `tests/test_federation_invocation.py` (12 tests).
  Total test count: 132 (up from 81).

### Changed

- `server.py` — `_setup_runtime()` initialises `FederationManager` at startup;
  version bumped to `0.3.0`.
- `workflow_executor.py` — accepts optional `visibility_resolver` and
  `federation_server_configs`; `run_task` and `continue_session` use
  `run_with_tools` when federation tools are visible for the active profile.
- `agent_adapter.py` — added `run_with_tools()` method.
- `pyproject.toml` — version bumped to `0.3.0`.

### Federation invariants enforced

- A tool must pass four gates to be callable: server enabled → discovered →
  allowlisted → profile permitted. All gates are enforced in code.
- No wildcard or passthrough allowlist mode exists.
- The `verification` profile has no downstream tool access by default.
- Discovery failures for individual servers are logged and skipped; startup is
  not aborted.
- All downstream invocations flow through the bounded invoker layer.

### Security notes

- Federation expands the trust boundary. It is intended for trusted
  operator-controlled environments only.
- The `streamable-http` transport remains unauthenticated. Federation does not
  change this — do not expose on a public interface without additional controls.
- Downstream servers run as subprocesses and inherit the process environment.

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
