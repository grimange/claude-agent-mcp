# Operator Guide (v1.0.0)

This guide covers how to configure, deploy, and inspect `claude-agent-mcp` in production.

---

## 1. Choosing an execution backend

`claude-agent-mcp` supports two execution backends:

| Backend | Auth | Tool use | Continuation | Mediation |
|---------|------|----------|--------------|-----------|
| `api` (default) | `ANTHROPIC_API_KEY` | Native (`tool_use` / `tool_result`) | Native multi-turn | No |
| `claude_code` | Claude Code login (`claude login`) | Text-based (limited, opt-in) or mediated | Reconstructed from session events | Yes (opt-in) |

Select with:

```bash
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=api         # default
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
```

---

## 2. Operator profile presets

v1.0.0 introduces **operator profile presets** — named configurations that set sensible defaults for common deployment scenarios. Individual env vars always override preset defaults.

```bash
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=safe_default         # conservative baseline
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=continuity_optimized # longer context windows
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=mediation_enabled    # mediation on
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=workflow_limited     # bounded multi-step workflows
```

### Preset reference

| Preset | Mediation | Max Continuation Turns | Max Workflow Steps | Session Approvals Cap |
|--------|-----------|------------------------|--------------------|-----------------------|
| `safe_default` | Off | 5 | 1 | 10 |
| `continuity_optimized` | Off | 10 | 1 | 10 |
| `mediation_enabled` | On | 5 | 1 | 50 |
| `workflow_limited` | On | 5 | 3 | 30 |

### Precedence rule

```
Individual env var > Operator profile preset > Hardcoded default
```

Example — use the `mediation_enabled` preset but disable mediation for this deployment:

```bash
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=mediation_enabled
export CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION=false  # overrides preset
```

---

## 3. Inspecting the resolved runtime state

Use `agent_get_runtime_status` (additive MCP tool) to see what the runtime believes is enabled:

```json
{
  "tool": "agent_get_runtime_status",
  "arguments": {}
}
```

Response includes:
- `version` — package version
- `operator_profile_preset` — active preset (null if none)
- `backend`, `transport`, `model`
- `federation_enabled`, `federation_active`
- `capability_flags` — what is enabled per config and backend
- `continuation_settings` — resolved continuation window policy
- `mediation_settings` — resolved single-action mediation config
- `workflow_settings` — resolved bounded workflow mediation config
- `preserved_limitations` — known product boundaries
- `resolved_at` — ISO timestamp

The same snapshot is also logged at startup at INFO level.

---

## 4. Session continuation (Claude Code backend)

When using `agent_continue_session` with the `claude_code` backend, the runtime reconstructs context from the persisted session event log. This is not native multi-turn — each invocation is a single CLI call.

Key settings:

```bash
# How many recent turn pairs to include (default: 5)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS=5

# How many warnings to carry forward (default: 3)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_WARNINGS=3

# How many forwarding events to summarize (default: 3)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_FORWARDING_EVENTS=3

# Include prior verification outcomes in continuation context (default: true)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_VERIFICATION_CONTEXT=true

# Include tool downgrade warnings in continuation context (default: true)
export CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_TOOL_DOWNGRADE_CONTEXT=true
```

The `continuity_optimized` preset applies larger defaults for these values.

### Continuation observability

Session events are recorded for every continuation reconstruction:
- `session_continuation_context_built` — policy used, truncation stats
- `session_continuation_context_truncated` — only when truncation occurred
- `session_continuation_prompt_rendered` — reconstruction version

Inspect via `agent_get_session` and the session event log.

---

## 5. Execution mediation (Claude Code backend)

> **Mediation is disabled by default.** Enable explicitly.

Mediation allows the Claude Code backend to embed structured action requests in its output. The runtime (not the backend) validates and dispatches approved requests to federated tools.

> **This is not native tool calling.** The Claude Code CLI has no tool invocation protocol. Mediated execution is runtime-dispatched text-pattern detection.

### Requirements

1. Federation must be active (`CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true` with a valid config)
2. Mediation must be enabled (`CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION=true`)
3. Target tools must be federation-visible for the active profile

### Single-action mediation (v0.8.0)

```bash
export CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION=true
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_ACTIONS_PER_TURN=1
export CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_ACTION_TYPES=read,lookup,inspect
```

### Bounded workflow mediation (v0.9.0)

```bash
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_WORKFLOW_STEPS=3
export CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_TOOLS=tool_a,tool_b
export CLAUDE_AGENT_MCP_CLAUDE_CODE_DENIED_MEDIATED_TOOLS=dangerous_tool
export CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_SESSION_MEDIATED_APPROVALS=30
```

### Mediation rejection diagnostics

All rejections produce a `MediationRejectionReason` code:

| Code | Meaning |
|------|---------|
| `feature_disabled` | Mediation is off in config |
| `federation_inactive` | Federation not initialized |
| `tool_not_visible` | Tool not visible for profile |
| `tool_not_allowed` | Tool in denied list |
| `per_turn_limit_exceeded` | Per-turn action limit hit |
| `session_approval_limit_exceeded` | Session approval cap reached |
| `workflow_step_limit_exceeded` | Workflow step count exceeds limit |
| `unsupported_action_type` | Action type not in allowed set |
| `invalid_version` | Mediation version mismatch |
| `malformed_request` | Request could not be parsed |

---

## 6. Audit and observability

### Warning categories

All operator-facing warnings are prefixed with a stable category code:

| Prefix | Meaning |
|--------|---------|
| `[tool_downgrade]` | Federation tools not forwarded to backend |
| `[tool_forwarding_incompatible]` | Per-tool injection incompatibility |
| `[history_truncated]` | Continuation history truncated |
| `[stop_reason_limited]` | `stop_reason` is `backend_defaulted` |
| `[empty_response]` | Claude Code CLI returned empty output |
| `[mediation_rejected]` | Mediated action rejected by policy |
| `[federation_inactive]` | Mediation failed — federation not active |
| `[continuation_context_truncated]` | Continuation context window applied |

### Programmatic audit summaries

Use `AuditPresenter` (internal) for structured summaries:

```python
from claude_agent_mcp.runtime.audit_presenter import AuditPresenter

totals = AuditPresenter.session_totals(session, events)
mediation = AuditPresenter.mediation_summary(events)
continuation = AuditPresenter.continuation_summary(events)
```

---

## 7. Federation

Federation connects downstream MCP servers to the runtime. Required for mediated execution.

```bash
export CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true
export CLAUDE_AGENT_MCP_FEDERATION_CONFIG=/path/to/federation.json
```

When federation is inactive and mediation is attempted, all requests are rejected with `federation_inactive`.

See `docs/federation.md` for full configuration details.

---

## 8. Storage and database

```bash
export CLAUDE_AGENT_MCP_STATE_DIR=/var/lib/claude-agent-mcp
export CLAUDE_AGENT_MCP_DB_PATH=/var/lib/claude-agent-mcp/claude-agent-mcp.db    # override
export CLAUDE_AGENT_MCP_ARTIFACT_DIR=/var/lib/claude-agent-mcp/artifacts          # override
```

SQLite is the persistence backend. Sessions survive process restarts. Stale `running` sessions are recovered as `interrupted` on startup.

---

## 9. Session locking

Only one active mutation may operate on a session at a time. Stale locks expire after:

```bash
export CLAUDE_AGENT_MCP_LOCK_TTL=300   # seconds, default 300
```

---

## 10. Preserved limitations

The following are explicit product boundaries in v1.0.0:

- **No native `tool_use` / `tool_result`** in the Claude Code backend. Mediated execution is runtime-dispatched, not backend-native tool calling.
- **No streaming transport** — stdio is the production transport. streamable-http is available but not the primary supported transport.
- **No cross-backend session migration** — sessions are bound to the backend active at creation.
- **No broad autonomous execution chaining** — per-turn and per-session mediation limits are enforced.
- **Mediated execution requires active federation** — without federation, mediated requests are rejected.

---

## 11. Quick-start examples

### Plain API backend

```bash
export ANTHROPIC_API_KEY=sk-ant-...
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=api
claude-agent-mcp
```

### Claude Code backend with continuity-optimized preset

```bash
claude login   # authenticate Claude Code
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=continuity_optimized
claude-agent-mcp
```

### Claude Code backend with mediation and federation

```bash
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=mediation_enabled
export CLAUDE_AGENT_MCP_FEDERATION_ENABLED=true
export CLAUDE_AGENT_MCP_FEDERATION_CONFIG=/path/to/federation.json
claude-agent-mcp
```

See `docs/upgrade-guide-v1.0.md` for migration notes from earlier versions.
