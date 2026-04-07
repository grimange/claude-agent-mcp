# AGENTS.md — claude-agent-mcp Agent and Contribution Rules

## 1. Purpose

This document defines how agents and contributors should work inside the `claude-agent-mcp` repository.

It exists to keep implementation aligned with the v0.1 architecture and to prevent scope drift.

This repository is building a **sessioned Claude-backed runtime exposed over MCP** with strict v0.1 boundaries.

---

## 2. v0.1 mission

Build a local-first MCP server that can:

- run bounded Claude-backed tasks
- persist sessions durably in SQLite
- continue sessions safely
- expose session inspection tools
- run a verification workflow under a constrained profile
- store artifact metadata and local artifact files
- normalize outputs into stable MCP-facing contracts

Everything else is secondary.

---

## 3. Hard boundaries

The following are in scope for v0.1:

- stdio transport
- SQLite persistence
- local filesystem artifacts
- canonical session transcript and metadata
- two built-in profiles: `general`, `verification`
- five public MCP tools:
  - `agent_run_task`
  - `agent_continue_session`
  - `agent_get_session`
  - `agent_list_sessions`
  - `agent_verify_task`
- session locking
- crash recovery for stale running sessions
- typed request and response schemas

The following are out of scope for v0.1:

- downstream MCP federation
- SSE or Streamable HTTP
- patch workflows
- cancellation flows
- public artifact browsing tools
- multi-tenant hosting
- background daemons
- broad plugin ecosystems

Do not blur roadmap ideas into implemented scope.

---

## 4. Core architectural invariants

All contributors and agents must preserve these invariants.

### 4.1 MCP external, runtime internal

The system is an MCP server externally.

The Claude runtime is an internal implementation detail.

### 4.2 Canonical internal state

The repository’s persisted session transcript and metadata are the source of truth.

Provider-native session IDs may exist, but are optional and non-authoritative.

### 4.3 Canonical response envelope

All mutating and workflow tools must return the same top-level envelope:

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

Specialized outputs belong under `result`.

### 4.4 Profiles are policy bundles

Profiles are not just prompts.

They define executable policy, including read/write rules and runtime caps.

### 4.5 Fail closed

When evidence, permissions, or inputs are insufficient, the system should deny or return structured failure rather than infer permissive behavior.

---

## 5. Agent roles inside this repo

Agentic work in this repository should conceptually fit one of these roles.

### 5.1 Contract agent

Responsible for:

- typed models
- response schemas
- enums
- error taxonomy
- tool request/response definitions

Priority: preserve explicit contracts.

### 5.2 Persistence agent

Responsible for:

- SQLite schema
- session store
- event log
- artifact metadata
- lock management
- crash recovery state transitions

Priority: preserve durable, canonical state.

### 5.3 Policy agent

Responsible for:

- profile registry
- policy engine
- working directory restrictions
- turn and timeout validation
- read-only rules

Priority: fail closed.

### 5.4 Runtime agent

Responsible for:

- Claude adapter
- execution normalization
- continuation handling
- workflow executor integration

Priority: no provider leakage.

### 5.5 MCP surface agent

Responsible for:

- server tool registration
- handler wiring
- MCP-facing validation
- stable response formatting

Priority: tool-surface consistency.

### 5.6 Test and hardening agent

Responsible for:

- persistence tests
- lock tests
- continuation tests
- profile-policy tests
- crash recovery tests
- schema/contract tests

Priority: repository guarantees over implementation optimism.

These roles are conceptual. A single contributor or coding agent may perform several of them.

---

## 6. Recommended implementation order

Unless explicitly directed otherwise, work in this order:

1. contracts and schemas
2. database and persistence
3. profile registry and policy engine
4. workflow executor skeleton
5. Claude adapter
6. MCP server wiring
7. verification workflow
8. hardening and tests

Do not start with transport flourish or future extensibility layers.

---

## 7. File ownership guidance

Suggested responsibility map:

- `types.py` → contract agent
- `errors.py` → contract agent
- `runtime/session_store.py` → persistence agent
- `runtime/artifact_store.py` → persistence agent
- `runtime/profile_registry.py` → policy agent
- `runtime/policy_engine.py` → policy agent
- `runtime/agent_adapter.py` → runtime agent
- `runtime/workflow_executor.py` → runtime + contract agents
- `tools/*.py` → MCP surface agent
- `server.py` → MCP surface agent
- `tests/*` → test and hardening agent

This is guidance, not a strict enforcement mechanism.

---

## 8. Coding rules

### 8.1 Prefer typed domain models

Use explicit models for:

- requests
- responses
- session rows
- artifact references
- error payloads
- profile definitions

Avoid loosely structured dictionaries when a stable model should exist.

### 8.2 Keep layers separate

Do not mix:

- MCP transport code
- policy decisions
- provider-specific logic
- database persistence logic

Cross-layer shortcuts are discouraged even if they appear faster.

### 8.3 No provider leakage

Do not expose raw Claude SDK objects or response shapes through internal contracts or MCP responses.

### 8.4 Avoid speculative abstraction

Do not add broad plugin systems, dynamic registries, or future-provider frameworks unless required for v0.1 correctness.

### 8.5 Preserve restart safety

Do not introduce logic that only works if the process never restarts.

---

## 9. Session rules

All session mutations must respect:

- canonical internal session IDs
- append-only session events
- single-writer locking
- explicit status transitions
- crash-recoverable persistence

Expected statuses:

- `created`
- `running`
- `completed`
- `failed`
- `interrupted`

`cancelled` is not active v0.1 scope.

---

## 10. Verification workflow rules

`agent_verify_task` is a specialized workflow, not a separate execution engine.

It must:

- resolve to the `verification` profile
- use fail-closed behavior by default
- validate evidence paths explicitly
- return structured result fields:
  - `verdict`
  - `findings`
  - `contradictions`
  - `missing_evidence`
  - `restrictions`

It must not silently degrade into a general-purpose run path without verification constraints.

---

## 11. Error handling rules

Use explicit internal error types and stable outward error codes.

At minimum preserve the distinction between:

- validation failures
- policy denial
- missing session
- session conflict
- provider runtime failure
- persistence failure
- normalization failure

Do not collapse all failures into generic runtime exceptions.

---

## 12. Test requirements

Every meaningful change should be evaluated against these repository guarantees:

- contract correctness
- session persistence
- correct lock behavior
- continuation correctness
- profile policy enforcement
- normalized result formatting
- verification workflow structure
- crash recovery behavior

A feature is not complete if it only works in the happy path.

---

## 13. Documentation rules

Documentation should:

- distinguish current behavior from roadmap ideas
- document invariants before examples
- show the canonical envelope consistently
- keep the v0.1 surface small and explicit

Do not document deferred features as if they already exist.

---

## 14. Change acceptance rule

A change is acceptable only if it improves the repository without violating:

- v0.1 scope
- canonical session ownership
- policy-bounded execution
- typed contracts
- single-writer session safety
- fail-closed verification behavior

If a proposed change weakens these properties, it should be rejected or deferred.

---

## 15. Preferred design bias

When choosing between two reasonable implementations, prefer the one that is:

1. smaller
2. more explicit
3. easier to test
4. less provider-coupled
5. safer under restart and partial failure

This bias is intentional.

---

## 16. Definition of done for v0.1

v0.1 is only done when:

- the MCP server works over stdio
- the five public tools are implemented
- sessions persist across restart
- continuation works from canonical internal state
- verification is structured and fail closed
- policies are enforced before execution
- stale running sessions recover as `interrupted`
- tests cover the critical invariants

