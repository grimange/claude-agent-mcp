# Changelog

All notable changes to `claude-agent-mcp` are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [1.0.2] — 2026-04-12

### Fixed

- **stdio capability negotiation crash** (`transports/stdio.py`) — `server.get_capabilities()`
  was called with `notification_options=None`, causing `AttributeError: 'NoneType' object has
  no attribute 'tools_changed'` on startup. Fixed by passing a proper `NotificationOptions()`
  instance imported from `mcp.server`.
- **Stale hardcoded transport version** (`transports/stdio.py`) — removed the static
  `VERSION = "0.2.0"` constant. The server now reports its version dynamically via
  `importlib.metadata.version("claude-agent-mcp")`, keeping capability negotiation
  consistent with the installed package.

### Tests

- `test_stdio_version_is_not_hardcoded_stale` — asserts the transport version is not
  the old stale constant and matches the installed package version.
- `test_stdio_notification_options_not_none` — asserts `get_capabilities` receives a
  `NotificationOptions` instance rather than `None`.

---

## [1.0.1] — 2026-04-11

### Documentation refresh

- **`README.md`** — full revision: clarified feature descriptions, updated installation steps,
  expanded backend usage section, improved structure and readability.
- **`docs/codex-setup.md`** — new guide covering Codex-specific setup, configuration, and
  integration patterns for the `claude_code` backend.

---

## [1.0.0] — 2026-04-08

### Stabilization, operator UX, and production-hardening release

v1.0.0 is the first stable production-ready release of the governed Claude Code execution runtime model.
It is a stabilization and clarity release — not a capability expansion.

No breaking changes to MCP tool contracts, response envelopes, or mediation formats.
All v0.6–v0.9 deployments are fully forward-compatible.

### Added

- **`OperatorProfilePreset` enum** (`types.py`) — four named presets for common deployment
  configurations: `safe_default`, `continuity_optimized`, `mediation_enabled`, `workflow_limited`.
  Each preset configures multiple fields at once. Individual env vars always override presets.
- **`CLAUDE_AGENT_MCP_OPERATOR_PROFILE` env var** (`config.py`) — selects an operator profile
  preset. Preset defaults are applied first; individual env vars override on top.
- **`_OPERATOR_PRESET_DEFAULTS` dict** (`config.py`) — maps preset names to default values for
  all configurable continuation and mediation fields.
- **`WarningCode` enum** (`types.py`) — eight stable warning category codes for normalized
  operator-facing warning messages: `tool_downgrade`, `tool_forwarding_incompatible`,
  `history_truncated`, `stop_reason_limited`, `empty_response`, `mediation_rejected`,
  `federation_inactive_for_mediation`, `continuation_context_truncated`.
- **`RuntimeStatusSnapshot` model** (`types.py`) — resolved runtime status and capability
  snapshot. Fields: `version`, `operator_profile_preset`, `backend`, `transport`, `model`,
  `federation_enabled`, `federation_active`, `capability_flags`, `continuation_settings`,
  `mediation_settings`, `workflow_settings`, `preserved_limitations`, `resolved_at`.
- **`RuntimeStatusInspector`** (`runtime/status_inspector.py`) — builds `RuntimeStatusSnapshot`
  from active config and optional `BackendCapabilities`. Wired into server startup.
- **`AuditPresenter`** (`runtime/audit_presenter.py`) — static helpers for structured summaries
  from session event logs. Methods: `continuation_summary()`, `mediation_summary()`,
  `workflow_summary()`, `session_totals()`. Also provides normalized warning format helpers
  (`format_tool_downgrade_warning()`, `format_mediation_rejected_warning()`, etc.).
- **`agent_get_runtime_status` MCP tool** (`server.py`) — additive inspection tool. Returns
  a `RuntimeStatusSnapshot` as JSON. Does not modify state.
- **`docs/operator-guide.md`** — comprehensive operator setup, configuration, and inspection guide.
- **`docs/upgrade-guide-v1.0.md`** — migration notes from v0.6, v0.7, v0.8, and v0.9.
- **`docs/release-validation.md`** — release checklist including smoke tests, compatibility
  statement, and packaging validation.

### Changed

- **`server.py` `VERSION`** — updated from `"0.4.0"` to `"1.0.0"`.
- **`pyproject.toml` `version`** — updated from `"0.3.0"` to `"1.0.0"`.
- **`reconstruction_version`** — `SessionContinuationContext` default and
  `_RECONSTRUCTION_VERSION` constant updated from `"v0.9.0"` to `"v1.0.0"`.
- **`docs/backend-capability-matrix.md`** — updated to v1.0.0; v1.0.0 version notes added.
- **`docs/claude-code-backend.md`** — updated to v1.0.0; operator preset config option documented.
- **Server startup log** — enhanced to include `preset=` in the ready log line.

### Preserved limitations

- No native `tool_use` / `tool_result` in the Claude Code backend
- No streaming transport
- No cross-backend session migration
- No broad autonomous execution chaining
- Mediated execution requires active federation

---

## [0.9.0] — 2026-04-08

### Mediation hardening and bounded workflow expansion track release

This release hardens the v0.8.0 execution mediation layer with stronger policy controls,
richer rejection diagnostics, bounded multi-step workflow support, improved continuation
treatment for mediated results, and full per-step audit observability.

The runtime remains the approving authority for every mediated step.
No open-ended or autonomous execution chains are introduced.
All external MCP tool contracts and response envelopes are unchanged.
The `api` backend is unaffected.

### Added

- **`MediatedWorkflowRequest` model** (`types.py`) — bounded ordered workflow of mediated
  action steps. Fields: `mediation_version` (`"v0.9.0"`), `workflow_id`, `steps`, `justification`.
  Parsed from `<mediated_workflow_request>` blocks in backend output via `parse_workflow()`.
- **`MediatedWorkflowStep` model** (`types.py`) — single step within a bounded workflow.
  Fields: `step_index`, `action_type`, `target_tool`, `arguments`, `justification`.
  Each step is individually validated before execution.
- **`MediatedWorkflowResult` model** (`types.py`) — aggregate result for a completed
  workflow. Fields: `workflow_id`, `total_steps`, `approved_steps`, `rejected_steps`,
  `completed_steps`, `failed_steps`, `step_results`.
- **`MediatedWorkflowStepResult` model** (`types.py`) — per-step result wrapping a
  `MediatedActionResult` with an optional `rejection_reason` enum value.
- **`MediationPolicyProfile` model** (`types.py`) — aggregated, operator-inspectable
  policy object. Built from config by `MediationEngine.build_policy_profile()`. Fields:
  `name`, `allowed_action_types`, `allowed_tools`, `denied_tools`, `max_steps_per_turn`,
  `max_approvals_per_session`, `continuation_inclusion_mode`, `mixed_action_types_allowed`.
- **`MediationRejectionReason` enum** (`types.py`) — 10 normalized rejection reason codes:
  `feature_disabled`, `invalid_version`, `unsupported_action_type`, `per_turn_limit_exceeded`,
  `workflow_step_limit_exceeded`, `session_approval_limit_exceeded`, `federation_inactive`,
  `tool_not_visible`, `tool_not_allowed`, `malformed_request`. Each rejection event now
  includes a stable machine-readable reason code — no free-text parsing needed.
- **`MediationContinuationInclusionMode` enum** (`types.py`) — controls how mediated results
  appear in continuation context. Values: `approved_only` (default), `all_steps`, `none`.
- **Six new `EventType` values** (`types.py`) — full per-step audit trail for bounded
  workflows: `mediated_workflow_requested`, `mediated_workflow_step_requested`,
  `mediated_workflow_step_approved`, `mediated_workflow_step_rejected`,
  `mediated_workflow_step_completed`, `mediated_workflow_completed`. The
  `mediated_workflow_completed` payload includes aggregate step counts for operator dashboards.
- **`mediated_workflow_summaries` field** on `SessionContinuationContext` (`types.py`) —
  compact summaries of bounded workflow step results from prior turns. Default `[]`.
  Populated by `ContinuationContextBuilder` when `include_mediated_results_in_continuation`
  is enabled.
- **`parse_workflow()` method** (`mediation_engine.py`) — parses `<mediated_workflow_request>`
  blocks using a strict deterministic regex pattern. Skips malformed blocks with WARNING logs;
  no silent degradation. Each step is fully validated before the workflow is accepted.
- **`validate_workflow_request()` method** (`mediation_engine.py`) — checks workflow-level
  constraints (mediation enabled, version matches `"v0.9.0"`, step count within
  `claude_code_max_mediated_workflow_steps`). Returns `(bool, policy_decision_code)`.
- **`step_to_action_request()` method** (`mediation_engine.py`) — converts a
  `MediatedWorkflowStep` to a `MediatedActionRequest` for individual step validation via
  the existing `validate_request()` gate chain.
- **`rejection_reason_enum()` method** (`mediation_engine.py`) — maps any policy decision
  code to a `MediationRejectionReason` enum value. Unknown codes fall back to `malformed_request`.
- **`build_policy_profile()` method** (`mediation_engine.py`) — builds a `MediationPolicyProfile`
  from the current config. Logged with mediation decisions for operator audit.
- **`_process_single_action()` helper** (`workflow_executor.py`) — extracted and extended
  version of v0.8.0 mediation processing, now accepts `session_approved_total` for
  session-level limit enforcement.
- **`_process_workflow()` helper** (`workflow_executor.py`) — full bounded workflow
  execution: validates at workflow level, then per-step; emits all six workflow event types;
  builds `MediatedWorkflowResult` with aggregate counts.
- **`_count_session_approvals()` helper** (`workflow_executor.py`) — counts
  `mediated_action_approved` and `mediated_workflow_step_approved` events from the session
  store to enforce session-level approval limits across turns.
- **`_extract_workflow_summaries()` static method** (`continuation_builder.py`) — derives
  compact summary strings from `mediated_workflow_step_completed` and
  `mediated_workflow_step_rejected` events. Rejected step summaries included only when
  `claude_code_include_rejected_mediation_in_continuation=true`.
- **Two new capability flags** (`backends/base.py`) — `supports_bounded_mediated_workflows`
  (default `False`) and `supports_mediation_policy_profiles` (default `False`).
  `claude_code` backend declares both `True`.
- **Six new config fields** (`config.py`) — all conservative defaults:
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_WORKFLOW_STEPS` (int, default `1`)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_TOOLS` (comma-separated, default all visible)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_DENIED_MEDIATED_TOOLS` (comma-separated, default none denied)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_SESSION_MEDIATED_APPROVALS` (int, default `100`)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_REJECTED_MEDIATION_IN_CONTINUATION` (bool, default `false`)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MEDIATION_POLICY_PROFILE` (str, default `"conservative"`)
- **Tests** — `tests/test_v09_mediation.py` (93 new tests) covering: new type models and
  enums, all config fields, capability flags, `parse_workflow()` (valid, malformed, missing
  fields, empty steps, unknown action type), `validate_workflow_request()` (all rejection
  paths), `validate_request()` new gates (session limit, tool allow/deny, combined counts),
  `step_to_action_request()`, `rejection_reason_enum()` (all codes), `build_policy_profile()`,
  `_extract_workflow_summaries()` (enabled/disabled, rejected inclusion), `_extract_mediated_summaries()`
  rejected-step inclusion, `_count_session_approvals()`, full `_process_mediated_actions()` workflow
  path (approval, tool-denied rejection, step-limit rejection, session-limit rejection, aggregate
  stats), v0.8.0 regression, `[Mediated Execution Context]` with workflow summaries.
  Total test count: 457 (up from 364).
- **Docs** — `docs/claude-code-backend.md` and `docs/backend-capability-matrix.md` updated
  to v0.9.0 with bounded workflow format reference, extended validation gate table with
  rejection reason enum, per-step event table, policy profile description, updated capability
  matrix, updated version notes, and explicit preserved-limitations section.

### Changed

- **`validate_request()`** (`mediation_engine.py`) — accepts new optional
  `session_approved_total: int = 0` parameter. Adds two new gates after the per-turn limit
  gate: session-level approval count check (`rejected:session_approval_limit_exceeded`) and
  tool allow/deny list enforcement (`rejected:tool_not_allowed`). Both gates are backward
  compatible — default config values leave existing behavior unchanged.
- **`_process_mediated_actions()`** (`workflow_executor.py`) — now routes to both
  `_process_single_action()` (v0.8.0 format) and `_process_workflow()` (v0.9.0 format).
  Fetches session-level approval count from persisted events at the start of each call.
  Workflow parsing only runs when `supports_bounded_mediated_workflows` is `True`.
- **`_extract_mediated_summaries()`** (`continuation_builder.py`) — extended to include
  `mediated_action_rejected` event summaries when
  `claude_code_include_rejected_mediation_in_continuation=true`. Existing behavior
  (rejected events excluded) is preserved as the default.
- **`ContinuationContextBuilder.build_context()`** (`continuation_builder.py`) — populates
  the new `mediated_workflow_summaries` field on `SessionContinuationContext`.
- **`_build_continuation_prompt()`** (`claude_code_backend.py`) — `[Mediated Execution
  Context]` section now renders both `mediated_action_summaries` and
  `mediated_workflow_summaries` combined. Section still omitted when both lists are empty.
- **`_RECONSTRUCTION_VERSION`** (`continuation_builder.py`) — bumped from `"v0.8.0"` to
  `"v0.9.0"`. `SessionContinuationContext.reconstruction_version` default updated accordingly.
- **`WORKFLOW_MEDIATION_VERSION`** and **`SUPPORTED_MEDIATION_VERSIONS`** added to
  `mediation_engine.py`. `MEDIATION_VERSION = "v0.8.0"` preserved unchanged for
  single-action format compatibility.

### New validation gates (v0.9.0 additions, in gate order)

| Gate | Rejection code | `MediationRejectionReason` |
|---|---|---|
| `claude_code_enable_execution_mediation` is `true` | `rejected:mediation_disabled` | `feature_disabled` |
| `mediation_version` in supported versions | `rejected:unsupported_mediation_version` | `invalid_version` |
| `action_type` is in allowed set | `rejected:action_type_not_allowed` | `unsupported_action_type` |
| Per-turn count < `max_mediated_actions_per_turn` | `rejected:per_turn_action_limit_exceeded` | `per_turn_limit_exceeded` |
| Session total < `max_session_mediated_approvals` **(new)** | `rejected:session_approval_limit_exceeded` | `session_approval_limit_exceeded` |
| `target_tool` not in denied list **(new)** | `rejected:tool_not_allowed` | `tool_not_allowed` |
| `target_tool` in allowed list, if set **(new)** | `rejected:tool_not_allowed` | `tool_not_allowed` |
| Federation is active | `rejected:federation_inactive` | `federation_inactive` |
| `target_tool` visible for active profile | `rejected:tool_not_visible` | `tool_not_visible` |

### Backend capability declarations (v0.9.0)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_execution_mediation` | No | **Yes** (v0.8.0) |
| `supports_mediated_action_results` | No | **Yes** (v0.8.0) |
| `supports_bounded_mediated_workflows` | No | **Yes** (v0.9.0, new) |
| `supports_mediation_policy_profiles` | No | **Yes** (v0.9.0, new) |

### Preserved limitations

- No native `tool_use`/`tool_result` loop in Claude Code mode
- No streaming transport
- No cross-backend session migration
- No broad autonomous or self-directed execution chains
- Mediated execution still requires active federation

---

## [0.8.0] — 2026-04-08

### Claude Code execution mediation track release

This release introduces a governed execution mediation layer for the `claude_code` backend.
Claude Code mode can now request bounded follow-up actions in a strict structured format;
the runtime validates, mediates, and executes approved requests under existing policy controls.

This is **not** native tool calling in Claude Code mode. The backend produces normal text output;
the runtime detects structured request blocks and dispatches them. The backend has no tool
invocation protocol of its own.

No new public MCP tools, profiles, or session semantics were added. All v0.7.0 MCP tool
contracts and response envelopes are unchanged. The `api` backend is unaffected and does
not regress.

### Added

- **`MediationEngine` class** (`runtime/mediation_engine.py`, new file) — stateless engine
  owned by the `WorkflowExecutor`. Implements three responsibilities: parsing structured
  request blocks from backend output (`parse_requests()`), validating requests through six
  policy gates (`validate_request()`), and executing approved actions through the federation
  invoker (`execute_action()`). Never raises — execution failures produce a `failed` result.
- **`MediatedActionRequest` model** (`types.py`) — internal model for requests parsed from
  backend output. Fields: `mediation_version`, `request_id`, `action_type`, `target_tool`,
  `arguments`, `justification`.
- **`MediatedActionResult` model** (`types.py`) — normalized result for every mediated action
  (approved+completed, approved+failed, or rejected). Fields: `request_id`, `status`,
  `tool_name`, `arguments_summary`, `result_summary`, `failure_reason`, `policy_decision`.
- **`MediatedActionType` enum** (`types.py`) — three allowed types: `read`, `lookup`,
  `inspect`. All are read-style and non-destructive. Mutating types are not supported.
- **`MediatedActionStatus` enum** (`types.py`) — four states: `approved`, `rejected`,
  `completed`, `failed`.
- **Four new `EventType` values** (`types.py`) — `mediated_action_requested`,
  `mediated_action_approved`, `mediated_action_rejected`, `mediated_action_completed`.
  Persisted by the workflow executor for every parsed request, enabling full operator audit.
- **`WorkflowExecutor._process_mediated_actions()` helper** — called after each backend
  execution in both `run_task` and `continue_session`. Parses requests, runs validation,
  executes approved actions, persists all four event types, and returns results to the
  caller. Rejected actions emit a warning in the `AgentResponse.warnings` array.
- **`[Mediated Execution Context]` continuation section** (`claude_code_backend.py`) —
  rendered in structured continuation prompts when `mediated_action_summaries` are present.
  Includes a disclaimer that actions were runtime-mediated, not native tool calls. Section
  is omitted entirely when the list is empty (no regression for existing sessions).
- **`mediated_action_summaries` field** on `SessionContinuationContext` (`types.py`) —
  compact summaries of prior mediated action results. Default `[]`; populated by
  `ContinuationContextBuilder` when `claude_code_include_mediated_results_in_continuation`
  is enabled.
- **`ContinuationContextBuilder._extract_mediated_summaries()` static method** — derives
  compact per-action summary strings from `mediated_action_completed` session events.
  Only includes status=`completed` entries; rejected and failed entries are excluded.
  Summary strings are truncated at 150 characters.
- **`supports_execution_mediation` capability flag** (`backends/base.py`) — `claude_code`
  backend declares `True`; `api` backend declares `False`. When `False` in the workflow
  executor, mediation processing is skipped entirely.
- **`supports_mediated_action_results` capability flag** (`backends/base.py`) — `claude_code`
  backend declares `True`; `api` backend declares `False`.
- **Four new config fields** (`config.py`) — all disabled/conservative by default:
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION` (bool, default `false`)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_ACTIONS_PER_TURN` (int, default `1`)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_ACTION_TYPES` (comma-separated, default all supported types)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_MEDIATED_RESULTS_IN_CONTINUATION` (bool, default `false`)
- **Tests** — `tests/test_v08_mediation.py` (77 new tests) covering: model validation,
  request parsing (valid, malformed, missing fields, unknown type), all six validation
  rejection paths, successful execution, execution failure, event persistence, rejection
  warnings in `AgentResponse`, continuation summarization enabled/disabled, `[Mediated
  Execution Context]` section rendering, capability flags, determinism regression,
  and no-side-effects regression for normal output and sessions without mediation events.
  Total test count: 364 (up from 287).
- **Docs** — `docs/claude-code-backend.md` and `docs/backend-capability-matrix.md`
  updated to v0.8.0 with execution mediation section, request format reference,
  validation gate table, observability event table, continuation integration,
  updated capability tables, and explicit "not native tool calling" disclaimers.

### Changed

- `runtime/workflow_executor.py` — `WorkflowExecutor.__init__()` instantiates a
  `MediationEngine(config, visibility_resolver)`. `run_task()` and `continue_session()`
  call `_process_mediated_actions()` after backend execution. Rejected mediated actions
  append a warning to the response `warnings` array (naming the request_id, tool, reason,
  and policy decision code). `continue_session()` passes `config=self._config` to
  `ContinuationContextBuilder.build_context()`.
- `runtime/continuation_builder.py` — `_RECONSTRUCTION_VERSION` bumped to `"v0.8.0"`.
  `build_context()` accepts optional `config` parameter (default `None`; backward
  compatible). `SessionContinuationContext` gains `mediated_action_summaries` field
  (default `[]`; backward compatible).
- `backends/claude_code_backend.py` — `capabilities` updated with two new `True` flags.
  `_build_continuation_prompt()` renders `[Mediated Execution Context]` section when
  `continuation_context.mediated_action_summaries` is non-empty.
- `types.py` — `SessionContinuationContext.reconstruction_version` default bumped
  from `"v0.7.0"` to `"v0.8.0"`.

### Mediation validation gates (in order)

| Gate | Rejection code |
|---|---|
| `claude_code_enable_execution_mediation` is `true` | `rejected:mediation_disabled` |
| `mediation_version` matches `"v0.8.0"` | `rejected:unsupported_mediation_version` |
| `action_type` is in allowed set | `rejected:action_type_not_allowed` |
| Per-turn count < `max_mediated_actions_per_turn` | `rejected:per_turn_action_limit_exceeded` |
| Federation is active (visibility resolver present) | `rejected:federation_inactive` |
| `target_tool` visible for active profile | `rejected:tool_not_visible` |

### Backend capability declarations (v0.8.0)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_downstream_tools` | Yes | No |
| `supports_structured_tool_use` | Yes | No |
| `supports_native_multiturn` | Yes | No |
| `supports_rich_stop_reason` | Yes | No |
| `supports_structured_messages` | Yes | No |
| `supports_workspace_assumptions` | No | Yes |
| `supports_limited_downstream_tools` | No | Yes (opt-in) |
| `supports_structured_continuation_context` | No | Yes (v0.7.0) |
| `supports_continuation_window_policy` | No | Yes (v0.7.0) |
| `supports_execution_mediation` | No | **Yes** (v0.8.0, opt-in) |
| `supports_mediated_action_results` | No | **Yes** (v0.8.0, opt-in) |

### Explicitly preserved limitations (v0.8.0)

- Single-turn per CLI invocation; no native multi-turn tool-use loop.
- Mediation is not native tool calling — the CLI has no tool invocation protocol.
- Mediated execution requires active federation; without it, all requests receive
  `rejected:federation_inactive`.
- Limited tool forwarding remains text-based context only (tools cannot be invoked).
- Full federation tool-use requires the `api` backend.
- `stop_reason` is always `backend_defaulted`.
- No open-ended autonomous execution chains — per-turn action count capped at 1 by default.

---

## [0.7.0] — 2026-04-08

### Claude Code session continuity track release

This release advances the `claude_code` backend from ad hoc history reconstruction
toward a structured, deterministic, and inspectable continuation model. No new public
MCP tools, profiles, or session semantics were added. All v0.6 MCP tool contracts and
response envelopes are unchanged. The `api` backend is unaffected and does not regress.

### Added

- **`SessionContinuationContext` model** (`types.py`) — structured continuation package
  built from persisted session events before each `agent_continue_session` call. Fields:
  `session_id`, `is_continuation`, `session_summary`, `recent_user_requests`,
  `recent_agent_outputs`, `relevant_warnings`, `forwarding_history`,
  `active_constraints`, `continuity_notes`, `reconstruction_version`, `render_stats`.
- **`ContinuationWindowPolicy` model** (`types.py`) — configurable bounds for how much
  prior context is included in continuation reconstruction:
  `max_recent_turns` (default 5), `max_warnings` (default 3),
  `max_forwarding_events` (default 3), `include_verification_context` (default true),
  `include_tool_downgrade_context` (default true).
- **`ContinuationRenderStats` model** (`types.py`) — metadata about what was included
  and omitted: `turns_included`, `turns_omitted`, `warnings_included`,
  `warnings_omitted`, `forwarding_events_included`, `forwarding_events_omitted`,
  `reconstruction_version`.
- **`ContinuationRelevantWarning` model** (`types.py`) — a warning with a `WarningRelevance`
  classification (`continuation_relevant`, `operator_only`, `request_local`) and a
  `source` label. Only `continuation_relevant` warnings appear in continuation prompts.
- **`ForwardingContinuationSummary` model** (`types.py`) — compact summary of prior
  forwarding decisions: `forwarding_mode`, `compatible_tool_names`, `dropped_tool_names`,
  `recent_drop_reasons`. Avoids re-dumping the full tool catalog on every continuation.
- **`WarningRelevance` enum** (`types.py`) — three-level classification for warning
  carry-forward policy.
- **Three new `EventType` values** (`types.py`) — `session_continuation_context_built`,
  `session_continuation_context_truncated`, `session_continuation_prompt_rendered`.
  Recorded for each continuation call to make reconstruction decisions inspectable.
- **`ContinuationContextBuilder` class** (`runtime/continuation_builder.py`, new file) —
  stateless pipeline that derives a `SessionContinuationContext` from persisted session
  events and a `ContinuationWindowPolicy`. Key static methods: `build_policy(config)`,
  `build_context(session, events, policy)`. Deterministic: identical session state
  produces identical output.
- **`_build_continuation_prompt()` method** (`claude_code_backend.py`) — renders
  a structured, section-based continuation prompt from `SessionContinuationContext`.
  Sections (when non-empty, in canonical order): `[System]`, `[Continuation Session]`,
  `[Session Summary]`, `[Recent Interaction State]`, `[Relevant Warnings]`,
  `[Tool Forwarding Context]`, `[Active Constraints]`, `[Current Request]`,
  `[Instructions]`. Empty sections are omitted deterministically.
- **`supports_structured_continuation_context` capability flag** (`backends/base.py`) —
  `claude_code` backend declares `True`; `api` backend declares `False` (uses native
  multi-turn via `conversation_history`).
- **`supports_continuation_window_policy` capability flag** (`backends/base.py`) —
  `claude_code` backend declares `True`; `api` backend declares `False`.
- **Five new config fields** (`config.py`):
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS` (int, default 5)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_WARNINGS` (int, default 3)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_FORWARDING_EVENTS` (int, default 3)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_VERIFICATION_CONTEXT` (bool, default true)
  - `CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_TOOL_DOWNGRADE_CONTEXT` (bool, default true)
- **Tests** — `tests/test_v07_continuation.py` (62 new tests) covering: policy
  construction, context building, truncation behavior, warning classification and
  filtering, forwarding summarization, capability flags, cross-backend contract,
  prompt section rendering, section omission, determinism, and v0.6 regression.
  Total test count: 287 (up from 225).
- **Docs** — `docs/claude-code-backend.md` and `docs/backend-capability-matrix.md`
  updated to v0.7.0 with structured continuation model, warning carry-forward rules,
  forwarding continuity, continuation observability events, and updated capability tables.

### Changed

- `runtime/workflow_executor.py` — `continue_session()` now builds a
  `ContinuationWindowPolicy` from config, calls `ContinuationContextBuilder.build_context()`
  to produce a `SessionContinuationContext`, records `session_continuation_context_built`
  (and `session_continuation_context_truncated` when applicable) events before execution,
  passes `continuation_context` to `backend.execute()`, and records
  `session_continuation_prompt_rendered` after execution. All paths preserve existing
  `conversation_history` for the `api` backend.
- `backends/claude_code_backend.py` — `execute()` accepts new `continuation_context`
  kwarg. When `continuation_context` is provided and `is_continuation=True`, the backend
  uses `_build_continuation_prompt()` instead of `_build_structured_prompt()`. When
  `continuation_context` is `None`, existing v0.6 behavior is preserved unchanged.
  Capabilities updated with the two new v0.7.0 flags.
- `backends/api_backend.py` — `execute()` accepts `continuation_context` kwarg for
  interface symmetry (ignored; API backend uses `conversation_history` natively).
- `backends/base.py` — `BackendCapabilities` extended with
  `supports_structured_continuation_context` and `supports_continuation_window_policy`
  fields (both default `False`). `execute()` signature extended with optional
  `continuation_context` parameter.

### Warning carry-forward policy

Warnings are now classified before being included in continuation prompts:

| Classification | Carried forward by default | Examples |
|---|---|---|
| `continuation_relevant` | Yes | Tool downgrade events, verification outcomes |
| `operator_only` | No | Stop-reason precision notices |
| `request_local` | No | Per-request transient warnings |

All warnings remain in the persisted session event log. Only classified-as-relevant
warnings appear in the `[Relevant Warnings]` section of continuation prompts.

### Backend capability declarations (v0.7.0)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_downstream_tools` | Yes | No |
| `supports_structured_tool_use` | Yes | No |
| `supports_native_multiturn` | Yes | No |
| `supports_rich_stop_reason` | Yes | No |
| `supports_structured_messages` | Yes | No |
| `supports_workspace_assumptions` | No | Yes |
| `supports_limited_downstream_tools` | No | Yes (opt-in) |
| `supports_structured_continuation_context` | No | **Yes** (v0.7.0) |
| `supports_continuation_window_policy` | No | **Yes** (v0.7.0) |

### Explicitly preserved limitations (v0.7.0)

- Single-turn per CLI invocation; no native multi-turn tool-use loop.
- v0.7.0 improves *reconstruction quality* for continuation prompts — it does not
  add native backend-persistent session state.
- Limited tool forwarding remains text-based context only (tools cannot be invoked).
- Full federation tool-use requires the `api` backend.
- `stop_reason` is always `backend_defaulted`.

---

## [0.6.0] — 2026-04-08

### Claude Code capability-expansion track release

This release expands the practical usefulness of the `claude_code` execution
backend after the v0.5 stabilization work. No new public MCP tools, profiles,
or session semantics were added. All v0.5 MCP tool contracts and response
envelopes are unchanged. The `api` backend is unaffected and does not regress.

### Added

- **Limited downstream tool forwarding** (`claude_code` backend, opt-in) —
  when `CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING=true`, compatible
  downstream tools are screened and injected as text descriptions into the
  structured prompt (`[Available Tools]` section). This is **not** a real
  tool-use loop — tools are informational context only; the CLI cannot invoke
  them. Disabled by default.
- **`ToolCompatibilityLevel` enum and `ToolScreenResult` dataclass**
  (`backends/claude_code_backend.py`) — three rejection levels:
  `missing_description`, `schema_unsupported` (uses `$ref`, `allOf`, `anyOf`,
  `oneOf`, or `not`), `complex_schema` (>5 top-level schema properties).
- **`screen_tool()` and `screen_tools()` static methods** on
  `ClaudeCodeExecutionBackend` — deterministic compatibility screening for
  individual and batched tool lists.
- **`_build_tool_descriptions_section()` method** — formats compatible tools
  as a structured `[Available Tools]` text section with name, description,
  parameters, required/optional labels, and an explicit "not invocable" notice.
- **`supports_limited_downstream_tools` capability flag** (`backends/base.py`)
  — added to `BackendCapabilities`. `claude_code` backend declares `True`; `api`
  backend declares `False` (full tool support supersedes the limited flag).
- **Continuation prompt framing** (`claude_code` backend, v0.6) — when
  `is_continuation=True`, the prompt uses `[Continuation Session]` instead of
  `[Session Context]` for the header, and the `[Instructions]` section reads
  "You are continuing this session. Resume from where you left off." Initial
  prompts retain `[Session Context]` framing.
- **Capability-aware forwarding in workflow executor** — `run_task` and
  `continue_session` check `supports_limited_downstream_tools` before the
  existing `supports_downstream_tools` check. When limited forwarding is active,
  compatible tools are passed to the backend; dropped tools are logged as session
  events with `dropped_names` and `forwarding_mode: limited_text_injection`.
- **Config field** — `claude_code_enable_limited_tool_forwarding` (bool, default
  `False`). Env var: `CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING`.
- **Tests** — `tests/test_claude_code_tool_forwarding.py` (28 new tests):
  tool screening, batch screening, tool description formatting, forwarding
  enabled/disabled behavior, per-tool vs. consolidated warnings, continuation
  framing. `TestCrossBackendContractV6` class (9 tests) added to
  `tests/test_backends.py`. Total test count: 225 (up from 196).
- **Docs** — `docs/claude-code-backend.md` and `docs/backend-capability-matrix.md`
  updated to v0.6 with limited forwarding section, continuation framing docs,
  updated capability tables, and updated warning reference.

### Changed

- `backends/claude_code_backend.py` — `_build_structured_prompt()` extended
  with `tools` and `is_continuation` parameters. Continuation framing branching
  added. `execute()` updated with tool forwarding/screening logic.
- `backends/api_backend.py` — `execute()` accepts `is_continuation` kwarg for
  interface symmetry (ignored; API backend handles multi-turn natively).
  `capabilities` now explicitly declares `supports_limited_downstream_tools=False`.
- `backends/base.py` — `BackendCapabilities` extended with
  `supports_limited_downstream_tools` field (default `False`). `execute()`
  signature extended with `is_continuation` parameter.
- `runtime/workflow_executor.py` — capability-aware tool forwarding branch
  added to both `run_task` and `continue_session`.
- `config.py` — `claude_code_enable_limited_tool_forwarding` field added.

### Warnings now surfaced

| Condition | Appears in `warnings` field |
|---|---|
| Tools visible, forwarding disabled (default) | Yes — consolidated, advises `api` backend |
| Tools visible, forwarding enabled, tool dropped | Yes — per-tool, names tool and reason |
| History truncated beyond exchange limit | Yes — states count kept |
| Stop-reason precision limited | Yes — always present |
| Empty CLI response | Yes |

### Backend capability declarations (v0.6)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_downstream_tools` | Yes | No |
| `supports_structured_tool_use` | Yes | No |
| `supports_native_multiturn` | Yes | No |
| `supports_rich_stop_reason` | Yes | No |
| `supports_structured_messages` | Yes | No |
| `supports_workspace_assumptions` | No | Yes |
| `supports_limited_downstream_tools` | No | Yes (opt-in) |

### Known limitations (claude_code backend, v0.6)

- Single-turn per CLI invocation; no native multi-turn tool-use loop.
- Limited tool forwarding is text-based context only — tools cannot be invoked.
- Full federation tool-use requires the `api` backend.
- History is reconstructed as structured labeled text, not Messages API objects.
- `stop_reason` is always `backend_defaulted`.

---

## [0.5.0] — 2026-04-07

### Claude Code stabilization track release

This release hardens the `claude_code` execution backend introduced in v0.4.
No new public MCP tools, profiles, or session semantics were added.
All v0.4 MCP tool contracts and response envelopes are unchanged.
The `api` backend is unaffected and does not regress.

### Added

- **`BackendCapabilities` dataclass** (`backends/base.py`) — frozen dataclass
  declaring six capability flags per backend: `supports_downstream_tools`,
  `supports_structured_tool_use`, `supports_native_multiturn`,
  `supports_rich_stop_reason`, `supports_structured_messages`,
  `supports_workspace_assumptions`. Used internally by the workflow executor
  to emit warnings and suppress unsupported forwarding paths.
- **`capabilities` property on `ExecutionBackend`** — abstract property added
  to the base interface; both backends implement it explicitly.
- **`session_summary` parameter on `execute()`** — optional kwarg added to the
  `ExecutionBackend.execute()` signature. The `claude_code` backend embeds the
  summary in the structured prompt; the `api` backend ignores it.
- **Structured prompt builder** (`claude_code_backend.py`) — replaces the
  previous flat plain-text history serialization. Produces a five-section
  structured prompt: `[System]`, `[Session Context]`, `[Conversation History]`,
  `[Current Request]`, `[Instructions]`. Section boundaries are visually distinct.
- **Deterministic history truncation** — the `claude_code` backend keeps the
  most recent **10 user/assistant exchange pairs** (configurable via
  `HISTORY_MAX_EXCHANGES`). Individual message content is capped at **2000
  characters** (`CONTENT_MAX_CHARS`). Truncated content is marked `[truncated]`.
- **`backend_defaulted` stop reason** — the `claude_code` backend now honestly
  reports `stop_reason: backend_defaulted` instead of the misleading `end_turn`.
- **Capability-aware workflow executor** — `WorkflowExecutor` checks
  `backend.capabilities.supports_downstream_tools` before building the tool
  invoker. If tools are resolved but the backend cannot forward them, a warning
  is emitted to the response envelope and a session event is recorded — no
  silent discard.
- **Session summary passed to backend** — `continue_session` now passes
  `session.summary_latest` as `session_summary` to `backend.execute()`, enabling
  the `claude_code` backend to include it in the `[Session Context]` section.
- **Docs** — `docs/backend-capability-matrix.md` (new) with full capability
  flag definitions and per-flag operator guidance. `docs/claude-code-backend.md`
  and `docs/execution-backends.md` updated to v0.5 with truncation policy,
  warning reference, and expanded troubleshooting.
- **Tests** — 20 new tests in `tests/test_backends.py` across four new test
  classes: `TestBackendCapabilities`, `TestClaudeCodePromptBuilder`,
  `TestClaudeCodeNormalizationV5`, `TestApiBackendV5Compatibility`.
  Total test count: 187 (up from 167).

### Changed

- `backends/claude_code_backend.py` — `_build_prompt()` preserved as a
  backwards-compatible alias for `_build_structured_prompt()`. Internal
  prompt construction fully replaced with the structured builder.
- `backends/api_backend.py` — `execute()` accepts the new `session_summary`
  kwarg (ignored). `capabilities` property added declaring full API support.
- `backends/base.py` — `BackendCapabilities` dataclass added;
  `ExecutionBackend.execute()` signature extended with `session_summary`;
  `capabilities` abstract property added.
- `runtime/workflow_executor.py` — `run_task` and `continue_session` updated
  to check backend capabilities before tool forwarding and to pass
  `session_summary` to `execute()`.
- `tests/test_backends.py` — existing `test_execute_returns_normalized_result`
  updated to assert `stop_reason == "backend_defaulted"` (was `"end_turn"`).

### Warnings now surfaced by `claude_code` backend

| Condition | Appears in `warnings` field |
|---|---|
| Downstream tools resolved but not forwarded | Yes — advises `api` backend |
| History truncated beyond exchange limit | Yes — states count kept |
| Stop-reason precision limited | Yes — always present |
| Empty CLI response | Yes |

### Backend capability declarations (v0.5)

| Capability | `api` | `claude_code` |
|---|---|---|
| `supports_downstream_tools` | Yes | No |
| `supports_structured_tool_use` | Yes | No |
| `supports_native_multiturn` | Yes | No |
| `supports_rich_stop_reason` | Yes | No |
| `supports_structured_messages` | Yes | No |
| `supports_workspace_assumptions` | No | Yes |

### Known limitations (claude_code backend, v0.5)

- Single-turn per CLI invocation; no native multi-turn tool-use loop.
- Downstream federation tools are not forwarded (warning emitted).
- History is reconstructed as structured labeled text, not Messages API objects.
- `stop_reason` is always `backend_defaulted`.

---

## [0.4.0] — 2026-04-07

### Execution backend track release

This release introduces pluggable execution backend support.
Operators can now choose between the Anthropic API and Claude Code as the execution substrate.
No new public workflow tools, profiles, or session semantics were added.
All v0.3 MCP tool contracts and response envelopes are unchanged.

### Added

- **Execution backend abstraction** — new `backends/` package introducing a formal
  `ExecutionBackend` interface. Backends are pluggable execution substrates; the
  workflow executor, session model, policy engine, and MCP surface remain unchanged.
- **`backends/base.py`** — `ExecutionBackend` ABC with `name`, `validate_startup()`,
  `is_available()`, and a unified `execute()` method covering both fresh tasks and
  session continuations.
- **`backends/registry.py`** — `BackendRegistry` for explicit named backend selection.
  Unknown backend names fail clearly; there is no silent fallback between backends.
- **`backends/api_backend.py`** — `ApiExecutionBackend`, the default backend.
  Wraps the existing `ClaudeAdapter` with no behavior change. Authenticated via
  `ANTHROPIC_API_KEY`. Fails at startup if the key is absent.
- **`backends/claude_code_backend.py`** — `ClaudeCodeExecutionBackend`, a CLI-backed
  backend that executes tasks via `claude --print <prompt>` as a subprocess.
  Authenticated via Claude Code's own login state (`claude login`), not API keys.
  Startup validation confirms the CLI is present and executable. Does not forward
  federation tools in v0.4 (warning included in response).
- **Backend selection config** — `CLAUDE_AGENT_MCP_EXECUTION_BACKEND` env var.
  Supported values: `api` (default), `claude_code`. Unknown values cause a startup
  failure with a clear error.
- **Claude Code backend config** — `CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH` (optional
  path to CLI binary) and `CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT` (seconds, default 300).
- **Backend error taxonomy** — five new error classes: `ExecutionBackendConfigError`,
  `ExecutionBackendUnavailableError`, `ExecutionBackendAuthError`,
  `ClaudeCodeUnavailableError`, `ClaudeCodeInvocationError`.
- **Docs** — `docs/execution-backends.md` (backend overview, selection, and error
  reference) and `docs/claude-code-backend.md` (Claude Code backend setup, limitations,
  and troubleshooting).
- **Tests** — `tests/test_backends.py` (35 tests covering registry, config validation,
  API backend startup and routing, Claude Code backend startup and execution, and the
  `build_backend` factory). Total test count: 167 (up from 132).

### Changed

- `workflow_executor.py` — `WorkflowExecutor` now accepts `execution_backend:
  ExecutionBackend` (replaces `agent_adapter: ClaudeAdapter`). All adapter calls
  replaced with `self._backend.execute()`. No behavioral change for the `api` backend.
- `server.py` — `_setup_runtime()` calls `build_backend(config)` to resolve the
  configured backend; logs the selected backend name at startup. Version bumped to `0.4.0`.
- `config.py` — added `execution_backend`, `claude_code_cli_path`, and
  `claude_code_timeout_seconds` fields; `validate()` now rejects unknown backend names.
- `conftest.py` — `mock_adapter` fixture updated to mock `ExecutionBackend.execute`;
  `executor` fixture updated to use `execution_backend=` parameter.

### Backend invariants enforced

- Backend selection is explicit and validated at startup — no magic inference.
- API credentials (`ANTHROPIC_API_KEY`) and Claude Code login state are separate auth
  models; the Claude Code backend does not use `ANTHROPIC_API_KEY`.
- Backends receive the already-filtered visible tool set — they do not own federation
  policy or allowlist logic.
- Internal session identity (`session_id`) remains the canonical identifier regardless
  of backend. Backend metadata is stored in the session `provider` field for
  observability only.
- Canonical `AgentResponse` envelope shape is unchanged across all backends.

### Known limitations (claude_code backend, v0.4)

- Single-turn execution only; no native multi-turn tool-use loop.
- Downstream federation tools are not forwarded to the CLI.
- Conversation history is serialised as plain text in the prompt.
- `stop_reason` is always reported as `end_turn`.

---

## [0.3.0] — 2026-04-07

### Federation track release

This release adds governed downstream MCP federation.
No new public workflow tools, profiles, or session semantics were added.
All v0.2 MCP tool contracts and response envelopes are unchanged.

### Added

- **Downstream MCP federation** — operators can statically configure downstream
  MCP servers that expose a controlled subset of tools to Claude-backed sessions.
  Federation is **disabled by default** (`CLAUDE_AGENT_MCP_FEDERATION_ENABLED=false`).
- **`federation/` package** — new internal module:
  - `registry.py` — `DownstreamRegistry` loads and validates downstream server
    configs from a JSON file; fails early on invalid config.
  - `connections.py` — `DownstreamConnectionManager` connects to enabled downstream
    servers at startup and discovers their tools.
  - `catalog.py` — `ToolCatalog` normalises discovered tools into collision-safe
    names (`{server_name}__{tool_name}`) and applies allowlist filtering.
  - `visibility.py` — `ToolVisibilityResolver` gates tool exposure by active profile.
  - `invoker.py` — `DownstreamToolInvoker` validates tool selection, executes via
    the connection layer, normalises results, and records session audit events.
  - `models.py` — `DownstreamServerConfig`, `DiscoveredTool`, `DownstreamToolCallResult`.
  - `__init__.py` — `FederationManager` startup coordinator.
- **New config fields** — `CLAUDE_AGENT_MCP_FEDERATION_ENABLED` and
  `CLAUDE_AGENT_MCP_FEDERATION_CONFIG` (path to JSON federation config file).
- **Tool-use loop** — `ClaudeAdapter.run_with_tools()` implements the Anthropic
  Messages API tool-use loop for federation-enabled sessions.
- **Federation session events** — three new `EventType` values:
  `downstream_tool_catalog_resolved`, `downstream_tool_invocation`,
  `downstream_tool_result`. Invocation events record input keys only (not values).
- **Federation error taxonomy** — six new error classes: `DownstreamServerConfigError`,
  `DownstreamDiscoveryError`, `DownstreamToolNotAllowedError`,
  `DownstreamToolNotVisibleError`, `DownstreamInvocationError`,
  `DownstreamSchemaValidationError`.
- **Docs** — `docs/federation.md` (operator guide), `docs/downstream-tool-policy.md`
  (four-gate policy model reference).
- **Tests** — `tests/test_federation_registry.py` (22 tests),
  `tests/test_federation_visibility.py` (17 tests),
  `tests/test_federation_invocation.py` (12 tests).
  Total test count: 132 (up from 81).

### Changed

- `server.py` — `_setup_runtime()` initialises `FederationManager` at startup;
  version bumped to `0.3.0`.
- `workflow_executor.py` — accepts optional `visibility_resolver` and
  `federation_server_configs`; `run_task` and `continue_session` use
  `run_with_tools` when federation tools are visible for the active profile.
- `agent_adapter.py` — added `run_with_tools()` method.
- `pyproject.toml` — version bumped to `0.3.0`.

### Federation invariants enforced

- A tool must pass four gates to be callable: server enabled → discovered →
  allowlisted → profile permitted. All gates are enforced in code.
- No wildcard or passthrough allowlist mode exists.
- The `verification` profile has no downstream tool access by default.
- Discovery failures for individual servers are logged and skipped; startup is
  not aborted.
- All downstream invocations flow through the bounded invoker layer.

### Security notes

- Federation expands the trust boundary. It is intended for trusted
  operator-controlled environments only.
- The `streamable-http` transport remains unauthenticated. Federation does not
  change this — do not expose on a public interface without additional controls.
- Downstream servers run as subprocesses and inherit the process environment.

---

## [0.2.0] — 2026-04-07

### Deployment track release

This release focuses entirely on deployment and operability.
No new workflow tools, profiles, or session semantics were added.
All v0.1 MCP tool contracts and response envelopes are unchanged.

### Added

- **Streamable HTTP transport** (`--transport streamable-http`) — exposes the
  same five MCP tools over HTTP using the MCP Streamable HTTP protocol
  (Starlette + uvicorn). Default bind: `127.0.0.1:8000`.
- **Transport abstraction** — `src/claude_agent_mcp/transports/` package with
  `stdio.py` and `streamable_http.py` bootstrap modules. Both share the same
  `build_server()` function; no runtime logic is duplicated per transport.
- **CLI flags** — `--transport`, `--host`, `--port`, `--version` added to the
  `claude-agent-mcp` entry point.
- **Config validation** — `Config.validate()` fails early and clearly on
  invalid transport names, out-of-range ports, or unrecognised log levels.
- **`CLAUDE_AGENT_MCP_` env prefix** — all configuration variables now have a
  canonical `CLAUDE_AGENT_MCP_` prefixed name. Legacy `CLAUDE_AGENT_` names
  still work as fallbacks.
- **New env vars**: `CLAUDE_AGENT_MCP_TRANSPORT`, `CLAUDE_AGENT_MCP_HOST`,
  `CLAUDE_AGENT_MCP_PORT`, `CLAUDE_AGENT_MCP_DB_PATH`,
  `CLAUDE_AGENT_MCP_ARTIFACT_DIR`.
- **Packaging** — `starlette>=0.40.0` and `uvicorn>=0.30.0` added as core
  dependencies. `httpx` added to dev extras.
- **Docs** — `docs/deployment.md`, `docs/transports.md`,
  `docs/troubleshooting.md`.
- **Tests** — `tests/test_transports.py` (13 tests), `tests/test_cli.py`
  (8 tests). Total test count: 81 (up from 60).

### Changed

- `server.py` — refactored `run()` into `run_stdio()` / `run_streamable_http()`
  with shared `_setup_runtime()`. Added `_build_parser()` for CLI arg parsing.
- `config.py` — added `transport`, `host`, `port` fields; added `validate()`;
  added `_env()` helper for primary/fallback env lookup.
- `__init__.py` — version bumped to `0.2.0`.
- `pyproject.toml` — version bumped to `0.2.0`; new dependencies added.
- `.env.example` — updated to document all `CLAUDE_AGENT_MCP_` variables.

### Security notes

- The `streamable-http` transport is **not authenticated**. Default bind is
  `127.0.0.1` (loopback-safe). Do not expose on a non-loopback interface
  without additional access control.
- This remains a single-operator, single-node, local-first system.

---

## [0.1.0] — 2026-04-07

### Initial release

- Sessioned Claude-backed agent runtime exposed over MCP (stdio transport)
- Durable SQLite-backed sessions with single-writer locking and crash recovery
- Five public MCP tools: `agent_run_task`, `agent_continue_session`,
  `agent_get_session`, `agent_list_sessions`, `agent_verify_task`
- Two built-in profiles: `general`, `verification`
- Canonical response envelope enforced on all mutating tools
- Local filesystem artifact storage
- Policy engine with working directory validation, turn caps, read-only rules
- Verification workflow with fail-closed behaviour and evidence-path validation
