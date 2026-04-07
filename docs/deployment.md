# Deployment Guide — claude-agent-mcp v0.2

## Overview

`claude-agent-mcp` is a sessioned Claude-backed runtime exposed over MCP.

v0.2 supports two transport modes:

| Transport | Use case |
|---|---|
| `stdio` | MCP host integration (Claude Desktop, etc.) |
| `streamable-http` | Local operator deployment beyond stdio |

This document covers installation, configuration, and startup for both modes.

---

## Prerequisites

- Python ≥ 3.11
- An Anthropic API key (`ANTHROPIC_API_KEY`)
- SQLite (bundled with Python)

---

## Installation

### Editable install (development)

```bash
git clone <repo>
cd claude-agent-mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

### Normal install

```bash
pip install .
```

After installation, the `claude-agent-mcp` CLI is available.

---

## Quick start

Copy and edit the environment file:

```bash
cp .env.example .env
# Set ANTHROPIC_API_KEY at minimum
```

Start in stdio mode (default):

```bash
claude-agent-mcp
# or explicitly:
claude-agent-mcp --transport stdio
```

Start in network mode:

```bash
claude-agent-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

---

## Configuration

All configuration is driven by environment variables. See `.env.example` for the full list.

### Required

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API key |

### Transport

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_AGENT_MCP_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `CLAUDE_AGENT_MCP_HOST` | `127.0.0.1` | Bind host (network transport only) |
| `CLAUDE_AGENT_MCP_PORT` | `8000` | Bind port (network transport only) |

### Storage

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_AGENT_MCP_STATE_DIR` | `.state` | Root directory for all state |
| `CLAUDE_AGENT_MCP_DB_PATH` | `<state_dir>/claude-agent-mcp.db` | SQLite path override |
| `CLAUDE_AGENT_MCP_ARTIFACT_DIR` | `<state_dir>/artifacts` | Artifact directory override |

### Runtime

| Variable | Default | Description |
|---|---|---|
| `CLAUDE_AGENT_MCP_MODEL` | `claude-sonnet-4-6` | Claude model |
| `CLAUDE_AGENT_MCP_LOCK_TTL` | `300` | Session lock TTL in seconds |
| `CLAUDE_AGENT_MCP_ALLOWED_DIRS` | CWD | Comma-separated allowed working directories |
| `CLAUDE_AGENT_MCP_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |

### Legacy variable names

The following v0.1 variable names still work as fallbacks:
`CLAUDE_AGENT_STATE_DIR`, `CLAUDE_AGENT_MODEL`, `CLAUDE_AGENT_LOCK_TTL_SECONDS`,
`CLAUDE_AGENT_ALLOWED_DIRS`, `CLAUDE_AGENT_MAX_ARTIFACT_BYTES`, `CLAUDE_AGENT_LOG_LEVEL`

Prefer the `CLAUDE_AGENT_MCP_` prefix in new deployments.

---

## CLI reference

```
claude-agent-mcp [OPTIONS]

Options:
  --transport {stdio,streamable-http}   Transport mode (default: stdio)
  --host HOST                           Bind host for streamable-http
  --port PORT                           Bind port for streamable-http
  --version                             Print version and exit
  -h, --help                            Show help
```

CLI flags override the corresponding environment variables when both are set.

---

## Version check

```bash
claude-agent-mcp --version
# claude-agent-mcp 0.2.0
```

---

## State and data locations

By default, all durable state lands under `.state/` in the working directory:

```
.state/
  claude-agent-mcp.db   ← SQLite session database
  artifacts/            ← local artifact files
```

Override with `CLAUDE_AGENT_MCP_STATE_DIR` or the individual path overrides.

---

## Security notes

- The `streamable-http` transport is **not authenticated**. Do not expose it on a
  non-loopback interface without additional access control.
- The default bind address is `127.0.0.1` (loopback-safe).
- This system is designed for single-operator, local-first use.
- It is not a multi-tenant hosted service.

---

## Smoke validation

Run a quick health check after install:

```bash
# 1. Verify the CLI starts
claude-agent-mcp --version

# 2. Verify tests pass
pytest tests/ -q

# 3. Verify the MCP tools are registered (requires a running server)
# See docs/transports.md for transport-specific validation steps.
```
