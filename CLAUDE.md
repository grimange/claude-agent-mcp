# CLAUDE.md — claude-agent-mcp Execution Contract

## 1. Purpose

This file defines the execution contract for Claude when working inside the `claude-agent-mcp` repository.

Claude is acting as an **implementation subcontractor** for a bounded Python project.

The repository goal is to implement a **sessioned Claude-backed agent runtime exposed over MCP** with:

- durable sessions
- normalized MCP tool contracts
- policy-bounded execution
- SQLite-backed persistence
- local filesystem artifact storage
- stdio-first MCP transport

Claude must optimize for:

- contract clarity
- implementation correctness
- bounded scope
- maintainability
- testability
- fail-closed behavior

---

## 2. Operating mode

Claude operates in:

> EXECUTION_MODE = BOUNDED_IMPLEMENTATION_SUBCONTRACTOR

Claude must:

- implement only the requested scope
- treat architecture docs and implementation prompts as authoritative
- preserve the v0.1 boundaries
- prefer explicit typed contracts over implicit behavior
- fail closed when requirements or constraints conflict
- keep provider-specific details isolated behind internal adapters

Claude must not:

- widen scope beyond v0.1 without explicit instruction
- introduce background-daemon behavior
- introduce multi-tenant assumptions
- add downstream MCP federation in v0.1
- add patch-application workflows in v0.1
- expose raw provider SDK objects through MCP contracts
- replace internal canonical session state with provider-native state

---

## 3. Authoritative implementation targets

Claude must treat the following as binding design constraints for v0.1:

1. `claude-agent-mcp` is an **MCP server externally**.
2. Internally it owns the **canonical session transcript and metadata**.
3. Provider-native session or thread IDs are optional accelerators, not the source of truth.
4. The public v0.1 MCP surface is limited to:
   - `agent_run_task`
   - `agent_continue_session`
   - `agent_get_session`
   - `agent_list_sessions`
   - `agent_verify_task`
5. All mutating/workflow tools return the **canonical response envelope**.
6. Profiles are **policy bundles**, not just prompt presets.
7. v0.1 transport is **stdio only**.
8. v0.1 persistence is **SQLite + local filesystem**.
9. v0.1 must enforce **single-writer session locking**.
10. v0.1 must support **crash recovery** for stale running sessions.

---

## 4. Required implementation priorities

Claude should implement in this order unless explicitly directed otherwise:

### Priority 1 — Contracts and types

Implement first:

- typed request/response models
- canonical response envelope
- session/status enums
- error taxonomy
- profile schema

Do not begin runtime-heavy work before contracts are explicit.

### Priority 2 — Persistence

Implement next:

- SQLite schema
- session records
- session event log
- session locking
- artifact metadata skeleton

Persistence must be usable before full agent execution is considered complete.

### Priority 3 — Policy enforcement

Implement next:

- profile registry
- policy engine
- working directory validation
- turn limits
- read-only policy behavior
- fail-closed validation rules

### Priority 4 — Provider adapter and workflow execution

Implement next:

- Claude adapter
- workflow executor
- run task flow
- continue session flow
- response normalization

### Priority 5 — MCP tool layer

Implement next:

- tool registration
- tool handlers
- request validation
- normalized MCP responses

### Priority 6 — Verification workflow

Implement after the generic flow is stable:

- `agent_verify_task`
- verification result normalization
- evidence-path validation
- verification profile defaults

### Priority 7 — Hardening and tests

Implement before release:

- crash recovery handling
- stale lock cleanup
- persistence tests
- continuation tests
- policy tests
- tool contract tests

---

## 5. Implementation rules

### 5.1 Contracts first

Claude must define models before wiring behavior.

Avoid premature runtime glue without typed contracts.

### 5.2 Internal source of truth

Claude must ensure the repository’s own persisted state is authoritative.

Do not rely on provider-native state as canonical.

### 5.3 No leaky abstractions

Provider-specific objects, SDK response shapes, or transport-specific quirks must not leak into:

- MCP tool responses
- core internal domain models
- persistent database schema

### 5.4 Thin workflow wrappers

Specialized workflows such as verification must be implemented as bounded wrappers over the shared workflow executor.

Do not fork a separate runtime path unless explicitly required.

### 5.5 Fail closed

When requirements are ambiguous or evidence is insufficient:

- reject invalid inputs
- return structured errors
- prefer denied execution over permissive guesses

### 5.6 Minimize speculative features

Do not scaffold large future abstractions that are not needed for v0.1 correctness.

A small clean design is preferred over an over-generalized framework.

---

## 6. Repository expectations

Claude should preserve or create a structure close to:

```text
src/claude_agent_mcp/
  server.py
  config.py
  logging.py
  errors.py
  types.py

  runtime/
    agent_adapter.py
    workflow_executor.py
    policy_engine.py
    session_store.py
    artifact_store.py
    profile_registry.py

  tools/
    run_task.py
    continue_session.py
    get_session.py
    list_sessions.py
    verify_task.py

  resources/
    session_resource.py
    artifact_resource.py

  prompts/
    system_profiles.py

  db/
    schema.sql
    migrations.py
```

Reasonable refinements are allowed, but v0.1 should remain recognizable against this architecture.

---

## 7. Required response envelope

Claude must preserve this canonical top-level shape for mutating/workflow tool results:

```json
{
  "ok": true,
  "session_id": "sess_123",
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

Workflow-specific outputs belong inside `result`.

Do not invent tool-specific top-level response shapes unless explicitly authorized.

---

## 8. Session rules

Claude must preserve these rules:

- sessions are durable and resumable
- each session has a canonical internal identifier
- each session persists timestamps, status, counts, and summaries
- session events are append-only
- only one active mutation may operate on a session at a time
- stale locks must expire safely
- stale `running` sessions must be recoverable as `interrupted`

Do not implement session handling purely in memory.

---

## 9. Profile rules

v0.1 includes only:

- `general`
- `verification`

Each profile must define policy, not only prompt text.

At minimum each profile should control:

- system prompt
- allowed tool classes
- read-only behavior
- working directory policy
- turn defaults and caps
- timeout defaults and caps
- artifact behavior
- fail-closed expectations

Do not introduce `planner`, `coder`, or `researcher` into the active v0.1 implementation unless explicitly requested.

---

## 10. Deferred features

Claude must treat the following as deferred unless explicitly requested:

- downstream MCP federation
- SSE transport
- Streamable HTTP transport
- patch proposal/apply workflows
- artifact browsing MCP tools
- cancellation workflows
- multi-provider abstraction beyond what is minimally required to isolate Claude runtime details

Deferred features may be noted in docs, but should not drive current implementation complexity.

---

## 11. Testing expectations

Claude should add or maintain tests for:

- typed contract validation
- session persistence across restart
- single-writer lock behavior
- session continuation behavior
- policy enforcement failures
- normalized response envelope shape
- verification workflow structure
- crash recovery behavior

Tests should focus on the repository’s own guarantees, not on reproducing provider internals.

---

## 12. Documentation behavior

When Claude updates docs, it should:

- keep v0.1 boundaries explicit
- separate implemented behavior from deferred roadmap items
- document invariants before examples
- keep examples aligned with actual typed contracts

Do not let README examples drift from the enforced schemas.

---

## 13. Decision rule when uncertain

When multiple implementation options appear valid, Claude should prefer the option that:

1. preserves the v0.1 boundary
2. keeps internal contracts explicit
3. reduces trust-boundary expansion
4. improves testability
5. avoids lock-in to provider-specific runtime behavior

When still uncertain, choose the smaller and more conservative design.

---

## 14. Definition of success

A v0.1 implementation is successful only if:

- MCP stdio integration works
- sessions persist and resume correctly
- canonical envelopes are returned consistently
- profile policies are enforced before execution
- verification runs are structured and fail closed
- crash recovery works for stale running sessions
- the implementation stays within the defined v0.1 scope

