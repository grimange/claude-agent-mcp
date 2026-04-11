# Setting up claude-agent-mcp in OpenAI Codex

This guide walks through adding `claude-agent-mcp` as an MCP server in OpenAI Codex so that Codex can call the full `claude-agent-mcp` tool surface.

If you arrived here from PyPI, start at [Prerequisites](#prerequisites). If you arrived from the repo, you can skip to [Recommended first setup](#recommended-first-setup).

---

## What this integration is

`claude-agent-mcp` is a standalone MCP server. It runs as a local process and speaks the Model Context Protocol over stdio.

Codex supports MCP servers through its `config.toml` file or via the `codex mcp add` CLI. Once registered, Codex treats `claude-agent-mcp` tools the same as any other MCP tool — they appear in the tool list and can be called during task execution.

This lets Codex invoke:

- `agent_run_task` — run a Claude-backed task in a durable session
- `agent_continue_session` — continue an existing session
- `agent_get_session` / `agent_list_sessions` — inspect session history
- `agent_verify_task` — run a structured evidence-based verification
- `agent_get_runtime_status` — confirm the server is running and configured correctly

`claude-agent-mcp` is not a Codex plugin and does not replace Codex's native tools. It adds a separately-governed Claude agent runtime that Codex can delegate work to.

---

## Prerequisites

- Python 3.11 or later
- `claude-agent-mcp` installed:

```bash
pip install claude-agent-mcp
```

- For the **`api` backend** (recommended for first-time setup):
  - An Anthropic API key (`ANTHROPIC_API_KEY`)

- For the **`claude_code` backend**:
  - The `claude` CLI installed and authenticated:

```bash
claude login
```

Confirm the CLI is working:

```bash
claude --version
```

---

## Recommended first setup

For most users, the simplest and most reliable starting point is:

- **Backend**: `api`
- **Transport**: `stdio` (the default — no extra configuration needed)
- **Config location**: user-level `~/.codex/config.toml`

This requires only an Anthropic API key and no additional tooling. The `claude_code` backend is a good alternative if you already have Claude Code installed and prefer not to use a direct API key.

---

## Option 1 — config.toml setup

### API backend

Edit `~/.codex/config.toml` and add:

```toml
[mcp_servers.claude-agent-mcp]
command = "claude-agent-mcp"

[mcp_servers.claude-agent-mcp.env]
ANTHROPIC_API_KEY = "sk-ant-..."
```

Replace `sk-ant-...` with your actual API key.

If `claude-agent-mcp` is not on your system PATH (e.g., it is installed in a virtualenv), use the full path to the binary:

```toml
[mcp_servers.claude-agent-mcp]
command = "/path/to/.venv/bin/claude-agent-mcp"

[mcp_servers.claude-agent-mcp.env]
ANTHROPIC_API_KEY = "sk-ant-..."
```

### Claude Code backend

```toml
[mcp_servers.claude-agent-mcp]
command = "claude-agent-mcp"

[mcp_servers.claude-agent-mcp.env]
CLAUDE_AGENT_MCP_EXECUTION_BACKEND = "claude_code"
```

The `claude` CLI must be installed, on PATH, and already authenticated. No `ANTHROPIC_API_KEY` is needed.

If the `claude` binary is not on PATH, set the explicit path:

```toml
[mcp_servers.claude-agent-mcp.env]
CLAUDE_AGENT_MCP_EXECUTION_BACKEND = "claude_code"
CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH = "/usr/local/bin/claude"
```

### Optional: set a state directory

By default, session state is written to `.state/` in the working directory. To use a fixed location:

```toml
[mcp_servers.claude-agent-mcp.env]
ANTHROPIC_API_KEY = "sk-ant-..."
CLAUDE_AGENT_MCP_STATE_DIR = "/home/you/.claude-agent-mcp/state"
```

---

## Option 2 — codex mcp add

You can register the server using the Codex CLI instead of editing `config.toml` directly.

### API backend

```bash
codex mcp add claude-agent-mcp \
  --command "claude-agent-mcp" \
  --env "ANTHROPIC_API_KEY=sk-ant-..."
```

### Claude Code backend

```bash
codex mcp add claude-agent-mcp \
  --command "claude-agent-mcp" \
  --env "CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code"
```

These commands write to `~/.codex/config.toml`. You can review or edit the result there.

---

## User config vs. project config

| Location | Scope |
|----------|-------|
| `~/.codex/config.toml` | Applies to all Codex sessions for your user |
| `.codex/config.toml` (in a project directory) | Applies only when Codex is run from that directory |

**Recommended**: start with `~/.codex/config.toml`. Move to project-level config if you need different backends or state directories per project.

---

## Verification steps

After adding the config, verify the integration works end to end.

### 1. Confirm the server binary is reachable

```bash
claude-agent-mcp --version
```

This should print the version number. If it fails with "command not found", see [Troubleshooting](#troubleshooting).

### 2. Confirm Codex sees the MCP server

Start a Codex session. In the tool or model info panel, `claude-agent-mcp` should appear in the connected MCP servers list.

### 3. Confirm the tools appear

The following tools should be listed under `claude-agent-mcp`:

- `agent_run_task`
- `agent_continue_session`
- `agent_get_session`
- `agent_list_sessions`
- `agent_verify_task`
- `agent_get_runtime_status`

### 4. Call agent_get_runtime_status

Ask Codex to call `agent_get_runtime_status` with an empty input `{}`.

A successful response looks like:

```json
{
  "ok": true,
  "backend": "api",
  "profiles": ["general", "verification"],
  ...
}
```

This confirms the server is running, authenticated, and reporting its resolved configuration.

### 5. Run a small task

Ask Codex to call `agent_run_task` with a minimal request:

```json
{
  "task": "Say hello and return a one-sentence confirmation.",
  "system_profile": "general"
}
```

A successful response includes `"ok": true`, a `session_id`, and output in `result.output_text`.

### 6. Confirm session persistence

Call `agent_list_sessions` and confirm the session from the previous step appears.

```json
{
  "limit": 5
}
```

---

## Troubleshooting

### `claude-agent-mcp` command not found

The binary is not on PATH. Either:

- Activate the virtualenv where the package is installed, or
- Use the full path in `config.toml`:

```toml
[mcp_servers.claude-agent-mcp]
command = "/full/path/to/claude-agent-mcp"
```

To find the installed path:

```bash
pip show claude-agent-mcp
which claude-agent-mcp
```

### Wrong Python version or virtualenv

Verify your environment:

```bash
python --version   # must be 3.11+
pip show claude-agent-mcp
```

If you use multiple Python environments, install the package in the same environment that Codex will use when spawning the server, or use the full binary path in `config.toml`.

### Missing ANTHROPIC_API_KEY

The `api` backend exits at startup if `ANTHROPIC_API_KEY` is not set. Confirm the key is set in the `[mcp_servers.claude-agent-mcp.env]` block and that it is valid.

### `claude` CLI not installed or not authenticated (claude_code backend)

Verify:

```bash
claude --version
claude login   # if not already authenticated
```

The `claude_code` backend will fail at startup with `claude_code_unavailable` if the CLI is not found or not executable.

### Codex config in wrong location

Codex reads `~/.codex/config.toml` for user-level config and `.codex/config.toml` for project-level config. A common mistake is placing the file at `~/.config/codex/config.toml` or similar. Confirm the path:

```bash
cat ~/.codex/config.toml
```

### Server starts but Codex does not expose the tools

- Confirm the `[mcp_servers.claude-agent-mcp]` section name matches exactly (case-sensitive)
- Confirm the `command` value resolves to a valid executable
- Restart Codex after changing `config.toml`
- Check Codex logs or the MCP server panel for startup errors

### Startup timeout or path issues

If the server times out on startup:

- Confirm the command resolves quickly — avoid using shell wrappers that do slow initialization
- Set `CLAUDE_AGENT_MCP_LOG_LEVEL=DEBUG` in the env block and check server stderr for errors

---

## Safety and expectations

### Backend choice changes execution behavior

The `api` backend uses the Anthropic Messages API directly. The `claude_code` backend delegates to the `claude` CLI. Both expose the same MCP tools, but:

- The `claude_code` backend uses single-shot CLI invocations, not a native multi-turn loop
- Federation tool-use is not supported with the `claude_code` backend
- Stop reason detection differs between backends

Start with `api` unless you have a specific reason to use `claude_code`.

### claude_code backend is not the same as native Codex tools

`claude-agent-mcp` with the `claude_code` backend runs a separate Claude agent process. It is governed by `claude-agent-mcp`'s own session model, policy engine, and configuration. It is not a direct pass-through to Codex's internal model or tool-use loop.

### Start minimal

Avoid enabling federation, mediation, or advanced features in your first setup. Confirm that `agent_run_task` and `agent_get_runtime_status` work correctly before layering in additional configuration.

### Federation and mediation are optional

`CLAUDE_AGENT_MCP_FEDERATION_ENABLED` defaults to `false`. Runtime-mediated execution (`CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION`) also defaults to `false`. Neither is needed for basic Codex integration.

---

## See also

- [README.md](../README.md) — project overview, quick start, and full configuration reference
- [docs/execution-backends.md](execution-backends.md) — detailed backend comparison
- [docs/claude-code-backend.md](claude-code-backend.md) — Claude Code backend reference
- [docs/deployment.md](deployment.md) — general deployment guide
