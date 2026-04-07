# Changelog

All notable changes to `claude-agent-mcp` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [0.5.0] — 2026-04-07

### Claude Code stabilization track release

This release hardens the `claude_code` execution backend introduced in v0.4.
No new public MCP tools, profiles, or session semantics were added.
All v0.4 MCP tool contracts and response envelopes are unchanged.
The `api` backend is unaffected and does not regress.

### Added

- **`BackendCapabilities` dataclass** (`backends/base.py`) — frozen dataclass
  declaring six capability flags per backend: `supports_downstream_tools`,
  `supports_structured_tool_use`, `supports_native_multiturn`,
  `supports_rich_stop_reason`, `supports_structured_messages`,
  `supports_workspace_assumptions`. Used internally by the workflow executor
  to emit warnings and suppress unsupported forwarding paths.
- **`capabilities` property on `ExecutionBackend`** — abstract property added
  to the base interface; both backends implement it explicitly.
- **`session_summary` parameter on `execute()`** — optional kwarg added to the
  `ExecutionBackend.execute()` signature. The `claude_code` backend embeds the
  summary in the structured prompt; the `api` backend ignores it.
- **Structured prompt builder** (`claude_code_backend.py`) — replaces the
  previous flat plain-text history serialization. Produces a five-section
  structured prompt: `[System]`, `[Session Context]`, `[Conversation History]`,
  `[Current Request]`, `[Instructions]`. Section boundaries are visually distinct.
- **Deterministic history truncation** — the `claude_code` backend keeps the
  most recent **10 user/assistant exchange pairs** (configurable via
  `HISTORY_MAX_EXCHANGES`). Individual message content is capped at **2000
  characters** (`CONTENT_MAX_CHARS`). Truncated content is marked `[truncated]`.
- **`backend_defaulted` stop reason** — the `claude_code` backend now honestly
  reports `stop_reason: backend_defaulted` instead of the misleading `end_turn`.
- **Capability-aware workflow executor** — `WorkflowExecutor` checks
  `backend.capabilities.supports_downstream_tools` before building the tool
  invoker. If tools are resolved but the backend cannot forward them, a warning
  is emitted to the response envelope and a session event is recorded — no
  silent discard.
- **Session summary passed to backend** — `continue_session` now passes
  `session.summary_latest` as `session_summary` to `backend.execute()`, enabling
  the `claude_code` backend to include it in the `[Session Context]` section.
- **Docs** — `docs/backend-capability-matrix.md` (new) with full capability
  flag definitions and per-flag operator guidance. `docs/claude-code-backend.md`
  and `docs/execution-backends.md` updated to v0.5 with truncation policy,
  warning reference, and expanded troubleshooting.
- **Tests** — 20 new tests in `tests/test_backends.py` across four new test
  classes: `TestBackendCapabilities`, `TestClaudeCodePromptBuilder`,
  `TestClaudeCodeNormalizationV5`, `TestApiBackendV5Compatibility`.
  Total test count: 187 (up from 167).

### Changed

- `backends/claude_code_backend.py` — `_build_prompt()` preserved as a
  backwards-compatible alias for `_build_structured_prompt()`. Internal
  prompt construction fully replaced with the structured builder.
- `backends/api_backend.py` — `execute()` accepts the new `session_summary`
  kwarg (ignored). `capabilities` property added declaring full API support.
- `backends/base.py` — `BackendCapabilities` dataclass added;
  `ExecutionBackend.execute()` signature extended with `session_summary`;
  `capabilities` abstract property added.
- `runtime/workflow_executor.py` — `run_task` and `continue_session` updated
  to check backend capabilities before tool forwarding and to pass
  `session_summary` to `execute()`.
- `tests/test_backends.py` — existing `test_execute_returns_normalized_result`
  updated to assert `stop_reason == "backend_defaulted"` (was `"end_turn"`).

### Warnings now surfaced by `claude_code` backend

| Condition | Appears in `warnings` field |
|---|---|
| Downstream tools resolved but not forwarded | Yes — advises `api` backend |
| History truncated beyond exchange limit | Yes — states count kept |
| Stop-reason precision limited | Yes — always present |
| Empty CLI response | Yes |

### Backend capability declarations (v0.5)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_downstream_tools` | Yes | No |
| `supports_structured_tool_use` | Yes | No |
| `supports_native_multiturn` | Yes | No |
| `supports_rich_stop_reason` | Yes | No |
| `supports_structured_messages` | Yes | No |
| `supports_workspace_assumptions` | No | Yes |

### Known limitations (claude_code backend, v0.5)

- Single-turn per CLI invocation; no native multi-turn tool-use loop.
- Downstream federation tools are not forwarded (warning emitted).
- History is reconstructed as structured labeled text, not Messages API objects.
- `stop_reason` is always `backend_defaulted`.

---

## [0.4.0] — 2026-04-07

### Execution backend track release

This release introduces pluggable execution backend support.
Operators can now choose between the Anthropic API and Claude Code as the execution substrate.
No new public workflow tools, profiles, or session semantics were added.
All v0.3 MCP tool contracts and response envelopes are unchanged.

### Added

- **Execution backend abstraction** — new `backends/` package introducing a formal
  `ExecutionBackend` interface. Backends are pluggable execution substrates; the
  workflow executor, session model, policy engine, and MCP surface remain unchanged.
- **`backends/base.py`** — `ExecutionBackend` ABC with `name`, `validate_startup()`,
  `is_available()`, and a unified `execute()` method covering both fresh tasks and
  session continuations.
- **`backends/registry.py`** — `BackendRegistry` for explicit named backend selection.
  Unknown backend names fail clearly; there is no silent fallback between backends.
- **`backends/api_backend.py`** — `ApiExecutionBackend`, the default backend.
  Wraps the existing `ClaudeAdapter` with no behavior change. Authenticated via
  `ANTHROPIC_API_KEY`. Fails at startup if the key is absent.
- **`backends/claude_code_backend.py`** — `ClaudeCodeExecutionBackend`, a CLI-backed
  backend that executes tasks via `claude --print <prompt>` as a subprocess.
  Authenticated via Claude Code's own login state (`claude login`), not API keys.
  Startup validation confirms the CLI is present and executable. Does not forward
  federation tools in v0.4 (warning included in response).
- **Backend selection config** — `CLAUDE_AGENT_MCP_EXECUTION_BACKEND` env var.
  Supported values: `api` (default), `claude_code`. Unknown values cause a startup
  failure with a clear error.
- **Claude Code backend config** — `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH` (optional
  path to CLI binary) and `CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT` (seconds, default 300).
- **Backend error taxonomy** — five new error classes: `ExecutionBackendConfigError`,
  `ExecutionBackendUnavailableError`, `ExecutionBackendAuthError`,
  `ClaudeCodeUnavailableError`, `ClaudeCodeInvocationError`.
- **Docs** — `docs/execution-backends.md` (backend overview, selection, and error
  reference) and `docs/claude-code-backend.md` (Claude Code backend setup, limitations,
  and troubleshooting).
- **Tests** — `tests/test_backends.py` (35 tests covering registry, config validation,
  API backend startup and routing, Claude Code backend startup and execution, and the
  `build_backend` factory). Total test count: 167 (up from 132).

### Changed

- `workflow_executor.py` — `WorkflowExecutor` now accepts `execution_backend:
  ExecutionBackend` (replaces `agent_adapter: ClaudeAdapter`). All adapter calls
  replaced with `self._backend.execute()`. No behavioral change for the `api` backend.
- `server.py` — `_setup_runtime()` calls `build_backend(config)` to resolve the
  configured backend; logs the selected backend name at startup. Version bumped to `0.4.0`.
- `config.py` — added `execution_backend`, `claude_code_cli_path`, and
  `claude_code_timeout_seconds` fields; `validate()` now rejects unknown backend names.
- `conftest.py` — `mock_adapter` fixture updated to mock `ExecutionBackend.execute`;
  `executor` fixture updated to use `execution_backend=` parameter.

### Backend invariants enforced

- Backend selection is explicit and validated at startup — no magic inference.
- API credentials (`ANTHROPIC_API_KEY`) and Claude Code login state are separate auth
  models; the Claude Code backend does not use `ANTHROPIC_API_KEY`.
- Backends receive the already-filtered visible tool set — they do not own federation
  policy or allowlist logic.
- Internal session identity (`session_id`) remains the canonical identifier regardless
  of backend. Backend metadata is stored in the session `provider` field for
  observability only.
- Canonical `AgentResponse` envelope shape is unchanged across all backends.

### Known limitations (claude_code backend, v0.4)

- Single-turn execution only; no native multi-turn tool-use loop.
- Downstream federation tools are not forwarded to the CLI.
- Conversation history is serialised as plain text in the prompt.
- `stop_reason` is always reported as `end_turn`.

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
