# Claude Code Backend (v0.4)

## Overview

The Claude Code backend executes tasks through the Claude Code CLI (`claude`) rather than the Anthropic Messages API directly. This allows operators who have Claude Code installed and authenticated to run `claude-agent-mcp` without configuring an `ANTHROPIC_API_KEY`.

> **Auth model:** Claude Code backend uses Claude Code's own login state. It does **not** use `ANTHROPIC_API_KEY`. These are distinct authentication mechanisms and must not be conflated.

## Prerequisites

1. **Claude Code must be installed.**
   Install it from [claude.ai/code](https://claude.ai/code).

2. **Claude Code must be authenticated.**
   Run `claude login` in the operator's environment to establish a session.

3. **The `claude` binary must be accessible.**
   Either on `PATH`, or configured via `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH`.

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

## Startup validation

At server startup, `claude-agent-mcp` will:

1. Look for the `claude` binary (via configured path or `PATH`).
2. Run `claude --version` to confirm the CLI is executable.
3. Fail with a clear error if either check fails.

Authentication state is **not** verified at startup. An unauthenticated Claude Code session will produce a `claude_code_invocation_error` at execution time with the CLI's error output.

## How execution works

For each task, the Claude Code backend:

1. Builds a single prompt string from the system prompt, conversation history, and user task.
2. Invokes `claude --print <prompt>` as a subprocess.
3. Collects stdout as the output text.
4. Returns a `NormalizedProviderResult` (same internal contract as the API backend).

## Known limitations (v0.4)

- **Single-turn only.** Each execution is a single CLI invocation. There is no native multi-turn API loop.
- **No federation tool-use.** Downstream federation tools are not forwarded to the Claude Code CLI. A warning is included in the response when tools would have been visible.
- **Conversation history is plain text.** Prior turns are serialized as labeled text blocks in the prompt. This is less precise than the structured Messages API format.
- **No stop_reason.** The CLI does not expose `stop_reason`; it is always reported as `end_turn`.
- **Model selection.** The `--model` flag is passed to the CLI, but not all model identifiers may be accepted by Claude Code. If the CLI rejects the model, the invocation will fail with `claude_code_invocation_error`.

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

## Comparison with API backend

| | `api` backend | `claude_code` backend |
|---|---|---|
| Auth | `ANTHROPIC_API_KEY` | `claude login` |
| Multi-turn | Native (Messages API) | Single-shot CLI |
| Federation tools | Supported | Not supported (v0.4) |
| Recommended for | Server deployments | Local Claude Code workflows |
