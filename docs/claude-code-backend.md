# Claude Code Backend (v0.5)

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

## How execution works (v0.5)

For each task, the Claude Code backend:

1. Builds a **structured prompt** from system/profile instructions, session summary, bounded conversation history, and the current user task.
2. Invokes `claude --print <prompt>` as a subprocess.
3. Collects stdout as the output text.
4. Returns a `NormalizedProviderResult` with `stop_reason: backend_defaulted`.
5. Emits warnings for any limitations that applied (truncation, tool forwarding, stop-reason precision).

---

## Context reconstruction (v0.5)

Continuation context is rebuilt using a structured prompt format rather than a flat plain-text dump:

```
[System]
<profile/policy instructions>

────────────────────────────────────────────────────────────
[Session Context]
<session summary from prior turns>

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

### Truncation policy

To keep prompts bounded, only the most recent **10 user/assistant exchange pairs** are included in the history section. If truncation occurs:

- A warning is added to the response `warnings` array.
- The session summary is included as the `[Session Context]` section to preserve high-level continuity.

Individual message content is capped at **2000 characters** per message. Truncated content is marked with `[truncated]`.

---

## Backend capabilities (v0.5)

The Claude Code backend declares the following capability profile:

| Capability | Supported | Notes |
|---|---|---|
| `supports_downstream_tools` | No | Federation tools are not forwarded to the CLI |
| `supports_structured_tool_use` | No | No agentic tool-use loop |
| `supports_native_multiturn` | No | Each CLI invocation is single-shot |
| `supports_rich_stop_reason` | No | `stop_reason` is always `backend_defaulted` |
| `supports_structured_messages` | No | History reconstructed as labeled text |
| `supports_workspace_assumptions` | Yes | CLI runs in the local environment |

These capabilities are used internally to emit accurate warnings and prevent unsupported forwarding paths.

---

## Warnings

The Claude Code backend emits warnings in the response envelope for the following conditions:

| Warning condition | Warning in `warnings` field |
|---|---|
| Downstream tools were visible but not forwarded | Yes — includes advice to use `api` backend |
| History was truncated | Yes — includes exchange count |
| Stop-reason precision is limited | Yes — always present |
| Empty CLI response | Yes |

Warnings appear in the `warnings` array of the canonical `AgentResponse` envelope.

---

## Known limitations (v0.5)

These limitations remain after v0.5 stabilization and are documented explicitly:

- **Single-turn per CLI invocation.** The Claude Code backend does not run a native multi-turn tool-use loop. Each call is one CLI invocation.
- **No federation tool forwarding.** Downstream federation tools are not passed to the CLI. A clear warning is emitted when this applies.
- **No rich stop reason.** The CLI does not expose stop semantics. `stop_reason` is always `backend_defaulted`. Do not write logic that depends on specific stop-reason values when using this backend.
- **History is reconstructed text.** Context is rebuilt from the internal session store using labeled text sections, not structured Messages API objects.
- **Model selection may fail.** The `--model` flag is passed to the CLI, but not all model identifiers may be accepted. If the CLI rejects the model, the invocation will fail with `claude_code_invocation_error`.

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

The session history exceeded the reconstruction limit. This is expected for long sessions. The session summary is used to preserve continuity. If precision is critical for a long session, consider using the `api` backend.

### Response `warnings` contains federation tool notice

Federation tools were configured for this profile but the `claude_code` backend cannot forward them. Switch to the `api` backend if you need downstream tool-use.

---

## Comparison with API backend

| | `api` backend | `claude_code` backend |
|---|---|---|
| Auth | `ANTHROPIC_API_KEY` | `claude login` |
| Multi-turn | Native (Messages API) | Single-shot CLI |
| Federation tools | Supported | Not supported |
| stop_reason | API-native | `backend_defaulted` |
| Conversation history | Structured messages array | Reconstructed labeled text |
| Recommended for | Server/CI deployments | Local Claude Code workflows |

For the full capability matrix, see [backend-capability-matrix.md](backend-capability-matrix.md).
