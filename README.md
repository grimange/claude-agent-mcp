# claude-agent-mcp

A sessioned Claude-backed agent runtime exposed over MCP (Model Context Protocol).

**Current version: v0.4.0**

## Overview

`claude-agent-mcp` is a local-first MCP server that provides:

- **Durable sessions** — sessions persist in SQLite and survive process restarts
- **Pluggable execution backends** — choose between Anthropic API or Claude Code CLI
- **Stable MCP tool contracts** — predictable request/response schemas
- **Policy-bounded execution** — profiles control permissions, turn limits, and behavior
- **Structured verification** — evidence-based evaluation with fail-closed semantics
- **Governed federation** — optional downstream MCP tool access with allowlist control

Single-node, operator-controlled, local-first.

---

## Tool surface

| Tool | Description |
|------|-------------|
| `agent_run_task` | Run a new Claude-backed task session |
| `agent_continue_session` | Continue an existing session |
| `agent_get_session` | Get session detail by ID |
| `agent_list_sessions` | List recent sessions |
| `agent_verify_task` | Run a structured verification workflow |

---

## Setup

### Requirements

- Python 3.11+
- One of:
  - Anthropic API key (`api` backend, default)
  - Claude Code installed and authenticated (`claude_code` backend)

### Install

```bash
git clone <repo>
cd claude-agent-mcp
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env — set ANTHROPIC_API_KEY for the api backend,
# or set CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code for the Claude Code backend
```

### Run

```bash
# stdio (default)
claude-agent-mcp

# Streamable HTTP
claude-agent-mcp --transport streamable-http --port 8000
```

---

## MCP client configuration

### stdio (default)

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

### Claude Code backend (no API key required)

```json
{
  "mcpServers": {
    "claude-agent-mcp": {
      "command": "claude-agent-mcp",
      "env": {
        "CLAUDE_AGENT_MCP_EXECUTION_BACKEND": "claude_code"
      }
    }
  }
}
```

Requires `claude` CLI to be installed and authenticated via `claude login`.

---

## Tool usage

### agent_run_task

```json
{
  "task": "Summarize the files in this directory",
  "system_profile": "general",
  "working_directory": "/path/to/project",
  "max_turns": 5
}
```

Response:

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "Task completed...",
  "result": { "output_text": "..." },
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

### agent_continue_session

```json
{
  "session_id": "sess_abc123",
  "message": "Can you expand on point 3?"
}
```

### agent_get_session

```json
{
  "session_id": "sess_abc123"
}
```

### agent_list_sessions

```json
{
  "limit": 10,
  "status": "completed"
}
```

### agent_verify_task

```json
{
  "task": "Verify that the implementation matches the spec",
  "scope": "authentication module",
  "evidence_paths": ["/path/to/spec.md", "/path/to/impl.py"],
  "fail_closed": true
}
```

Returns a verdict: `pass`, `pass_with_restrictions`, `fail_closed`, or `insufficient_evidence`.

---

## Profiles

### `general`

Bounded general task execution. Read/write access. Up to 50 turns max.

### `verification`

Evidence-based evaluation. Read-only. Fail-closed by default. Up to 20 turns max.

---

## Execution backends

v0.4 introduces a formal backend abstraction. The execution backend controls how Claude tasks run internally. MCP contracts, sessions, policies, and response envelopes are the same regardless of backend.

| Backend | Auth | Description |
|---------|------|-------------|
| `api` (default) | `ANTHROPIC_API_KEY` | Anthropic Messages API |
| `claude_code` | `claude login` | Claude Code CLI (`claude --print`) |

Select via:

```bash
CLAUDE_AGENT_MCP_EXECUTION_BACKEND=api         # default
CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
```

Unknown backend names fail at startup with a clear error. There is no silent fallback between backends.

See [`docs/execution-backends.md`](docs/execution-backends.md) and [`docs/claude-code-backend.md`](docs/claude-code-backend.md) for full details.

---

## Transports

| Transport | Flag | Description |
|-----------|------|-------------|
| `stdio` | `--transport stdio` (default) | For MCP client integration |
| `streamable-http` | `--transport streamable-http` | HTTP endpoint on `127.0.0.1:8000` |

See [`docs/transports.md`](docs/transports.md) for HTTP transport setup.

---

## Federation (optional)

Downstream MCP server tools can be made available to Claude-backed sessions. Disabled by default.

```bash
CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true
CLAUDE_AGENT_MCP_FEDERATION_CONFIG=/path/to/federation.json
```

All downstream tools require explicit allowlisting. No wildcard or passthrough mode exists. See [`docs/federation.md`](docs/federation.md).

---

## State storage

```
.state/
  claude-agent-mcp.db      # SQLite: sessions, events, artifacts metadata
  artifacts/
    <session_id>/          # Per-session artifact files
```

---

## Configuration reference

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_TRANSPORT` | `stdio` | Transport: `stdio` or `streamable-http` |
| `CLAUDE_AGENT_MCP_HOST` | `127.0.0.1` | Bind host (streamable-http) |
| `CLAUDE_AGENT_MCP_PORT` | `8000` | Bind port (streamable-http) |
| `CLAUDE_AGENT_MCP_STATE_DIR` | `.state` | State storage root |
| `CLAUDE_AGENT_MCP_MODEL` | `claude-sonnet-4-6` | Claude model |
| `CLAUDE_AGENT_MCP_LOCK_TTL` | `300` | Session lock TTL (seconds) |
| `CLAUDE_AGENT_MCP_ALLOWED_DIRS` | CWD | Comma-separated allowed working directories |
| `CLAUDE_AGENT_MCP_MAX_ARTIFACT_BYTES` | `10485760` | Max artifact size (10 MB) |
| `CLAUDE_AGENT_MCP_LOG_LEVEL` | `INFO` | Log level |

### Execution backend (v0.4)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_EXECUTION_BACKEND` | `api` | Backend: `api` or `claude_code` |
| `ANTHROPIC_API_KEY` | — | Required for `api` backend |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH` | — | Path to `claude` binary (optional) |
| `CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT` | `300` | CLI timeout in seconds |

### Federation (v0.3)

| Variable | Default | Description |
|----------|---------|-------------|
| `CLAUDE_AGENT_MCP_FEDERATION_ENABLED` | `false` | Enable downstream federation |
| `CLAUDE_AGENT_MCP_FEDERATION_CONFIG` | — | Path to federation JSON config |

---

## Tests

```bash
pytest tests/ -v
```

167 tests across sessions, policy, tools, transports, federation, backends, and verification.

---

## Known limitations

- **No cancellation** — in-flight sessions cannot be cancelled
- **No public artifact browsing** — artifact read/list tools are deferred
- **Single-node only** — no distributed workers or multi-tenant hosting
- **Claude Code backend: single-turn** — no native tool-use loop; federation tools not forwarded
- **Streamable HTTP: unauthenticated** — do not expose on a non-loopback interface without additional access control

---

## Documentation

| Doc | Description |
|-----|-------------|
| [`docs/execution-backends.md`](docs/execution-backends.md) | Backend selection, API mode, Claude Code mode |
| [`docs/claude-code-backend.md`](docs/claude-code-backend.md) | Claude Code backend setup and troubleshooting |
| [`docs/transports.md`](docs/transports.md) | Transport configuration |
| [`docs/deployment.md`](docs/deployment.md) | Deployment guide |
| [`docs/federation.md`](docs/federation.md) | Downstream federation operator guide |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |
