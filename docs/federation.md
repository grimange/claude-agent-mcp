# Federation Guide — claude-agent-mcp v0.3

## Overview

v0.3 adds **governed downstream MCP federation**: the ability to connect
`claude-agent-mcp` to explicitly configured downstream MCP servers and expose
a controlled subset of their tools to Claude-backed sessions.

Federation is:
- **Opt-in** — disabled by default
- **Static** — configured at startup, not per-request
- **Allowlisted** — tools are deny-by-default
- **Profile-gated** — visibility is controlled per execution profile
- **Audited** — downstream tool usage is recorded in session events

This is not a dynamic plugin marketplace. It is controlled operator-administered federation.

---

## Enabling Federation

Set two environment variables:

```bash
CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true
CLAUDE_AGENT_MCP_FEDERATION_CONFIG=/path/to/federation.json
```

If `CLAUDE_AGENT_MCP_FEDERATION_ENABLED` is false (the default), the federation
config file is never read and no downstream servers are contacted.

---

## Federation Config File

The federation config is a JSON file with this structure:

```json
{
  "downstream_servers": [
    {
      "name": "filesystem_tools",
      "transport": "stdio",
      "command": "python",
      "args": ["-m", "my_downstream_server"],
      "env": {},
      "enabled": true,
      "discovery_timeout_seconds": 10.0,
      "allowed_tools": ["read_file", "list_dir"],
      "profiles_allowed": ["general"]
    }
  ]
}
```

### Server config fields

| Field | Required | Description |
|-------|----------|-------------|
| `name` | yes | Unique server name. Must not contain `__`. Used as prefix in normalized tool names. |
| `transport` | yes | Transport type. Only `"stdio"` is supported in v0.3. |
| `command` | yes | Executable to spawn for stdio transport. |
| `args` | no | Arguments to pass to the command. |
| `env` | no | Environment variables for the subprocess. |
| `enabled` | no | Default: `true`. Set to `false` to disable without removing the config. |
| `discovery_timeout_seconds` | no | Default: `10.0`. Timeout for tool discovery at startup. |
| `allowed_tools` | yes | **Exact downstream tool names** that may be exposed. Empty means no tools. |
| `profiles_allowed` | yes | **Profile names** that may see tools from this server. |

### Rules

1. A downstream server must be **explicitly configured** to be used.
2. A configured server may be **disabled** (`"enabled": false`) — disabled servers are ignored.
3. Only tools listed in `allowed_tools` can ever be visible. Discovery alone is insufficient.
4. Only profiles listed in `profiles_allowed` can see the server's tools.
5. The `verification` profile is **not** in `profiles_allowed` by default and must be added explicitly.

---

## Normalized Tool Names

To prevent collisions, all downstream tools are renamed to:

```
{server_name}__{downstream_tool_name}
```

Example: a tool named `read_file` from server `filesystem_tools` becomes
`filesystem_tools__read_file`.

Server names must not contain `__`.

---

## Profile Visibility

| Profile | Default downstream tool access |
|---------|-------------------------------|
| `general` | May receive tools if listed in `profiles_allowed` |
| `verification` | **None** unless explicitly added to `profiles_allowed` |

Adding `"verification"` to `profiles_allowed` should be done with care.
The verification profile is read-only and fail-closed by design.
Downstream tools expand the tool surface available during verification runs.

---

## Startup Behavior

At startup, `claude-agent-mcp`:

1. Checks `CLAUDE_AGENT_MCP_FEDERATION_ENABLED`. If false, stops here.
2. Reads the federation config file. Config errors abort startup.
3. Connects to each enabled downstream server and discovers its tools.
4. Applies the allowlist — only listed tools become candidates.
5. Logs how many tools were discovered and allowlisted.

Discovery failures for individual servers are **logged and skipped** — they do
not abort startup. This means a transient downstream failure at startup results
in zero tools from that server, not a crash.

---

## Tool Invocation Flow

When Claude calls a downstream tool during a session:

1. The tool call is validated against the **visible set** for the active profile.
2. Required arguments are validated against the tool's input schema.
3. The downstream MCP server is contacted via a fresh stdio connection.
4. The result is normalized and returned to Claude.
5. Two session events are recorded: `downstream_tool_invocation` and `downstream_tool_result`.

Tool invocations that fail (connection error, schema error, timeout) produce a
normalized error result — they do not crash the session.

---

## Session Audit Events

When federation tools are active, the following events are added to session history:

| Event type | When |
|------------|------|
| `downstream_tool_catalog_resolved` | At the start of each execution, listing which tools are visible |
| `downstream_tool_invocation` | When Claude selects a downstream tool (input keys only, not values) |
| `downstream_tool_result` | After invocation completes (success or failure) |

Invocation events record **input keys only**, not input values, to minimize
the risk of logging sensitive data.

---

## Security Posture

Federation expands the trust boundary. Operators must understand:

1. **Downstream servers run as subprocesses.** They inherit the process environment.
   Restrict what downstream servers can access.

2. **Streamable-HTTP transport without authentication is not public-safe.**
   Federation tools are governed by configuration and policy, but the HTTP
   endpoint itself has no authentication in v0.3.

3. **The `verification` profile is downstream-disabled by default.**
   Verification is designed to be read-only and fail-closed. Adding downstream
   tools to verification must be a deliberate operator decision.

4. **There is no wildcard allowlist mode.**
   Every tool that can be invoked must be explicitly named in `allowed_tools`.

5. **Federation is intended for trusted operator-controlled environments.**
   Do not expose federation-enabled deployments to untrusted callers without
   additional authentication and network controls.

---

## Example

Start the server with federation enabled, pointing at a config file:

```bash
ANTHROPIC_API_KEY=sk-...
CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true
CLAUDE_AGENT_MCP_FEDERATION_CONFIG=/etc/claude-agent-mcp/federation.json
claude-agent-mcp --transport streamable-http
```

At startup, the server will log:
```
Federation initialized: 1 server(s), 2 tool(s) discovered, 2 allowlisted
Federation active: 2 allowlisted tool(s) available
```

Claude sessions using the `general` profile will then have access to the
allowlisted downstream tools during task execution.

---

## Limitations (v0.3)

- Only `stdio` transport is supported for downstream servers.
- No persistent connection pooling — a new subprocess is started per discovery
  and per tool invocation.
- No dynamic server registration at runtime.
- No wildcard or "allow all" shortcut.
- No cancellation of in-flight downstream calls.
- `verification` profile remains downstream-disabled by default.

These are intentional constraints for the v0.3 controlled federation release.
