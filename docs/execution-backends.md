# Execution Backends (v0.4)

## Overview

`claude-agent-mcp` supports multiple execution backends. The execution backend controls how Claude-backed tasks are executed internally. The public MCP tool surface, session model, policy enforcement, and canonical response envelopes remain identical regardless of which backend is selected.

## Supported backends

| Backend name  | Auth model           | Description |
|---------------|----------------------|-------------|
| `api`         | `ANTHROPIC_API_KEY`  | Anthropic Messages API (default) |
| `claude_code` | Claude Code login    | Claude Code CLI (`claude -p`) |

## Selecting a backend

Set the environment variable:

```
CLAUDE_AGENT_MCP_EXECUTION_BACKEND=api         # default
CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
```

Backend selection is explicit and validated at startup. Unknown backend names cause an immediate startup failure with a clear error message. There is no silent fallback between backends.

## API backend (default)

### Purpose

The `api` backend executes tasks through the Anthropic Messages API. This is the default and backward-compatible choice for server-style deployments.

### Authentication

Requires `ANTHROPIC_API_KEY` to be set. This backend will fail at startup if the variable is absent.

### Configuration

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | (required) | Anthropic API credential |
| `CLAUDE_AGENT_MCP_MODEL` | `claude-sonnet-4-6` | Claude model |

### When to use

- Server-style deployments with API credentials
- CI/CD automation environments
- Any environment where `ANTHROPIC_API_KEY` is available

---

## Claude Code backend

The Claude Code backend executes tasks via the Claude Code CLI (`claude`) instead of direct API calls. This allows operators who have Claude Code installed and authenticated to use `claude-agent-mcp` without a separate API key.

See [claude-code-backend.md](claude-code-backend.md) for full setup and usage details.

### When to use

- Operators who have Claude Code installed and authenticated via `claude login`
- Environments where Claude subscriptions are available but API keys are not desired
- Local development workflows that already use Claude Code

---

## Backend contract guarantees

Regardless of which backend is selected, the following guarantees hold:

1. **Canonical response envelopes are unchanged.** MCP tool responses use the same `AgentResponse` shape.
2. **Internal session identity is unchanged.** `session_id` is always the internal canonical identifier.
3. **Policy enforcement is unchanged.** Profile-based policy is evaluated before any backend execution.
4. **Federation allowlists are unchanged.** Visible tools are resolved before the backend is invoked.
5. **Session transcripts are unchanged.** All session events are persisted in the internal SQLite store.

## Backend metadata in sessions

Sessions record the backend name in the `provider` column of the sessions table. This is for observability only and does not affect session behavior.

## Behavioral differences

| Behavior | api | claude_code |
|---|---|---|
| Auth mechanism | API key | Claude Code login |
| Federation tool-use loop | Supported | Not supported (v0.4) |
| Multi-turn native support | Via Messages API | CLI single-shot |
| Conversation history | Native messages array | Serialized as plain text in prompt |
| Stop reason | API-native | Always `end_turn` |

## Error reference

| Error code | Cause |
|---|---|
| `execution_backend_config_error` | Unknown backend name |
| `execution_backend_auth_error` | API key missing for `api` backend |
| `execution_backend_unavailable` | Backend prerequisites not satisfied |
| `claude_code_unavailable` | `claude` CLI not found or not executable |
| `claude_code_invocation_error` | CLI invocation failed at runtime |
