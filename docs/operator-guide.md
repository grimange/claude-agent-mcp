# Operator Guide (v1.1.1)

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

---

## 12. APNTalk verification mode (v1.1.0)

### What it is

`apntalk_verification` is a first-class restricted runtime mode that publishes only two MCP tools:

- `agent_get_runtime_status`
- `agent_verify_task`

All other tools (`agent_run_task`, `agent_continue_session`, `agent_get_session`, `agent_list_sessions`) are not registered. MCP introspection will not list them. Calls to them are rejected.

This mode is:
- **verification-only** — only `agent_verify_task` can execute workflows
- **advisory-only** — the authority posture is `advisory_only`
- **bounded** — allowed directories must be explicit at startup
- **machine-verifiable** — `agent_get_runtime_status` returns exact proof fields
- **fail-closed** — startup aborts if any contract requirement is not satisfied

### Requirements

APNTalk verification mode is **`claude_code` backend only**. The `api` backend is not supported.

Startup will fail if:
- backend is not `claude_code`
- transport is not `stdio`
- allowed directories are missing or not absolute paths

### How to enable

**Via environment variable:**

```bash
export CLAUDE_AGENT_MCP_MODE=apntalk_verification
export CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code
export CLAUDE_AGENT_MCP_TRANSPORT=stdio
export CLAUDE_AGENT_MCP_ALLOWED_DIRS=/path/to/bounded/dir
claude-agent-mcp
```

**Via CLI flag:**

```bash
CLAUDE_AGENT_MCP_EXECUTION_BACKEND=claude_code \
CLAUDE_AGENT_MCP_ALLOWED_DIRS=/path/to/bounded/dir \
claude-agent-mcp --mode apntalk_verification
```

### Exact admitted tool surface

| Tool | Admitted |
|------|----------|
| `agent_get_runtime_status` | Yes |
| `agent_verify_task` | Yes |
| `agent_run_task` | **No** |
| `agent_continue_session` | **No** |
| `agent_get_session` | **No** |
| `agent_list_sessions` | **No** |

### Runtime-status proof

Call `agent_get_runtime_status` to receive machine-readable restriction proof:

```json
{
  "mode": "apntalk_verification",
  "policy_mode": "verification_only",
  "authority_mode": "advisory_only",
  "tool_surface_mode": "restricted",
  "active_profile": "apntalk_verification",
  "backend": "claude_code",
  "transport": "stdio",
  "exposed_tools": ["agent_get_runtime_status", "agent_verify_task"],
  "allowed_directories": ["/path/to/bounded/dir"],
  "restriction_contract_id": "apntalk_verification_v1",
  "restriction_contract_version": 1,
  "fail_closed_enabled": true,
  "restriction_compliance": true,
  "non_compliance_reasons": null,
  "server_version": "1.1.0"
}
```

All restriction proof fields are exact, machine-readable values. `restriction_compliance: true` confirms the contract is fully satisfied.

### Operator startup log

At startup, the server logs a restriction summary at INFO level:

```
APNTalk verification mode ACTIVE — restriction_contract_id=apntalk_verification_v1
  backend=claude_code transport=stdio profile=apntalk_verification
  exposed_tools=['agent_get_runtime_status', 'agent_verify_task']
  allowed_dirs=['/path/to/bounded/dir'] compliance=PASS
```

### Fail-closed behavior

If any contract requirement is not satisfied, startup aborts:

```
APNTalk verification mode startup contract violation(s):
  • backend='api' does not match required_backend='claude_code'
Startup aborted (fail_closed=true). Fix the above before starting in apntalk_verification mode.
```

There is no silent fallback to standard mode.

### Bounded directory requirements

`CLAUDE_AGENT_MCP_ALLOWED_DIRS` must contain at least one entry. All entries must be absolute paths. If omitted, the server defaults to CWD — which is explicitly allowed and surfaced in the runtime status proof.

To use a specific bounded directory:

```bash
export CLAUDE_AGENT_MCP_ALLOWED_DIRS=/home/user/project
```

### Standard mode is unchanged

Activating APNTalk mode is strictly additive. Standard mode behavior and the full tool surface remain identical when `CLAUDE_AGENT_MCP_MODE` is not set or is `standard`.

Restriction proof fields in `agent_get_runtime_status` are `null` in standard mode.

---

## 13. Verification result interpretation (v1.1.1)

`agent_verify_task` returns structured, machine-readable reason codes and assessment fields alongside the traditional `verdict` and `findings`. These fields make it easier to distinguish between a weak request, a policy mismatch, and a genuine evidence failure.

### Richer result fields

In addition to the existing `verdict`, `findings`, `contradictions`, `missing_evidence`, and `restrictions` fields, results now include:

| Field | Type | Description |
|-------|------|-------------|
| `decision` | string | Top-level decision: `verified`, `not_verified`, or `inconclusive` |
| `primary_reason` | string | Most actionable reason code for the outcome |
| `reason_codes` | array | All applicable stable reason codes |
| `operator_guidance` | array | Short actionable hint strings |
| `evidence_sufficiency` | string | `sufficient`, `partial`, or `insufficient` |
| `scope_assessment` | string | `narrow`, `acceptable`, `broad`, or `too_broad` |
| `profile_alignment` | string | `in_profile`, `out_of_profile`, or `restricted_mode_mismatch` |

### Verification reason taxonomy

Reason codes are stable and grouped into three conceptual categories:

**Evidence reasons**
- `sufficient_evidence` — Evidence supports the claim.
- `insufficient_evidence` — Evidence is absent, weak, or inconclusive.

**Request-quality reasons**
- `scope_too_broad` — Request covers too many artifacts or objectives.
- `ambiguous_request` — Verification goal is unclear or multi-valued.
- `missing_required_context` — No named target, artifact, or evidence anchor.
- `non_verifiable_request` — Cannot be answered through passive evidence review.

**Policy/profile reasons**
- `out_of_profile_request` — Request exceeds the active verification profile.
- `restricted_mode_mismatch` — Request is incompatible with APNTalk verification mode.

### Narrow verification requests (recommended form)

These requests produce high-signal results:

```
"Verify whether src/claude_agent_mcp/server.py exposes only the admitted tool pair."
"Check whether the runtime status proof confirms restricted mode is active."
"Verify whether the exposed tool list is exactly ['agent_get_runtime_status', 'agent_verify_task']."
"Confirm that the restriction_contract_id field is 'apntalk_verification_v1'."
```

Each example:
- names a specific artifact or observable property
- has one verification objective
- has a concrete pass/fail criterion

### Broad verification requests (likely to produce low-signal results)

These requests produce weak or inconclusive results:

| Request | Likely codes |
|---------|-------------|
| "Review the whole system." | `scope_too_broad` |
| "Tell me if everything is safe." | `scope_too_broad`, `missing_required_context` |
| "Validate the entire repo." | `scope_too_broad` |
| "Check whether the whole integration is correct." | `scope_too_broad` |
| "Fix the authentication module." | `out_of_profile_request` (standard) or `restricted_mode_mismatch` (APNTalk) |

### APNTalk restricted mode

In APNTalk verification mode, execution-oriented requests (those using verbs like `fix`, `create`, `write`, `modify`) are blocked before session creation. The response will include:

```json
{
  "ok": false,
  "session_id": "",
  "result": {
    "verdict": "fail_closed",
    "decision": "not_verified",
    "primary_reason": "restricted_mode_mismatch",
    "reason_codes": ["restricted_mode_mismatch", "insufficient_evidence"],
    "operator_guidance": [
      "The active APNTalk verification mode only permits bounded advisory verification tasks.",
      "Reframe the request as a specific, observable claim to verify against existing evidence."
    ],
    "profile_alignment": "restricted_mode_mismatch"
  }
}
```

This is a hard policy block. No session is created and no execution occurs.

### Distinguishing failure types

Use `primary_reason` to distinguish:

| `primary_reason` | Meaning |
|-----------------|---------|
| `sufficient_evidence` | Evidence supported the claim |
| `insufficient_evidence` | Evidence was absent or inconclusive |
| `scope_too_broad` | Request was too wide — narrow the scope |
| `out_of_profile_request` | Request exceeded the verification profile |
| `restricted_mode_mismatch` | Request was incompatible with APNTalk mode |
| `missing_required_context` | No named subject or evidence anchor provided |
| `ambiguous_request` | Goal was unclear — clarify with a specific claim |

A request with `primary_reason=scope_too_broad` was not "verified as failing" — it was simply too broad to evaluate. Narrow the request and retry.
