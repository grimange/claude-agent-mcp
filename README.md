# claude-agent-mcp

`claude-agent-mcp` is an MCP server that exposes Claude-backed task and session tooling to any MCP client. Install it from PyPI, register it with your MCP client (such as OpenAI Codex), and your client gains access to durable, policy-bounded Claude sessions through a stable, consistent tool interface.

Sessions persist in SQLite across server restarts. Both the Anthropic API and the Claude Code CLI are supported as execution backends. All tool responses share a single normalized response envelope regardless of which backend is active.

**v1.1.0 · Python 3.11+ · stdio transport · SQLite persistence**

---

## Features

- **Durable sessions** — sessions persist in SQLite and survive process restarts
- **Two execution backends** — Anthropic API (`api`) or Claude Code CLI (`claude_code`)
- **Stable MCP tool contracts** — consistent request/response schemas across backends
- **Session continuation** — resume any past session from any MCP client with full context
- **Policy-bounded execution** — profiles control permissions, turn limits, and directory access
- **Structured verification** — evidence-based evaluation with fail-closed semantics
- **Runtime status inspection** — confirm active configuration without side effects
- **APNTalk verification mode** — server-level restricted surface: verification-only, advisory-only, machine-verifiable, fail-closed (v1.1.0)
- **Optional downstream federation** — expose other MCP server tools to Claude under explicit operator allowlists

---

## Installation

```bash
pip install claude-agent-mcp
```

Python 3.11 or later is required. The `claude-agent-mcp` command is available immediately after install.

---

## Backend options

`claude-agent-mcp` supports two execution backends. Choose based on what credentials you have available.

| Backend | Requires | Notes |
|---------|----------|-------|
| `api` *(default)* | `ANTHROPIC_API_KEY` | Full multi-turn support via Anthropic Messages API |
| `claude_code` | `claude` CLI + `claude login` | No separate API key needed; uses your Claude Code session |

Both backends expose the same MCP tools, session model, and response envelopes. Backend selection controls only how Claude executes tasks internally.

**Start with `api`** unless you already have Claude Code installed and prefer not to manage a separate API key.

Unknown backend names fail at startup with a clear error. There is no silent fallback between backends.

---

## Quick start

### API backend

```bash
pip install claude-agent-mcp
export ANTHROPIC_API_KEY=sk-ant-...
claude-agent-mcp
```

Your MCP client can now connect to the server over stdio.

### Claude Code backend

```bash
pip install claude-agent-mcp
claude login           # authenticate once if not already done
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
claude-agent-mcp
```

No API key required. Authentication comes from your existing Claude Code session.

### APNTalk verification mode (v1.1.0)

APNTalk mode restricts the server to a two-tool verification surface at the server level — not downstream filtering:

```bash
pip install claude-agent-mcp
claude login
export CLAUDE_AGENT_MCP_MODE=apntalk_verification
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
export CLAUDE_AGENT_MCP_ALLOWED_DIRS=/path/to/bounded/scope
claude-agent-mcp
```

In this mode the server publishes only `agent_get_runtime_status` and `agent_verify_task`. All other tools are absent from MCP introspection. Startup fails if the contract cannot be fully satisfied. See [docs/operator-guide.md — APNTalk verification mode](docs/operator-guide.md#12-apntalk-verification-mode-v110) for full details.

---

## Using with Codex

`claude-agent-mcp` works as an MCP server in OpenAI Codex. Add it to `~/.codex/config.toml`:

**API backend:**

```toml
[mcp_servers.claude-agent-mcp]
command = "claude-agent-mcp"

[mcp_servers.claude-agent-mcp.env]
ANTHROPIC_API_KEY = "sk-ant-..."
```

**Claude Code backend:**

```toml
[mcp_servers.claude-agent-mcp]
command = "claude-agent-mcp"

[mcp_servers.claude-agent-mcp.env]
CLAUDE_AGENT_MCP_EXECUTION_BACKEND = "claude_code"
```

Once registered, Codex can call all `claude-agent-mcp` tools during task execution.

For full setup instructions, verification steps, and troubleshooting see **[docs/codex-setup.md](https://github.com/grimange/claude-agent-mcp/blob/main/docs/codex-setup.md)**.

---

## Available tools

| Tool | Description |
|------|-------------|
| `agent_run_task` | Run a new Claude-backed task in a durable session |
| `agent_continue_session` | Continue an existing session with a follow-up message |
| `agent_get_session` | Get details for a session by ID |
| `agent_list_sessions` | List recent sessions with optional status filter |
| `agent_verify_task` | Run a structured, fail-closed verification workflow |
| `agent_get_runtime_status` | Inspect active backend, profiles, and resolved configuration |

All tools return a normalized response envelope:

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "...",
  "result": {},
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

---

## Tool examples

### Run a task

```json
{
  "task": "Summarize the files in this directory",
  "system_profile": "general",
  "working_directory": "/path/to/project",
  "max_turns": 5
}
```

### Continue a session

```json
{
  "session_id": "sess_abc123",
  "message": "Can you expand on point 3?"
}
```

### Run a structured verification

```json
{
  "task": "Verify the implementation matches the spec",
  "scope": "authentication module",
  "evidence_paths": ["/path/to/spec.md", "/path/to/impl.py"],
  "fail_closed": true
}
```

Returns a verdict: `pass`, `pass_with_restrictions`, `fail_closed`, or `insufficient_evidence`.

---

## Execution profiles

### `general`

Bounded general task execution. Read/write access. Up to 50 turns maximum.

### `verification`

Evidence-based evaluation. Read-only. Fail-closed by default. Up to 20 turns maximum.

---

## Verification

Check the server is installed and operational:

```bash
claude-agent-mcp --version
```

From any connected MCP client, call `agent_get_runtime_status` with an empty request `{}`. A healthy response includes `"ok": true`, the active backend name, and the active profile list. This call has no side effects and is safe to use as a readiness check.

---

## Limitations

These are the current boundaries of the v1.1.0 release.

- **No in-flight cancellation** — sessions run to completion or timeout; there is no cancel signal
- **No artifact browsing tools** — artifact read and list tools are not yet exposed via MCP
- **Single-node only** — designed for single-operator, local-first use; no distributed workers or multi-tenant hosting
- **Claude Code backend: single-shot CLI** — each turn invokes the `claude` CLI individually; there is no native tool-use loop. Execution mediation uses runtime-governed text detection, not native tool calling
- **Mediated execution requires active federation** — without federation configured, all mediated action requests are rejected with `rejected:federation_inactive`
- **Streamable HTTP transport is unauthenticated** — do not expose on a non-loopback address without additional access control

Start with the simplest configuration that meets your needs. Enable federation, mediation, or advanced features only after confirming the basic flow works.

---

## Configuration reference

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_MODE` | `standard` | Runtime mode: `standard` or `apntalk_verification` |
| `CLAUDE_AGENT_MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `streamable-http` |
| `CLAUDE_AGENT_MCP_HOST` | `127.0.0.1` | Bind host (streamable-http only) |
| `CLAUDE_AGENT_MCP_PORT` | `8000` | Bind port (streamable-http only) |
| `CLAUDE_AGENT_MCP_STATE_DIR` | `.state` | State storage root |
| `CLAUDE_AGENT_MCP_MODEL` | `claude-sonnet-4-6` | Claude model |
| `CLAUDE_AGENT_MCP_LOCK_TTL` | `300` | Session lock TTL in seconds |
| `CLAUDE_AGENT_MCP_ALLOWED_DIRS` | CWD | Comma-separated allowed working directories |
| `CLAUDE_AGENT_MCP_MAX_ARTIFACT_BYTES` | `10485760` | Max artifact size (10 MB) |
| `CLAUDE_AGENT_MCP_LOG_LEVEL` | `INFO` | Log level |

### Execution backend

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_EXECUTION_BACKEND` | `api` | Backend: `api` or `claude_code` |
| `ANTHROPIC_API_KEY` | — | Required for `api` backend |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH` | — | Override path to `claude` binary |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT` | `300` | CLI timeout in seconds |

### Federation (optional, disabled by default)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_FEDERATION_ENABLED` | `false` | Enable downstream MCP federation |
| `CLAUDE_AGENT_MCP_FEDERATION_CONFIG` | — | Path to federation JSON config |

All downstream tools require explicit allowlisting. See [`docs/federation.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/federation.md).

### Claude Code backend: advanced options

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS` | `5` | Max turns included in continuation context |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION` | `false` | Enable runtime-mediated execution |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_ACTIONS_PER_TURN` | `1` | Single-action limit per turn |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_WORKFLOW_STEPS` | `1` | Max steps per bounded workflow |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_TOOLS` | all visible | Tool allowlist — empty = permit all |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_DENIED_MEDIATED_TOOLS` | none | Tool denylist — always blocked |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_SESSION_MEDIATED_APPROVALS` | `100` | Session-level approval cap |

See [`docs/claude-code-backend.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/claude-code-backend.md) for the full mediation reference.

---

## Other MCP clients

For clients that use JSON-format MCP configuration (such as Claude Desktop):

```json
{
  "mcpServers": {
    "claude-agent-mcp": {
      "command": "claude-agent-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

---

## State storage

```
.state/
  claude-agent-mcp.db      # SQLite: sessions, events, artifact metadata
  artifacts/
    <session_id>/          # Per-session artifact files
```

Override the root with `CLAUDE_AGENT_MCP_STATE_DIR`.

---

## Documentation

| Doc | Description |
|-----|-------------|
| [`docs/codex-setup.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/codex-setup.md) | Full Codex MCP setup guide |
| [`docs/execution-backends.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/execution-backends.md) | Backend comparison and selection |
| [`docs/claude-code-backend.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/claude-code-backend.md) | Claude Code backend: continuation, mediation, troubleshooting |
| [`docs/backend-capability-matrix.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/backend-capability-matrix.md) | Complete capability comparison |
| [`docs/transports.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/transports.md) | Transport configuration |
| [`docs/deployment.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/deployment.md) | General deployment guide |
| [`docs/federation.md`](https://github.com/grimange/claude-agent-mcp/blob/main/docs/federation.md) | Downstream federation operator guide |
| [`CHANGELOG.md`](https://github.com/grimange/claude-agent-mcp/blob/main/CHANGELOG.md) | Version history |

---

## Development

To run from source or contribute:

```bash
git clone <repo>
cd claude-agent-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY or CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
```

Run tests:

```bash
pytest tests/ -v
```

457 tests covering sessions, policy enforcement, tool contracts, transports, federation, backends, verification, continuation context, and execution mediation.
