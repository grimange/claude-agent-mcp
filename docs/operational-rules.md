# claude-agent-mcp v0.1 — Operational Rules

## Transport

v0.1 is **stdio-only**. The server communicates exclusively over stdin/stdout using the MCP JSON-RPC framing. SSE and Streamable HTTP are deferred.

---

## State storage

All state is stored in `.state/` by default:

```
.state/
  claude-agent-mcp.db      # SQLite database (WAL mode)
  artifacts/
    <session_id>/          # Per-session artifact files
```

Override the state directory with `CLAUDE_AGENT_STATE_DIR`.

The SQLite database is single-process / single-node. Do not run multiple server instances against the same database.

---

## Session locking

Sessions use SQLite-backed row locks with a configurable TTL (`CLAUDE_AGENT_LOCK_TTL_SECONDS`, default 300s).

- Only one mutation may target a session at a time.
- If a session is locked, concurrent requests return a `session_conflict` error.
- Stale locks (TTL exceeded) are cleared automatically.

---

## Crash recovery

On startup, the server reclassifies any sessions in `running` status to `interrupted`. This is safe — persisted transcript events and artifact metadata remain intact.

Stale `running` sessions are never left permanently ambiguous.

---

## Working directory policy

Execution is restricted to directories in `CLAUDE_AGENT_ALLOWED_DIRS` (comma-separated, defaults to CWD at startup).

Requests with a `working_directory` outside the allowlist are rejected with `policy_denied` before execution begins.

The `verification` profile requires an explicit working directory (no CWD fallback by default).

---

## Profile policies

### general

- Read/write file access
- Max 10 turns default, 50 turns cap
- Timeout: 300s default, 900s cap
- Artifacts: any type

### verification

- Read-only file access
- Max 5 turns default, 20 turns cap
- Timeout: 180s default, 600s cap
- Artifacts: verification-report type only
- Fail-closed: yes

---

## Artifact limits

Artifact body size is capped at `CLAUDE_AGENT_MAX_ARTIFACT_BYTES` (default 10MB). Artifacts exceeding this limit are rejected with `artifact_persistence_error`.

---

## Session statuses

| Status | Meaning |
|--------|---------|
| `created` | Session record created, not yet running |
| `running` | Actively executing (lock held) |
| `completed` | Execution finished normally |
| `failed` | Execution ended with an error |
| `interrupted` | Execution was in progress when the process exited |

`cancelled` is not implemented in v0.1.

---

## Error codes

| Code | Meaning |
|------|---------|
| `validation_error` | Input failed schema or constraint validation |
| `policy_denied` | Profile policy blocked the request |
| `session_not_found` | No session with the given ID |
| `session_conflict` | Session is locked by another execution |
| `session_status_error` | Session is in a state that does not permit the requested operation |
| `provider_runtime_error` | Anthropic API call failed |
| `artifact_persistence_error` | Failed to write artifact to disk or database |
| `normalization_error` | Failed to normalize provider output into the internal result model |

---

## What is not in v0.1

- No cancellation (`agent_cancel_session`)
- No public artifact browsing (`agent_list_artifacts`, `agent_read_artifact`)
- No downstream MCP federation
- No SSE or HTTP transport
- No patch workflows
- No multi-tenant hosting
- No background daemons
- No distributed workers
