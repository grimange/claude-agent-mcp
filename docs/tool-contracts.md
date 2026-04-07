# claude-agent-mcp v0.1 — Tool Contracts

All v0.1 tools are exposed over stdio MCP transport.

---

## Canonical response envelope

All mutating and workflow tools (`agent_run_task`, `agent_continue_session`, `agent_verify_task`) return this envelope:

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

On failure, `ok` is `false`, `errors` is non-empty, and `status` reflects the session state.

Error objects have this shape:

```json
{
  "code": "string",
  "message": "string",
  "details": {}
}
```

Stable error codes: `validation_error`, `policy_denied`, `session_not_found`, `session_conflict`, `session_status_error`, `provider_runtime_error`, `artifact_persistence_error`, `normalization_error`.

---

## agent_run_task

Start a new Claude-backed task session.

### Request

```json
{
  "task": "string (required)",
  "system_profile": "general | verification  (default: general)",
  "working_directory": "string (default: server CWD)",
  "attachments": ["string"],
  "max_turns": 10,
  "allow_tools": true
}
```

- `task` is required.
- `system_profile` defaults to `general`.
- `max_turns` is capped to the profile maximum (50 for `general`, 20 for `verification`).
- `allow_tools` is advisory; the active profile determines actual tool classes permitted.

### Response

Canonical envelope with `workflow: "run_task"`.

```json
{
  "ok": true,
  "session_id": "sess_abc123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "Task completed.",
  "result": {
    "output_text": "..."
  },
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

---

## agent_continue_session

Continue an existing session with a new message.

### Request

```json
{
  "session_id": "sess_abc123 (required)",
  "message": "string (required)",
  "max_turns": 10
}
```

- `session_id` and `message` are required.
- Session must exist, must not be locked, and must have a resumable status (`completed` or `failed` not allowed).
- Conversation history is replayed from stored events; provider-native session continuity is not used.

### Response

Canonical envelope with `workflow: "continue_session"`.

---

## agent_get_session

Get full detail for a single session.

### Request

```json
{
  "session_id": "sess_abc123 (required)"
}
```

### Response

```json
{
  "session_id": "sess_abc123",
  "workflow": "run_task",
  "profile": "general",
  "status": "completed",
  "created_at": "ISO 8601 timestamp",
  "updated_at": "ISO 8601 timestamp",
  "last_activity_at": "ISO 8601 timestamp",
  "summary_latest": "string | null",
  "artifact_count": 0,
  "turn_count": 1,
  "request_count": 1,
  "working_directory": "string | null"
}
```

On error:

```json
{
  "error": {
    "code": "session_not_found",
    "message": "..."
  }
}
```

---

## agent_list_sessions

List recent sessions with optional status filter.

### Request

```json
{
  "limit": 20,
  "status": "created | running | completed | failed | interrupted (optional)"
}
```

- `limit` defaults to 20, max 200.

### Response

```json
{
  "sessions": [
    {
      "session_id": "sess_abc123",
      "workflow": "run_task",
      "profile": "general",
      "status": "completed",
      "updated_at": "ISO 8601 timestamp",
      "summary_latest": "string | null"
    }
  ]
}
```

---

## agent_verify_task

Run a structured verification workflow.

Always uses the `verification` profile (read-only, fail-closed by default).

### Request

```json
{
  "task": "string (required)",
  "scope": "string (optional)",
  "evidence_paths": ["string"],
  "fail_closed": true,
  "system_profile": "verification"
}
```

- `task` is required.
- `evidence_paths` must exist on disk if provided (checked before execution).
- `fail_closed` defaults to `true`.

### Response

Canonical envelope with `workflow: "verify_task"` and `profile: "verification"`.

```json
{
  "ok": true,
  "session_id": "sess_def456",
  "status": "completed",
  "workflow": "verify_task",
  "profile": "verification",
  "summary": "Verification completed.",
  "result": {
    "verdict": "pass | pass_with_restrictions | fail_closed | insufficient_evidence",
    "findings": ["string"],
    "contradictions": ["string"],
    "missing_evidence": ["string"],
    "restrictions": ["string"]
  },
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

All five `result` fields are always present, even on error paths.

### Verdict rules

| Condition | `fail_closed=true` | `fail_closed=false` |
|-----------|-------------------|-------------------|
| Model says `pass` | `pass` | `pass` |
| Model says `fail_closed` | `fail_closed` | `fail_closed` |
| Model says `pass_with_restrictions` | `pass_with_restrictions` | `pass_with_restrictions` |
| Model says `insufficient_evidence` | `fail_closed` | `insufficient_evidence` |
| Evidence path missing | `fail_closed` (early, `ok=false`) | `fail_closed` (early, `ok=false`) |
| Unrecognized model output | `fail_closed` | `insufficient_evidence` |

---

## Deferred tools (not in v0.1)

The following tools are explicitly out of scope for v0.1:

- `agent_cancel_session`
- `agent_list_artifacts`
- `agent_read_artifact`
- `agent_plan_task`
- `agent_patch_task`
- `agent_summarize_session`
