# Downstream Tool Policy — claude-agent-mcp v0.3

This document describes the policy model that governs downstream tool exposure
in `claude-agent-mcp` v0.3 federation.

---

## Policy layers

There are four distinct gates between a downstream tool and a Claude session.
A tool must pass all four to be callable.

### Layer 1 — Server enablement

A downstream server must be **enabled** in the federation config:

```json
{ "enabled": true }
```

Disabled servers are ignored at startup. Their tools are never discovered.

### Layer 2 — Discovery

At startup, `claude-agent-mcp` connects to each enabled server and lists its tools.
Discovery is a read-only operation.

A tool that exists on the server but fails discovery (connection error, timeout)
is not in the catalog and is not usable. Discovery failures for individual servers
are logged and skipped.

### Layer 3 — Allowlist

A discovered tool must be explicitly named in `allowed_tools`:

```json
{ "allowed_tools": ["read_file", "list_dir"] }
```

Tools not in this list are in the catalog but marked `allowed=false`.
They cannot be invoked, regardless of profile.

There is no "allow all" shortcut. Every exposable tool must be named.

### Layer 4 — Profile visibility

Even an allowlisted tool is invisible unless the **active profile** is listed
in `profiles_allowed`:

```json
{ "profiles_allowed": ["general"] }
```

| Profile | Behavior |
|---------|----------|
| `general` | May receive tools if explicitly listed in `profiles_allowed` |
| `verification` | Receives no downstream tools unless explicitly listed |

The `verification` profile exclusion is enforced in code, not just documented.

---

## Decision tree

For each downstream tool during a session:

```
Is the server enabled?
  └─ No  → tool does not exist
  └─ Yes → Was discovery successful?
              └─ No  → tool not in catalog
              └─ Yes → Is the tool in allowed_tools?
                          └─ No  → catalog entry exists but allowed=false; not callable
                          └─ Yes → Is the active profile in profiles_allowed?
                                      └─ No  → tool not visible for this session
                                      └─ Yes → tool is callable
```

---

## Verification posture

The `verification` profile is conservative by design:

- Read-only
- Fail-closed
- Downstream tools disabled by default

Adding downstream tools to verification adds evidence-gathering capability,
but also expands the tool surface during a read-only evaluation. This must
be a deliberate operator decision with clear justification.

If you add `"verification"` to `profiles_allowed` for a server, document why.

---

## Audit trail

Every downstream tool invocation is recorded in session events:

- `downstream_tool_catalog_resolved` — which tools were visible when the session started
- `downstream_tool_invocation` — which tool was called (input keys, not values)
- `downstream_tool_result` — whether the call succeeded

This provides an audit trail for all downstream tool usage without over-logging
potentially sensitive input values.

---

## Adding a new downstream server

Checklist:

- [ ] Name the server clearly and uniquely (no `__` in the name)
- [ ] Set `transport: stdio`
- [ ] Specify the exact command and args to start the server
- [ ] List only the tools you intend to expose in `allowed_tools`
- [ ] List only the profiles that should see these tools in `profiles_allowed`
- [ ] Do not add `verification` to `profiles_allowed` unless you have a specific need
- [ ] Test discovery before enabling in production
- [ ] Review the server's own permissions and capabilities

---

## Revoking downstream tool access

To revoke access to a tool:

1. Remove the tool from `allowed_tools` in the federation config.
2. Restart the server. Changes take effect at next startup.

To disable an entire server:

1. Set `"enabled": false` in the server's config block.
2. Restart the server.

There is no runtime revocation in v0.3.

---

## Trust boundary statement

`claude-agent-mcp` trusts:
- Its own internal session state and policy enforcement
- Operator-provided configuration (federation config file)
- The list of allowlisted tools

`claude-agent-mcp` does NOT trust:
- The content of downstream tool results (treated as untrusted external input)
- Raw downstream server names or tool names before normalization
- Automatic tool exposure without explicit allowlisting

Downstream tool results are passed to Claude as tool_result blocks.
Claude interprets their content, not the server layer.
