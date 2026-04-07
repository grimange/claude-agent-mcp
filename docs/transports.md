# Transport Guide — claude-agent-mcp v0.2

## Overview

`claude-agent-mcp` supports two transport modes in v0.2:

| Transport | Protocol | When to use |
|---|---|---|
| `stdio` | stdin/stdout | MCP host integration (Claude Desktop, etc.) |
| `streamable-http` | HTTP + SSE streams | Local operator or programmatic deployment |

Both transports expose the same five MCP tools with identical contracts.
The runtime, policy enforcement, session persistence, and response envelopes
are unchanged regardless of transport.

---

## stdio transport

### How it works

The server reads JSON-RPC messages from stdin and writes responses to stdout.
This is the MCP specification's primary transport for local host integration.

### Start

```bash
claude-agent-mcp --transport stdio
# or simply:
claude-agent-mcp
```

### MCP host configuration (Claude Desktop example)

```json
{
  "mcpServers": {
    "claude-agent-mcp": {
      "command": "claude-agent-mcp",
      "args": ["--transport", "stdio"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

### Notes

- stdio is the default. If `CLAUDE_AGENT_MCP_TRANSPORT` is unset, stdio is used.
- There is no network exposure in stdio mode.

---

## streamable-http transport

### How it works

The server runs an HTTP server (via Starlette + uvicorn) that accepts MCP
requests on `POST /mcp`. Responses are returned as SSE streams. This follows
the MCP Streamable HTTP protocol specification.

### Start

```bash
claude-agent-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Or via environment:

```bash
CLAUDE_AGENT_MCP_TRANSPORT=streamable-http \
CLAUDE_AGENT_MCP_HOST=127.0.0.1 \
CLAUDE_AGENT_MCP_PORT=8000 \
claude-agent-mcp
```

### Endpoint

```
POST http://127.0.0.1:8000/mcp
```

All MCP JSON-RPC requests are sent to this endpoint.

### Session behaviour

The streamable-http transport maintains per-HTTP-client MCP sessions tracked
by `Mcp-Session-Id` headers. Sessions idle for more than 30 minutes are
automatically terminated.

### Validation — tool enumeration

After starting the server, you can verify it responds correctly with an MCP
`tools/list` request:

```bash
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' | head -20
```

Expected: a JSON-RPC response containing the five v0.1 tools.

### Security constraints

**Important**: The streamable-http transport is not authenticated.

- Default bind is `127.0.0.1` (loopback-safe). Do not change this without
  understanding the trust implications.
- Do not expose this port publicly without an authentication proxy in front.
- This transport is intended for single-operator, single-node, local deployment.
- It is not designed for multi-tenant or internet-facing use.

---

## Transport invariants (both modes)

Regardless of transport:

- All five v0.1 tools are available: `agent_run_task`, `agent_continue_session`,
  `agent_get_session`, `agent_list_sessions`, `agent_verify_task`
- All mutating tools return the canonical response envelope
- Session state is persisted in SQLite (durable across restarts)
- Policy enforcement runs before any execution
- Profile policies (`general`, `verification`) are enforced identically

---

## Choosing a transport

Use **stdio** when:
- Integrating with a MCP host (Claude Desktop, Claude Code, etc.)
- You want zero network exposure
- You don't need programmatic HTTP access

Use **streamable-http** when:
- You are running the server as a standalone process and connecting from a
  client application over the network
- You need multiple clients to share one server instance
- You are integrating with tooling that uses HTTP-based MCP clients

---

## Not yet supported (deferred)

The following transports are deferred beyond v0.2:

- SSE-only transport (separate from Streamable HTTP)
- WebSocket transport
- Multi-tenant hosted deployment

These are explicitly out of scope for the v0.2 deployment track.
