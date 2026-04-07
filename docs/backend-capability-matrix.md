# Backend Capability Matrix (v0.6)

This document describes the capability flags declared by each execution backend in `claude-agent-mcp`. These flags are used internally by the workflow executor to emit warnings and suppress unsupported execution paths. They are not exposed in MCP tool contracts.

---

## Capability flags

| Capability | Description |
|---|---|
| `supports_downstream_tools` | Backend can receive and invoke downstream federation tools |
| `supports_structured_tool_use` | Backend participates in a structured agentic tool-use loop |
| `supports_native_multiturn` | Backend maintains its own native conversation state across turns |
| `supports_rich_stop_reason` | Backend returns semantically rich `stop_reason` values |
| `supports_structured_messages` | Backend accepts structured role/content message objects |
| `supports_workspace_assumptions` | Backend can operate on a local workspace directory natively |
| `supports_limited_downstream_tools` | Backend supports text-based tool description injection (v0.6, opt-in) |

---

## Capability matrix

| Capability | `api` backend | `claude_code` backend |
|---|---|---|
| `supports_downstream_tools` | **Yes** | No |
| `supports_structured_tool_use` | **Yes** | No |
| `supports_native_multiturn` | **Yes** | No |
| `supports_rich_stop_reason` | **Yes** | No |
| `supports_structured_messages` | **Yes** | No |
| `supports_workspace_assumptions` | No | **Yes** |
| `supports_limited_downstream_tools` | No | **Yes** (opt-in) |

---

## What each flag means for operators

### `supports_downstream_tools`

When `False` (claude_code): downstream federation tools configured for a profile will **not** be forwarded to the backend. A warning is emitted in the response `warnings` field. Switch to the `api` backend if your workflow requires federation tool-use.

### `supports_structured_tool_use`

When `False` (claude_code): there is no agentic tool-use loop. The backend receives a single prompt and returns a single response. Tools cannot be invoked mid-execution.

### `supports_native_multiturn`

When `False` (claude_code): each execution is a single CLI invocation. Continuation context is reconstructed from the internal session store and embedded as labeled text sections in the prompt. This is less precise than a native multi-turn API conversation.

### `supports_rich_stop_reason`

When `False` (claude_code): `stop_reason` is always reported as `backend_defaulted`. Do not write downstream logic that depends on specific stop-reason values (e.g., `tool_use`, `max_tokens`) when using this backend.

### `supports_structured_messages`

When `False` (claude_code): conversation history is not passed as a structured messages array. It is reconstructed as labeled plain-text sections in the prompt. Message role boundaries are preserved as labels (`[User]`, `[Assistant]`) but the format is text-based.

### `supports_workspace_assumptions`

When `True` (claude_code): the CLI runs in the operator's local environment and can access local files. This is useful for local development workflows.

### `supports_limited_downstream_tools`

When `True` (claude_code, v0.6): the backend can inject compatible downstream tool definitions as **text descriptions** into the prompt. This is not a real tool-use loop — tools are described for model context only and cannot be invoked.

Enabled opt-in via `CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING=true`. When `False` (default), a consolidated downgrade warning is emitted instead.

The `api` backend has `supports_limited_downstream_tools=False` because it supports full tool invocation (`supports_downstream_tools=True`) — the limited flag is irrelevant.

---

## Warnings emitted by the workflow executor (v0.6)

The workflow executor checks backend capabilities before execution and emits warnings to the response `warnings` array when mismatches are detected.

| Condition | Warning emitted |
|---|---|
| Federation tools resolved but forwarding disabled (claude_code) | Yes — consolidated, advises `api` backend |
| Federation tools resolved, limited forwarding enabled, incompatible tool dropped (v0.6) | Yes — per-tool, names tool and reason |
| History truncated (claude_code) | Yes — states exchange count kept |
| Stop-reason precision limited (claude_code) | Yes — always present |
| Empty CLI response (claude_code) | Yes |

---

## Implementation reference

Capabilities are declared as a frozen `BackendCapabilities` dataclass in `src/claude_agent_mcp/backends/base.py`. Each backend implements the `capabilities` property.

```python
from claude_agent_mcp.backends.base import BackendCapabilities

# claude_code backend declaration (v0.6)
BackendCapabilities(
    supports_downstream_tools=False,
    supports_structured_tool_use=False,
    supports_native_multiturn=False,
    supports_rich_stop_reason=False,
    supports_structured_messages=False,
    supports_workspace_assumptions=True,
    supports_limited_downstream_tools=True,  # v0.6, opt-in
)

# api backend declaration
BackendCapabilities(
    supports_downstream_tools=True,
    supports_structured_tool_use=True,
    supports_native_multiturn=True,
    supports_rich_stop_reason=True,
    supports_structured_messages=True,
    supports_workspace_assumptions=False,
    supports_limited_downstream_tools=False,  # full support, not limited
)
```

---

## Version notes

- v0.5: Capability matrix introduced. Backend limitations declared programmatically and surfaced as runtime warnings.
- v0.6: `supports_limited_downstream_tools` added. Claude Code backend can now inject compatible tool descriptions as text (opt-in). Continuation prompts use distinct `[Continuation Session]` framing.
