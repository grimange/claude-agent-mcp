# Claude Code Backend (v0.7.0)

## Overview

The Claude Code backend executes tasks through the Claude Code CLI (`claude`) rather than the Anthropic Messages API directly. This allows operators who have Claude Code installed and authenticated to run `claude-agent-mcp` without configuring an `ANTHROPIC_API_KEY`.

> **Auth model:** Claude Code backend uses Claude Code's own login state. It does **not** use `ANTHROPIC_API_KEY`. These are distinct authentication mechanisms and must not be conflated.

---

## Prerequisites

1. **Claude Code must be installed.**
   Install it from [claude.ai/code](https://claude.ai/code).

2. **Claude Code must be authenticated.**
   Run `claude login` in the operator's environment to establish a session.

3. **The `claude` binary must be accessible.**
   Either on `PATH`, or configured via `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH`.

---

## Configuration

```bash
# Required: select the claude_code backend
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code

# Optional: explicit path to the claude CLI
export CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH=/path/to/claude

# Optional: CLI timeout in seconds (default: 300)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT=300

# Optional (v0.6): enable limited downstream tool forwarding (default: false)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING=false

# Optional (v0.7.0): continuation window policy
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS=5
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_WARNINGS=3
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_FORWARDING_EVENTS=3
export CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_VERIFICATION_CONTEXT=true
export CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_TOOL_DOWNGRADE_CONTEXT=true
```

Do not set `ANTHROPIC_API_KEY` when using the Claude Code backend. The backend does not use it.

---

## Startup validation

At server startup, `claude-agent-mcp` will:

1. Look for the `claude` binary (via configured path or `PATH`).
2. Run `claude --version` to confirm the CLI is executable.
3. Fail with a clear error if either check fails.

Authentication state is **not** verified at startup. An unauthenticated Claude Code session will produce a `claude_code_invocation_error` at execution time with the CLI's error output.

---

## How execution works (v0.7.0)

For each task, the Claude Code backend:

1. Builds a **structured prompt** from system/profile instructions, session context, bounded conversation history, optional compatible tool descriptions, and the current user task.
2. Invokes `claude --print <prompt>` as a subprocess.
3. Collects stdout as the output text.
4. Returns a `NormalizedProviderResult` with `stop_reason: backend_defaulted`.
5. Emits warnings for any limitations that applied (truncation, tool filtering, stop-reason precision).

For **continuation calls** (`agent_continue_session`), the runtime additionally:

1. Builds a `SessionContinuationContext` from persisted session events using a `ContinuationWindowPolicy`.
2. Renders a structured continuation prompt with deterministic section ordering.
3. Records session events for operator inspection (`session_continuation_context_built`, `session_continuation_prompt_rendered`).

---

## Context reconstruction (v0.7.0)

### Initial task prompt

For new sessions (`agent_run_task`), the prompt uses this structure:

```
[System]
<profile/policy instructions>

────────────────────────────────────────────────────────────
[Session Context]
<session summary from prior turns>

────────────────────────────────────────────────────────────
[Available Tools]
<compatible tool descriptions — only when limited forwarding is enabled>

────────────────────────────────────────────────────────────
[Conversation History]
[User]
<prior user message>

[Assistant]
<prior assistant response>

────────────────────────────────────────────────────────────
[Current Request]
<current user message>

────────────────────────────────────────────────────────────
[Instructions]
Respond to the current request above. Use the conversation history and session context as background.
```

### Structured continuation prompt (v0.7.0)

For `agent_continue_session` calls, the backend uses a structured continuation context
built deterministically from persisted session events. Empty sections are omitted.

```
[System]
<profile/policy instructions>

────────────────────────────────────────────────────────────
[Continuation Session]
Session: <session_id>
Reconstruction: v0.7.0
Context: N turn(s) included[, M omitted]

────────────────────────────────────────────────────────────
[Session Summary]
<session summary>

────────────────────────────────────────────────────────────
[Recent Interaction State]
[User]
<recent user request>

[Assistant]
<recent agent output>

────────────────────────────────────────────────────────────
[Relevant Warnings]
- <continuation-relevant warning 1>
- <continuation-relevant warning 2>

────────────────────────────────────────────────────────────
[Tool Forwarding Context]
Mode: <forwarding_mode>
Forwarded: <tool names>
Dropped: <tool names>

────────────────────────────────────────────────────────────
[Active Constraints]
working_directory: <path>
profile: <profile>

────────────────────────────────────────────────────────────
[Current Request]
<current user message>

────────────────────────────────────────────────────────────
[Instructions]
You are continuing this session. Resume from where you left off, building on the session summary and recent interaction state above.
```

Section ordering is deterministic. Empty sections are omitted entirely — operators can trust that the rendered prompt does not contain empty placeholders.

### Truncation policy

To keep prompts bounded, the continuation window policy controls what is included:

| Policy field | Default | Description |
|---|---|---|
| `max_recent_turns` | 5 | Recent user/assistant turn pairs |
| `max_warnings` | 3 | Continuation-relevant warnings |
| `max_forwarding_events` | 3 | Forwarding decision events |
| `include_verification_context` | true | Prior verification outcomes |
| `include_tool_downgrade_context` | true | Prior tool downgrade events |

When truncation occurs, the omission counts are recorded in a `session_continuation_context_truncated` session event and noted in the prompt's `[Continuation Session]` header.

Individual message content is capped at **2000 characters** per message. Truncated content is marked with `[truncated]`.

---

## Warning carry-forward (v0.7.0)

Warnings are classified for continuation relevance before being included in prompts:

| Classification | Meaning | Carried forward? |
|---|---|---|
| `continuation_relevant` | Affects capability interpretation in resumed execution | Yes (by default) |
| `operator_only` | For operator awareness only; does not affect model behavior | No |
| `request_local` | Specific to a single request | No |

**Continuation-relevant warnings include:**
- Prior tool downgrade events (when `include_tool_downgrade_context=true`)
- Prior verification outcomes (when `include_verification_context=true`)

All warnings remain in persisted session events. Only classified-as-relevant warnings appear in the continuation prompt's `[Relevant Warnings]` section.

---

## Continuation observability (v0.7.0)

The following session events are recorded for each continuation call:

| Event type | Contents | Purpose |
|---|---|---|
| `session_continuation_context_built` | policy, stats (included/omitted counts, version) | Operator audit: what was selected |
| `session_continuation_context_truncated` | stats | Operator alert: context was truncated |
| `session_continuation_prompt_rendered` | reconstruction_version | Confirms structured rendering was used |

These events are visible via `agent_get_session` detail and the internal session event log.

---

## Backend capabilities (v0.7.0)

The Claude Code backend declares the following capability profile:

| Capability | Supported | Notes |
|---|---|---|
| `supports_downstream_tools` | No | Full federation tool invocation not supported |
| `supports_structured_tool_use` | No | No agentic tool-use loop |
| `supports_native_multiturn` | No | Each CLI invocation is single-shot |
| `supports_rich_stop_reason` | No | `stop_reason` is always `backend_defaulted` |
| `supports_structured_messages` | No | History reconstructed as labeled text |
| `supports_workspace_assumptions` | Yes | CLI runs in the local environment |
| `supports_limited_downstream_tools` | Yes | Text-based tool description injection (opt-in, v0.6) |
| `supports_structured_continuation_context` | Yes | Uses `SessionContinuationContext` for continuation (v0.7.0) |
| `supports_continuation_window_policy` | Yes | Respects `ContinuationWindowPolicy` bounds (v0.7.0) |

These capabilities are used internally to emit accurate warnings and select the appropriate rendering path.

---

## Limited downstream tool forwarding (v0.6)

By default, downstream federation tools are not forwarded. When `CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING=true` is set, the backend can inject compatible tool descriptions as **text** into the prompt.

**This is not a real tool-use loop.** The CLI cannot invoke tools. The descriptions are informational context only — they tell the model what tools exist but cannot execute them.

### Compatibility screening

Before injection, each tool is screened:

| Rejection reason | Level |
|---|---|
| Tool has no description | `missing_description` |
| Schema uses `$ref`, `allOf`, `anyOf`, `oneOf`, or `not` | `schema_unsupported` |
| Schema has more than 5 top-level properties | `complex_schema` |

Compatible tools are injected as an `[Available Tools]` section. Incompatible tools are dropped with a per-tool warning naming the tool and the reason.

Prior tool forwarding decisions are summarized in the `[Tool Forwarding Context]` section of continuation prompts.

---

## Warnings

The Claude Code backend emits warnings in the response envelope for the following conditions:

| Warning condition | Warning in `warnings` field |
|---|---|
| Downstream tools visible but forwarding disabled | Yes — consolidated, advises `api` backend |
| Compatible tool forwarded, incompatible tool dropped (v0.6) | Yes — per-tool, names tool and reason |
| History was truncated | Yes — includes exchange count |
| Stop-reason precision is limited | Yes — always present |
| Empty CLI response | Yes |

Warnings appear in the `warnings` array of the canonical `AgentResponse` envelope.

---

## Known limitations (v0.7.0)

These limitations remain after v0.7.0 continuity improvement and are documented explicitly:

- **No native multi-turn execution.** Each CLI invocation is single-shot. v0.7.0 improves continuation *context reconstruction* but does not add native multi-turn execution to the CLI.
- **Not API-equivalent session continuity.** The structured continuation context improves determinism and operator visibility, but it is still a text reconstruction — not a native backend-persistent conversation state.
- **Limited tool forwarding is text-only.** Even when enabled, tools are injected as text descriptions — not invoked. The model sees tool context but cannot call them.
- **No full federation tool support.** Real tool invocation is not available. Use the `api` backend for full federation tool-use.
- **No rich stop reason.** `stop_reason` is always `backend_defaulted`.
- **Model selection may fail.** The `--model` flag is passed to the CLI, but not all model identifiers may be accepted.

For full capability comparison, see [backend-capability-matrix.md](backend-capability-matrix.md).

---

## Troubleshooting

### `ClaudeCodeUnavailableError: claude CLI not found in PATH`

Install Claude Code and ensure the binary is on `PATH`, or set `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH` to the absolute path.

### `ClaudeCodeInvocationError: claude CLI exited with code 1`

The CLI failed. Check the error message — it includes stderr output. Common causes:
- Not authenticated: run `claude login`
- Rate limited
- Invalid prompt or model name

### Execution times out

Increase `CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT` (seconds). Default is 300.

### Response `warnings` contains truncation notice

The session history exceeded the reconstruction limit. This is expected for long sessions. The session summary is used to preserve continuity. Configure `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS` to increase the window (at the cost of longer prompts).

### Response `warnings` contains federation tool notice

Federation tools were configured for this profile but the `claude_code` backend cannot forward them. Switch to the `api` backend if you need downstream tool-use.

### Continuation context seems incomplete

Check the `session_continuation_context_built` event via `agent_get_session` for `turns_omitted`, `warnings_omitted`, and `forwarding_events_omitted` counts. Adjust the continuation window policy env vars to include more context.

---

## Comparison with API backend

| | `api` backend | `claude_code` backend |
|---|---|---|
| Auth | `ANTHROPIC_API_KEY` | `claude login` |
| Multi-turn | Native (Messages API) | Single-shot CLI per call |
| Continuation context | Structured messages array | Structured text reconstruction (v0.7.0) |
| Federation tools | Supported | Not supported |
| stop_reason | API-native | `backend_defaulted` |
| Recommended for | Server/CI deployments | Local Claude Code workflows |

For the full capability matrix, see [backend-capability-matrix.md](backend-capability-matrix.md).
