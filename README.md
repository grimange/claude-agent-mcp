# claude-agent-mcp

A sessioned Claude-backed agent runtime exposed over MCP (Model Context Protocol).

## Overview

`claude-agent-mcp` is a local-first MCP server that provides:

- **Durable sessions** — sessions persist in SQLite and survive process restarts
- **Claude-backed task execution** — bounded agent tasks via the Anthropic API
- **Stable MCP tool contracts** — predictable request/response schemas
- **Policy-bounded execution** — profiles control permissions, turn limits, and behavior
- **Structured verification** — evidence-based evaluation with fail-closed semantics

v0.1 is stdio-only, single-node, and non-daemonized.

---

## v0.1 Tool Surface

| Tool | Description |
|------|-------------|
| `agent_run_task` | Run a new Claude-backed task session |
| `agent_continue_session` | Continue an existing session |
| `agent_get_session` | Get session detail by ID |
| `agent_list_sessions` | List recent sessions |
| `agent_verify_task` | Run a structured verification workflow |

---

## Setup

### Requirements

- Python 3.11+
- Anthropic API key

### Install

```bash
git clone <repo>
cd claude-agent-mcp
pip install -e ".[dev]"
```

### Configure

```bash
cp .env.example .env
# Edit .env and add your ANTHROPIC_API_KEY
```

### Run

```bash
claude-agent-mcp
```

Or via Python:

```bash
python -m claude_agent_mcp.server
```

---

## MCP Client Configuration

Add to your MCP client config (e.g. `~/.config/mcp/config.json`):

```json
{
  "mcpServers": {
    "claude-agent-mcp": {
      "command": "claude-agent-mcp",
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-..."
      }
    }
  }
}
```

---

## Tool Usage

### agent_run_task

```json
{
  "task": "Summarize the files in this directory",
  "system_profile": "general",
  "working_directory": "/path/to/project",
  "max_turns": 5
}
```

Response:

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "Task completed...",
  "result": { "output_text": "..." },
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

### agent_continue_session

```json
{
  "session_id": "sess_abc123",
  "message": "Can you expand on point 3?"
}
```

### agent_get_session

```json
{
  "session_id": "sess_abc123"
}
```

### agent_list_sessions

```json
{
  "limit": 10,
  "status": "completed"
}
```

### agent_verify_task

```json
{
  "task": "Verify that the implementation matches the spec",
  "scope": "authentication module",
  "evidence_paths": ["/path/to/spec.md", "/path/to/impl.py"],
  "fail_closed": true
}
```

Returns a verdict: `pass`, `pass_with_restrictions`, `fail_closed`, or `insufficient_evidence`.

---

## Profiles

### `general`

For bounded general task execution. Read/write access. Up to 50 turns max.

### `verification`

For evidence-based evaluation. Read-only. Fail-closed by default. Up to 20 turns max.

---

## State Storage

State is stored in `.state/` by default:

```
.state/
  claude-agent-mcp.db      # SQLite database
  artifacts/
    <session_id>/          # Per-session artifact files
```

Override with `CLAUDE_AGENT_STATE_DIR` environment variable.

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | required | Anthropic API key |
| `CLAUDE_AGENT_STATE_DIR` | `.state` | State storage directory |
| `CLAUDE_AGENT_MODEL` | `claude-sonnet-4-6` | Claude model |
| `CLAUDE_AGENT_LOCK_TTL_SECONDS` | `300` | Session lock TTL |
| `CLAUDE_AGENT_ALLOWED_DIRS` | CWD | Comma-separated allowed working directories |
| `CLAUDE_AGENT_MAX_ARTIFACT_BYTES` | `10485760` | Max artifact size (10MB) |
| `CLAUDE_AGENT_LOG_LEVEL` | `INFO` | Log level |

---

## Tests

```bash
pytest tests/ -v
```

---

## Known Limitations (v0.1)

- **stdio transport only** — SSE and Streamable HTTP are deferred
- **No cancellation** — `agent_cancel_session` is not implemented
- **No public artifact browsing** — `agent_list_artifacts` / `agent_read_artifact` are deferred
- **No downstream MCP federation** — the server does not proxy to downstream MCP servers
- **Single-node only** — no distributed workers or multi-tenant hosting
- **Messages API (stateless)** — each continuation replays transcript history; provider-native session continuity is not used in v0.1
