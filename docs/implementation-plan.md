# claude-agent-mcp v0.1 — Revised Architecture Specification

## 1. Purpose

`claude-agent-mcp` is a standalone Python repository that exposes a **sessioned Claude-backed agent runtime over MCP**.

Externally, it behaves as an **MCP server**.

Internally, it provides:

- durable session management
- controlled Claude agent execution
- artifact storage and retrieval
- profile-based runtime policy
- stable, normalized tool responses

v0.1 is intentionally **local-first**, **single-node**, and **non-daemonized**. It is designed to be safely usable by MCP clients such as Codex or other agentic hosts without introducing autonomous background behavior.

---

## 2. Product goals

v0.1 exists to provide five core capabilities:

1. **Durable sessions**  
   Sessions survive process restarts and can be resumed explicitly.

2. **Claude-backed task execution**  
   The runtime can execute bounded agent tasks through the Claude Agent SDK.

3. **Stable MCP tool contracts**  
   MCP clients interact through predictable request and response schemas.

4. **Artifact persistence**  
   Sessions can produce retrievable artifacts such as reports, plans, and summaries.

5. **Policy-bounded execution**  
   Profiles and guardrails constrain what an execution may do.

---

## 3. Non-goals for v0.1

v0.1 does **not** include:

- multi-tenant hosting
- distributed workers
- autonomous background task execution
- dynamic plugin loading from untrusted sources
- unrestricted shell-like execution
- automatic downstream MCP federation
- patch application by default
- web-scale deployment assumptions

These may be added later, but the architecture for v0.1 should not depend on them.

---

## 4. System model

### 4.1 External role

`claude-agent-mcp` is an MCP server that exposes a small, well-defined tool surface.

Clients call MCP tools such as:

- `agent_run_task`
- `agent_continue_session`
- `agent_get_session`
- `agent_list_sessions`
- `agent_verify_task`

### 4.2 Internal role

Internally, the system is composed of six layers:

1. **MCP Server Layer**  
   Receives tool calls, validates input, returns normalized results.

2. **Workflow Layer**  
   Maps tool calls to runtime actions and specialized workflows.

3. **Policy Layer**  
   Applies profile constraints, filesystem rules, turn caps, and other guardrails.

4. **Agent Runtime Layer**  
   Invokes the Claude Agent SDK and normalizes provider behavior.

5. **Persistence Layer**  
   Stores sessions, events, and artifact metadata in SQLite.

6. **Artifact Storage Layer**  
   Stores artifact bodies on the local filesystem.

This separation is mandatory. MCP-facing contracts must not directly depend on provider-specific runtime objects.

---

## 5. Architectural principles

The v0.1 implementation must follow these principles:

### 5.1 MCP contract stability first

The external API is the MCP tool surface, not the internal Claude SDK contract.

### 5.2 Internal transcript is canonical

The runtime may use provider-specific session features internally, but the source of truth must be the system’s own persisted session transcript and metadata.

### 5.3 Profiles are policy bundles

A profile is not only a prompt preset. It is a full execution policy.

### 5.4 Workflow tools are thin wrappers

Specialized tools like verification must be implemented as bounded wrappers over the same core runtime.

### 5.5 Fail closed by default

When inputs, permissions, or evidence are insufficient, the runtime should return bounded failure states rather than improvise.

---

## 6. Repo structure

Recommended v0.1 repo structure:

```text
claude-agent-mcp/
  pyproject.toml
  README.md
  .env.example

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

  tests/
    test_server.py
    test_sessions.py
    test_runtime.py
    test_tools.py
    test_verify_workflow.py

  docs/
    architecture.md
    tool-contracts.md
    operational-rules.md
    session-model.md
```

### Notes on revision

Compared with the original structure, this spec makes a few intentional changes:

- `agent_runner.py` becomes `agent_adapter.py` to emphasize provider isolation.
- `profiles.py` becomes `profile_registry.py` to make profile loading and resolution explicit.
- `guardrails.py` becomes `policy_engine.py` to broaden the concept from ad hoc checks to structured policy.
- `downstream_mcp.py` is removed from v0.1 core scope and deferred.

---

## 7. Public MCP surface for v0.1

v0.1 should expose only the following tools:

- `agent_run_task`
- `agent_continue_session`
- `agent_get_session`
- `agent_list_sessions`
- `agent_verify_task`

The following are explicitly deferred beyond v0.1:

- `agent_cancel_session`
- `agent_list_artifacts`
- `agent_read_artifact`
- `agent_plan_task`
- `agent_patch_task`
- `agent_summarize_session`

This narrower surface reduces implementation risk while preserving a usable first release.

---

## 8. Tool categories

The exposed tools fall into three categories:

### 8.1 Core runtime tools

- `agent_run_task`
- `agent_continue_session`

These create and advance sessions.

### 8.2 Inspection tools

- `agent_get_session`
- `agent_list_sessions`

These expose persisted state safely.

### 8.3 Workflow tools

- `agent_verify_task`

This is a specialized wrapper over the core runtime using a constrained profile and normalized result shape.

This classification should exist in code, not only in documentation.

---

## 9. Canonical response envelope

All mutating and workflow tools in v0.1 should return a shared top-level response shape.

### 9.1 Standard envelope

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

### Field meanings

- `ok`: boolean indicating whether the request executed successfully at the tool-contract level
- `session_id`: canonical internal session identifier
- `status`: normalized runtime state
- `workflow`: logical workflow name
- `profile`: resolved profile used for execution
- `summary`: concise human-readable result summary
- `result`: workflow-specific structured payload
- `artifacts`: list of artifact references generated during execution
- `warnings`: non-fatal issues
- `errors`: structured errors or failure explanations

### 9.2 Why this matters

Workflow-specific schemas should live inside `result`, not at the top level. This keeps the MCP surface consistent and easier to consume.

---

## 10. Session model

### 10.1 Session definition

A session is the canonical persisted unit of execution state for a Claude-backed conversation or workflow.

A session must be owned by `claude-agent-mcp`, not by the underlying provider.

### 10.2 Session semantics

The system uses a **hybrid session model**:

- the internal transcript is the source of truth
- provider-native session/thread identifiers may be stored as optional runtime accelerators
- continuation must remain possible even if provider-native session linkage is absent or invalid

### 10.3 Session fields

Each session record must include:

- `session_id`
- `workflow`
- `profile`
- `provider`
- `provider_session_id` nullable
- `status`
- `working_directory`
- `created_at`
- `updated_at`
- `last_activity_at`
- `request_count`
- `turn_count`
- `artifact_count`
- `summary_latest`
- `locked_by` nullable
- `lock_expires_at` nullable

### 10.4 Session event log

Each session must also have an append-only event log or transcript table containing events such as:

- user input
- system prompt resolution
- policy decisions
- provider request start
- provider response summary
- artifact emission
- workflow normalization
- error event

This event log is critical for debugging and replay.

### 10.5 Session statuses

Allowed session statuses for v0.1:

- `created`
- `running`
- `completed`
- `failed`
- `interrupted`

`cancelled` is reserved for later versions when cancellation semantics are implemented.

---

## 11. Session concurrency and locking

v0.1 must enforce **single-writer semantics per session**.

Rules:

- only one active mutation may target a session at a time
- concurrent `agent_continue_session` requests for the same session must not run simultaneously
- if a session is already locked, the tool should return a normalized conflict error
- stale locks must expire safely

This behavior must be implemented in SQLite-backed state, not only in memory.

---

## 12. Session continuation rules

`agent_continue_session` must obey these rules:

- the target session must exist
- the session must not be locked by another active execution
- the session status must be compatible with continuation
- the session’s policy must still allow continuation
- the new message must append to the canonical transcript
- the runtime must produce a new normalized result and update session summaries

If a provider-native session reference exists, it may be used. If it fails, the system should fall back to replaying internal context as needed.

---

## 13. Artifact model

Artifacts are outputs generated by session workflows and stored durably.

### 13.1 Artifact storage design

- artifact metadata: SQLite
- artifact body: local filesystem

### 13.2 Artifact metadata fields

Each artifact must store:

- `artifact_id`
- `session_id`
- `workflow`
- `profile`
- `artifact_type`
- `logical_name`
- `mime_type`
- `path`
- `size_bytes`
- `sha256`
- `created_at`
- `turn_index`
- `producer_tool`

### 13.3 Artifact reference shape

The response envelope should expose artifact references as structured objects such as:

```json
{
  "artifact_id": "art_123",
  "artifact_type": "report",
  "logical_name": "verification-report.md",
  "mime_type": "text/markdown"
}
```

### 13.4 v0.1 artifact scope

v0.1 may create artifacts internally, but full public artifact browsing tools are deferred until after the first release.

---

## 14. Profiles as policy bundles

v0.1 ships the following built-in profiles:

- `general`
- `verification`

`planner`, `coder`, and `researcher` are deferred until later versions.

### 14.1 Profile structure

Each profile must define:

- `name`
- `system_prompt`
- `allowed_tool_classes`
- `read_only`
- `working_directory_policy`
- `max_turns_default`
- `max_turns_max`
- `timeout_seconds_default`
- `timeout_seconds_max`
- `artifact_policy`
- `result_schema`
- `fail_closed`

### 14.2 Profile behavior

#### `general`

For bounded general task execution.

#### `verification`

For evidence-based evaluation with fail-closed behavior and read-only defaults.

`verification` must not silently downgrade evidence requirements.

---

## 15. Policy engine

The policy engine is responsible for validating and enforcing runtime constraints before execution begins.

### 15.1 Policy domains

The policy engine must evaluate:

- profile selection
- working directory rules
- read-only mode
- turn limits
- timeout limits
- attachment rules
- artifact size rules

### 15.2 Tool classes

Even in v0.1, the system should define internal execution classes such as:

- `workspace_read`
- `workspace_write`
- `artifact_write`
- `state_inspection`

The provider adapter may support more capabilities internally, but the policy engine decides whether they are usable under the active profile.

---

## 16. Claude runtime adapter

The Claude runtime must be isolated behind a provider adapter.

### 16.1 Responsibilities

The adapter is responsible for:

- invoking the Claude Agent SDK
- passing resolved prompts and task messages
- handling one-shot and continuation flows
- mapping provider outcomes into normalized internal objects
- preventing provider-specific types from escaping into the MCP layer

### 16.2 Output normalization

The adapter must normalize provider outputs into an internal result object before tool-level formatting occurs.

This is mandatory so that workflows like verification are built on internal contracts, not on raw provider responses.

---

## 17. Workflow executor

The workflow executor mediates between tools and the runtime.

Responsibilities:

- resolve profile
- consult policy engine
- create or update session
- invoke agent adapter
- store transcript events
- materialize artifacts if needed
- produce canonical response envelope

Specialized workflows like verification must use this same executor with fixed policy and output shaping.

---

## 18. v0.1 tool contracts

### 18.1 `agent_run_task`

#### Request

```json
{
  "task": "string",
  "system_profile": "general|verification",
  "working_directory": "string",
  "attachments": ["string"],
  "max_turns": 10,
  "allow_tools": true
}
```

#### Normalized response

```json
{
  "ok": true,
  "session_id": "sess_123",
  "status": "completed",
  "workflow": "run_task",
  "profile": "general",
  "summary": "Task completed",
  "result": {
    "output_text": "string"
  },
  "artifacts": [],
  "warnings": [],
  "errors": []
}
```

#### Notes

- `allow_tools` should be treated as a request, not a guarantee
- the active profile and policy engine decide final behavior
- `system_profile` defaults to `general`

### 18.2 `agent_continue_session`

#### Request

```json
{
  "session_id": "sess_123",
  "message": "string",
  "max_turns": 10
}
```

#### Response

Same canonical envelope, with `workflow` set to `continue_session`.

### 18.3 `agent_get_session`

#### Request

```json
{
  "session_id": "sess_123"
}
```

#### Response

```json
{
  "session_id": "sess_123",
  "workflow": "run_task",
  "profile": "general",
  "status": "completed",
  "created_at": "timestamp",
  "updated_at": "timestamp",
  "last_activity_at": "timestamp",
  "summary_latest": "string",
  "artifact_count": 0,
  "turn_count": 3
}
```

### 18.4 `agent_list_sessions`

#### Request

```json
{
  "limit": 20,
  "status": "completed"
}
```

#### Response

```json
{
  "sessions": [
    {
      "session_id": "sess_123",
      "workflow": "run_task",
      "profile": "general",
      "status": "completed",
      "updated_at": "timestamp",
      "summary_latest": "string"
    }
  ]
}
```

### 18.5 `agent_verify_task`

#### Request

```json
{
  "task": "string",
  "scope": "string",
  "evidence_paths": ["string"],
  "fail_closed": true,
  "system_profile": "verification"
}
```

#### Response

```json
{
  "ok": true,
  "session_id": "sess_456",
  "status": "completed",
  "workflow": "verify_task",
  "profile": "verification",
  "summary": "Verification completed",
  "result": {
    "verdict": "pass|pass_with_restrictions|fail_closed|insufficient_evidence",
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

---

## 19. Persistence design

### 19.1 Database

Use SQLite in v0.1.

Suggested tables:

- `sessions`
- `session_events`
- `artifacts`

Optional:

- `session_locks`
- `schema_migrations`

### 19.2 Filesystem layout

Suggested local storage layout:

```text
.state/
  claude-agent-mcp.db
  artifacts/
    <session_id>/
      <artifact_id>-<logical_name>
```

This should be configurable but default to a safe local path.

---

## 20. Error model

Define a small explicit error taxonomy.

Suggested internal error types:

- `ValidationError`
- `PolicyDeniedError`
- `SessionNotFoundError`
- `SessionConflictError`
- `ProviderRuntimeError`
- `ArtifactPersistenceError`
- `NormalizationError`

These should map into normalized MCP responses with stable error codes.

Example error object:

```json
{
  "code": "session_conflict",
  "message": "Session is currently locked by another execution"
}
```

---

## 21. Crash recovery model

If the process exits during a run:

- the in-progress session must not remain ambiguously `running` forever
- on startup, stale `running` sessions should be reclassified to `interrupted`
- lock cleanup must occur safely
- persisted transcript and artifact metadata must remain readable

This recovery path should be tested explicitly.

---

## 22. Security and safety model

v0.1 is for safe local deployment.

Minimum safety controls:

- working directory allowlist
- read-only profile behavior for verification
- max turn caps
- timeout caps
- bounded artifact size
- bounded attachment inputs
- no dynamic downstream MCP imports in v0.1

---

## 23. Transport scope

v0.1 transport support:

- **stdio only**

SSE and Streamable HTTP are deferred until after the core runtime, persistence, and policy layers are stable.

---

## 24. Downstream MCP scope

Downstream MCP federation is explicitly **out of scope for v0.1**.

Rationale:

- it expands the trust boundary significantly
- it complicates policy enforcement
- it creates tool-discovery and permission-mapping problems too early

The architecture should remain compatible with future downstream tool federation, but no runtime dependency on it should exist in the first release.

---

## 25. Recommended implementation phases for v0.1

### Phase 0 — Charter and contracts

Define:

- v0.1 scope
- non-goals
- canonical response envelope
- session status model
- error taxonomy
- profile schema

### Phase 1 — Repo foundation

Build:

- `pyproject.toml`
- package layout
- config loading
- logging
- typed models
- SQLite bootstrap
- `.env.example`

### Phase 2 — MCP server shell

Implement:

- `server.py`
- stdio transport
- tool registration
- basic input validation
- basic health behavior

Checkpoint: MCP client can enumerate and call stubs.

### Phase 3 — Persistence layer

Implement:

- SQLite schema
- session CRUD
- session event log
- session locking
- artifact metadata skeleton

Checkpoint: sessions persist across restart.

### Phase 4 — Policy and profiles

Implement:

- `profile_registry.py`
- `policy_engine.py`
- built-in `general` and `verification` profiles
- directory and turn validation

Checkpoint: invalid policy requests fail before runtime execution.

### Phase 5 — Claude adapter and workflow executor

Implement:

- `agent_adapter.py`
- `workflow_executor.py`
- run/continue execution flow
- normalization layer

Checkpoint: `agent_run_task` works end-to-end.

### Phase 6 — Inspection tools

Implement:

- `agent_get_session`
- `agent_list_sessions`

Checkpoint: session state is inspectable and stable.

### Phase 7 — Verification workflow

Implement:

- `agent_verify_task`
- verification result normalization
- read-only verification behavior

Checkpoint: bounded verification runs produce structured verdicts.

### Phase 8 — Hardening

Implement:

- crash recovery
- stale lock cleanup
- test coverage
- release documentation

---

## 26. Minimum viable v0.1 release definition

A release qualifies as v0.1 only if all of the following are true:

- MCP server runs over stdio
- sessions persist across restart
- session continuation works through internal canonical state
- profile policies are enforced before runtime execution
- `agent_run_task` works end-to-end
- `agent_get_session` and `agent_list_sessions` work reliably
- `agent_verify_task` produces structured verification results
- crash recovery reclassifies stale running sessions safely
- tests cover persistence, continuation, locking, and verification

---

## 27. Recommended build strategy

Implement as a clean-room repository anchored on:

- official Claude Agent SDK
- official MCP Python SDK
- SQLite + filesystem persistence
- Pydantic-based schema validation

Avoid designing around community wrappers or downstream federation during v0.1.

The architecture should optimize for:

- contract clarity
- deterministic storage semantics
- small trust boundary
- future extensibility without current overreach

---

## 28. Summary of the revision

Compared with the original plan, this revised spec makes these major changes:

- narrows v0.1 tool scope
- defines a canonical response envelope
- formalizes hybrid session semantics
- adds concurrency and crash-recovery rules
- upgrades profiles into policy bundles
- defers downstream MCP federation
- defers patching and rich artifact browsing
- tightens the release definition around persistence and verification

That gives you a much firmer v0.1 target: not just “Claude over MCP,” but a **policy-bounded, session-durable, MCP-native Claude runtime** with a small enough scope to build cleanly.

