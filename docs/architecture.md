# claude-agent-mcp v0.1 — Architecture

## Overview

`claude-agent-mcp` is a local-first MCP server exposing a sessioned Claude-backed agent runtime over the Model Context Protocol.

It is **stdio-only**, **single-node**, and **non-daemonized** in v0.1.

---

## System layers

The implementation is composed of six layers:

```
MCP Client (e.g. Claude Code, Codex)
        │ stdio (JSON-RPC)
        ▼
┌─────────────────────────┐
│  MCP Server Layer       │  server.py — tool registration, stdio transport
├─────────────────────────┤
│  Workflow Layer         │  workflow_executor.py — orchestrates execution
├─────────────────────────┤
│  Policy Layer           │  policy_engine.py, profile_registry.py
├─────────────────────────┤
│  Agent Runtime Layer    │  agent_adapter.py — Anthropic Messages API
├─────────────────────────┤
│  Persistence Layer      │  session_store.py — SQLite sessions + events
├─────────────────────────┤
│  Artifact Storage Layer │  artifact_store.py — local filesystem
└─────────────────────────┘
```

---

## Provider adapter

The agent runtime layer wraps the **Anthropic Messages API** (not the Claude Agent SDK).

`ClaudeAdapter` is responsible for:

- calling `client.messages.create()` with the resolved system prompt and conversation history
- collecting text blocks from the response
- returning a `NormalizedProviderResult` — no Anthropic SDK types escape this module

v0.1 is single-shot per turn. There is no tool-use loop.

Continuation replays the stored event-derived conversation history as `messages` to the Messages API. Provider-native session continuity is not used.

---

## Session model

Sessions are owned internally by this repository. The Anthropic API is stateless — no provider-native session ID is stored or used.

Each session record in SQLite contains:

- canonical `session_id` (e.g. `sess_<hex>`)
- `workflow`, `profile`, `provider`, `status`
- timestamps: `created_at`, `updated_at`, `last_activity_at`
- counters: `request_count`, `turn_count`, `artifact_count`
- `summary_latest`
- locking fields: `locked_by`, `lock_expires_at`

Session events are append-only. On continuation, the workflow executor rebuilds the conversation history from stored events before calling the adapter.

### Session statuses

- `created` → `running` → `completed` | `failed`
- `running` → `interrupted` (crash recovery on startup)

`cancelled` is reserved for a future version.

---

## Single-writer locking

Sessions use SQLite-backed row locking with a TTL (default 300s).

- only one mutation may target a session at a time
- stale locks expire after TTL
- on startup, any sessions still in `running` state are reclassified to `interrupted`

Locking is single-process / single-node only. There is no distributed locking.

---

## Profiles as policy bundles

Profiles are not just prompt presets. They are full execution policies.

v0.1 ships two profiles:

| Profile | Read-only | Max turns | Fail-closed |
|---------|-----------|-----------|-------------|
| `general` | No | 50 | No |
| `verification` | Yes | 20 | Yes |

Each profile defines: system prompt, allowed tool classes, working directory policy, turn caps, timeout caps, artifact policy.

---

## Verification workflow

`agent_verify_task` is implemented as a bounded wrapper over the same workflow executor.

It forces the `verification` profile (read-only, fail-closed), validates evidence paths before execution, and parses a structured verdict from the model output.

Possible verdicts: `pass`, `pass_with_restrictions`, `fail_closed`, `insufficient_evidence`.

If the model output does not contain a recognizable verdict:
- `fail_closed=true` → verdict defaults to `fail_closed`
- `fail_closed=false` → verdict defaults to `insufficient_evidence`

---

## Persistence layout

```
.state/
  claude-agent-mcp.db      # SQLite database
  artifacts/
    <session_id>/          # Per-session artifact files
```

SQLite tables: `sessions`, `session_events`, `artifacts`, `schema_migrations`.

WAL mode and foreign keys are enabled.

---

## Canonical response envelope

All mutating and workflow tools return this top-level shape:

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "string",
  "result": {},
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

Workflow-specific output lives inside `result`. The top-level shape is stable across all five tools.

---

## Transport

v0.1 supports **stdio only**.

SSE and Streamable HTTP are deferred.

---

## v0.1 non-goals

- Downstream MCP federation
- SSE or Streamable HTTP transport
- Cancellation (`agent_cancel_session`)
- Public artifact browsing tools
- Multi-tenant hosting
- Background daemons
- Distributed workers
- Patch workflows
