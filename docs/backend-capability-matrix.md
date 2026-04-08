# Backend Capability Matrix (v0.8.0)

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
| `supports_structured_continuation_context` | Backend accepts and uses `SessionContinuationContext` for continuation (v0.7.0) |
| `supports_continuation_window_policy` | Backend respects `ContinuationWindowPolicy` for bounded reconstruction (v0.7.0) |
| `supports_execution_mediation` | Backend output may contain structured mediated action requests processed by the runtime (v0.8.0) |
| `supports_mediated_action_results` | Backend supports inclusion of mediated action results in continuation context (v0.8.0) |

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
| `supports_structured_continuation_context` | No | **Yes** (v0.7.0) |
| `supports_continuation_window_policy` | No | **Yes** (v0.7.0) |
| `supports_execution_mediation` | No | **Yes** (v0.8.0, opt-in) |
| `supports_mediated_action_results` | No | **Yes** (v0.8.0, opt-in) |

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

### `supports_structured_continuation_context` (v0.7.0)

When `True` (claude_code): the backend accepts a `SessionContinuationContext` object and uses it to render a deterministic, section-based continuation prompt. This supersedes the flat `session_summary` / conversation history approach for continuation calls.

When `False` (api): the API backend uses native multi-turn conversation state (`conversation_history` array). Structured continuation context is not needed and is ignored.

### `supports_continuation_window_policy` (v0.7.0)

When `True` (claude_code): the backend respects a `ContinuationWindowPolicy` that bounds how much prior context is included in continuation reconstruction. Policy parameters include:

- `max_recent_turns`: how many turn pairs to include (default: 5)
- `max_warnings`: how many continuation-relevant warnings to carry forward (default: 3)
- `max_forwarding_events`: how many forwarding events to summarize (default: 3)
- `include_verification_context`: whether to carry forward prior verification verdicts (default: true)
- `include_tool_downgrade_context`: whether to carry forward tool downgrade warnings (default: true)

When `False` (api): not applicable. The API backend manages context natively.

### `supports_execution_mediation` (v0.8.0)

When `True` (claude_code): the backend's text output may contain structured mediated action request blocks. The **runtime** (not the backend) detects, validates against policy and federation visibility, and executes approved requests.

> **This is not native tool calling.** The Claude Code CLI has no tool invocation protocol. Mediated execution is text-pattern detection and runtime dispatch — not backend-native tool use.

Enabled opt-in via `CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION=true`. Disabled by default.

When `False` (api): not applicable. The API backend has full native `tool_use` / `tool_result` support.

### `supports_mediated_action_results` (v0.8.0)

When `True` (claude_code): compact summaries of completed mediated actions can be included in continuation context prompts via the `[Mediated Execution Context]` section.

Enabled opt-in via `CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_MEDIATED_RESULTS_IN_CONTINUATION=true`. Disabled by default.

When `False` (api): not applicable.

---

## Warnings emitted by the workflow executor (v0.7.0)

The workflow executor checks backend capabilities before execution and emits warnings to the response `warnings` array when mismatches are detected.

| Condition | Warning emitted |
|---|---|
| Federation tools resolved but forwarding disabled (claude_code) | Yes — consolidated, advises `api` backend |
| Federation tools resolved, limited forwarding enabled, incompatible tool dropped (v0.6) | Yes — per-tool, names tool and reason |
| History truncated (claude_code) | Yes — states exchange count kept |
| Stop-reason precision limited (claude_code) | Yes — always present |
| Empty CLI response (claude_code) | Yes |

---

## Session events for continuation observability (v0.7.0)

For continuation calls on backends with `supports_structured_continuation_context=True`, the following session events are recorded:

| Event type | Contents |
|---|---|
| `session_continuation_context_built` | policy used, counts of included/omitted turns/warnings/forwarding events, reconstruction version |
| `session_continuation_context_truncated` | same stats, recorded only when truncation occurred |
| `session_continuation_prompt_rendered` | reconstruction version |

These events are accessible via `agent_get_session` and the internal event log.

---

## Implementation reference

Capabilities are declared as a frozen `BackendCapabilities` dataclass in `src/claude_agent_mcp/backends/base.py`. Each backend implements the `capabilities` property.

```python
from claude_agent_mcp.backends.base import BackendCapabilities

# claude_code backend declaration (v0.8.0)
BackendCapabilities(
    supports_downstream_tools=False,
    supports_structured_tool_use=False,
    supports_native_multiturn=False,
    supports_rich_stop_reason=False,
    supports_structured_messages=False,
    supports_workspace_assumptions=True,
    supports_limited_downstream_tools=True,           # v0.6, opt-in
    supports_structured_continuation_context=True,    # v0.7.0
    supports_continuation_window_policy=True,         # v0.7.0
    supports_execution_mediation=True,                # v0.8.0, opt-in via config
    supports_mediated_action_results=True,            # v0.8.0, opt-in via config
)

# api backend declaration
BackendCapabilities(
    supports_downstream_tools=True,
    supports_structured_tool_use=True,
    supports_native_multiturn=True,
    supports_rich_stop_reason=True,
    supports_structured_messages=True,
    supports_workspace_assumptions=False,
    supports_limited_downstream_tools=False,          # full support, not limited
    supports_structured_continuation_context=False,   # API backend has native multi-turn
    supports_continuation_window_policy=False,        # not applicable
    supports_execution_mediation=False,               # not applicable — API has native tool use
    supports_mediated_action_results=False,           # not applicable
)
```

---

## Warnings emitted for mediation (v0.8.0)

When mediation is enabled and requests are rejected, warnings are added to the response `warnings` array:

| Condition | Warning |
|---|---|
| Mediated action rejected by any policy gate | Yes — names request_id, tool, reason, and policy_decision code |

Rejected actions are also recorded as `mediated_action_rejected` session events for operator audit.

---

## Version notes

- v0.5: Capability matrix introduced. Backend limitations declared programmatically and surfaced as runtime warnings.
- v0.6: `supports_limited_downstream_tools` added. Claude Code backend can now inject compatible tool descriptions as text (opt-in). Continuation prompts use distinct `[Continuation Session]` framing.
- v0.7.0: `supports_structured_continuation_context` and `supports_continuation_window_policy` added. Claude Code backend uses a deterministic `SessionContinuationContext` for all continuation calls. Warning carry-forward is classified and bounded. Forwarding history is summarized. Continuation reconstruction decisions are recorded as inspectable session events.
- v0.8.0: `supports_execution_mediation` and `supports_mediated_action_results` added. Claude Code backend output may contain structured mediated action request blocks. The runtime validates and executes approved requests under explicit policy control. This is NOT native tool calling. All mediation events are persisted as session events for operator inspection. Mediated results can optionally be summarized in continuation context.
