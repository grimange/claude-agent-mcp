"""Canonical typed models for claude-agent-mcp.

These are the authoritative contract types. Provider-specific types must not
appear here or in anything that depends on this module.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class SessionStatus(str, Enum):
    created = "created"
    running = "running"
    completed = "completed"
    failed = "failed"
    interrupted = "interrupted"


class WorkflowName(str, Enum):
    run_task = "run_task"
    continue_session = "continue_session"
    verify_task = "verify_task"


class ProfileName(str, Enum):
    general = "general"
    verification = "verification"


class EventType(str, Enum):
    user_input = "user_input"
    system_prompt_resolved = "system_prompt_resolved"
    policy_decision = "policy_decision"
    provider_request_start = "provider_request_start"
    provider_response_summary = "provider_response_summary"
    artifact_emission = "artifact_emission"
    workflow_normalization = "workflow_normalization"
    error_event = "error_event"
    # Federation events (v0.3)
    downstream_tool_catalog_resolved = "downstream_tool_catalog_resolved"
    downstream_tool_invocation = "downstream_tool_invocation"
    downstream_tool_result = "downstream_tool_result"
    # Continuation observability events (v0.7.0)
    session_continuation_context_built = "session_continuation_context_built"
    session_continuation_context_truncated = "session_continuation_context_truncated"
    session_continuation_prompt_rendered = "session_continuation_prompt_rendered"
    # Execution mediation events (v0.8.0)
    mediated_action_requested = "mediated_action_requested"
    mediated_action_approved = "mediated_action_approved"
    mediated_action_rejected = "mediated_action_rejected"
    mediated_action_completed = "mediated_action_completed"
    # Bounded workflow mediation events (v0.9.0)
    mediated_workflow_requested = "mediated_workflow_requested"
    mediated_workflow_step_requested = "mediated_workflow_step_requested"
    mediated_workflow_step_approved = "mediated_workflow_step_approved"
    mediated_workflow_step_rejected = "mediated_workflow_step_rejected"
    mediated_workflow_step_completed = "mediated_workflow_step_completed"
    mediated_workflow_completed = "mediated_workflow_completed"


class WarningRelevance(str, Enum):
    """Classifies a warning for carry-forward relevance in continuation context (v0.7.0)."""

    continuation_relevant = "continuation_relevant"
    """Warning is relevant to continued execution — carry it forward."""

    operator_only = "operator_only"
    """Warning is for operator awareness only — do not include in continuation prompts."""

    request_local = "request_local"
    """Warning is specific to a single request and should not carry forward."""


class ToolClass(str, Enum):
    workspace_read = "workspace_read"
    workspace_write = "workspace_write"
    artifact_write = "artifact_write"
    state_inspection = "state_inspection"


class MediatedActionType(str, Enum):
    """Supported mediated action types in v0.8.0.

    All allowed types are read-style, bounded, and non-destructive.
    Mutating or open-ended action types are not supported in v0.8.0.
    """

    read = "read"
    """Read-style tool invocation (non-mutating data reads)."""

    lookup = "lookup"
    """Bounded lookup or enumeration request."""

    inspect = "inspect"
    """Non-destructive inspection or verification request."""


class MediatedActionStatus(str, Enum):
    """Status of a mediated action execution."""

    approved = "approved"
    """Request passed validation and was approved for execution."""

    rejected = "rejected"
    """Request was rejected by policy, visibility, or allowlist enforcement."""

    completed = "completed"
    """Request was executed successfully."""

    failed = "failed"
    """Request was approved and attempted but execution failed."""


class MediationRejectionReason(str, Enum):
    """Normalized rejection reason codes for mediated actions and workflows (v0.9.0).

    Each value maps to a distinct policy gate failure, making rejection causes
    operator-inspectable without parsing free-text failure messages.
    """

    feature_disabled = "feature_disabled"
    """Mediation feature is disabled in config."""

    invalid_version = "invalid_version"
    """Mediation version does not match the runtime's supported version."""

    unsupported_action_type = "unsupported_action_type"
    """Requested action type is not in the configured allowed set."""

    per_turn_limit_exceeded = "per_turn_limit_exceeded"
    """Per-turn mediated action count limit exceeded."""

    workflow_step_limit_exceeded = "workflow_step_limit_exceeded"
    """Workflow contains more steps than the configured maximum."""

    session_approval_limit_exceeded = "session_approval_limit_exceeded"
    """Session-level mediated approval count limit exceeded."""

    federation_inactive = "federation_inactive"
    """Federation is not active in this runtime."""

    tool_not_visible = "tool_not_visible"
    """Target tool is not visible for the active profile."""

    tool_not_allowed = "tool_not_allowed"
    """Target tool is in the denied list or not in the allowed list."""

    malformed_request = "malformed_request"
    """Mediated request could not be parsed or is missing required fields."""


class MediationContinuationInclusionMode(str, Enum):
    """Controls how mediated step results are included in continuation context (v0.9.0)."""

    approved_only = "approved_only"
    """Include only approved and completed steps in continuation summaries (default)."""

    all_steps = "all_steps"
    """Include both approved/completed and rejected step summaries."""

    none = "none"
    """Do not include any mediated step summaries in continuation context."""


class VerificationVerdict(str, Enum):
    pass_ = "pass"
    pass_with_restrictions = "pass_with_restrictions"
    fail_closed = "fail_closed"
    insufficient_evidence = "insufficient_evidence"


class VerificationReasonCode(str, Enum):
    """Stable reason codes for verification outcomes (v1.1.1).

    Maps into three conceptual groups:

    Evidence reasons:
        sufficient_evidence     — Evidence supports the claim.
        insufficient_evidence   — Evidence is absent, weak, or inconclusive.

    Request-quality reasons:
        scope_too_broad         — Request covers too many artifacts or objectives.
        missing_required_context — No named target, artifact, or evidence anchor.
        non_verifiable_request  — Cannot be answered through passive evidence review.
        ambiguous_request       — Verification goal is unclear or multi-valued.

    Policy/profile reasons:
        out_of_profile_request      — Request exceeds the active verification profile.
        restricted_mode_mismatch    — Request is incompatible with APNTalk verification mode.
    """

    sufficient_evidence = "sufficient_evidence"
    insufficient_evidence = "insufficient_evidence"
    scope_too_broad = "scope_too_broad"
    out_of_profile_request = "out_of_profile_request"
    restricted_mode_mismatch = "restricted_mode_mismatch"
    missing_required_context = "missing_required_context"
    non_verifiable_request = "non_verifiable_request"
    ambiguous_request = "ambiguous_request"


class VerificationDecision(str, Enum):
    """Top-level decision code for a verification result (v1.1.1)."""

    verified = "verified"
    not_verified = "not_verified"
    inconclusive = "inconclusive"


class EvidenceSufficiency(str, Enum):
    """Evidence sufficiency assessment for a verification result (v1.1.1)."""

    sufficient = "sufficient"
    partial = "partial"
    insufficient = "insufficient"


class ScopeAssessment(str, Enum):
    """Request scope assessment for a verification result (v1.1.1)."""

    narrow = "narrow"
    acceptable = "acceptable"
    broad = "broad"
    too_broad = "too_broad"


class ProfileAlignment(str, Enum):
    """Profile alignment assessment for a verification result (v1.1.1)."""

    in_profile = "in_profile"
    out_of_profile = "out_of_profile"
    restricted_mode_mismatch = "restricted_mode_mismatch"


class OperatorProfilePreset(str, Enum):
    """Named operator-facing profile presets that configure multiple fields at once (v1.0.0).

    Presets provide a clear mental model for common deployment configurations.
    Individual env vars always take precedence over preset defaults.

    Mapping:
        safe_default         — Conservative baseline; mediation off; short continuation windows.
        continuity_optimized — Longer continuation windows; mediation off; more context carried forward.
        mediation_enabled    — Mediation on; conservative per-turn limit; results in continuation.
        workflow_limited     — Mediation on; bounded multi-step workflows; session approval cap.
    """

    safe_default = "safe_default"
    continuity_optimized = "continuity_optimized"
    mediation_enabled = "mediation_enabled"
    workflow_limited = "workflow_limited"


class RuntimeMode(str, Enum):
    """Named runtime modes for claude-agent-mcp (v1.1.0).

    standard              — Full tool surface, all profiles, all backends.
    apntalk_verification  — Restricted mode: verification-only, advisory-only,
                            claude_code backend, stdio transport, exact admitted
                            tool pair only.
    """

    standard = "standard"
    apntalk_verification = "apntalk_verification"


class WarningCode(str, Enum):
    """Stable warning category codes for operator-facing warning messages (v1.0.0).

    Used to normalize warning phrasing across the runtime. Each code maps to a
    distinct class of operator-visible degradation or policy condition.
    """

    tool_downgrade = "tool_downgrade"
    """Downstream tools were resolved but not forwarded to the backend."""

    tool_forwarding_incompatible = "tool_forwarding_incompatible"
    """A specific tool was filtered as incompatible with text-based injection."""

    history_truncated = "history_truncated"
    """Continuation history was truncated due to window policy limits."""

    stop_reason_limited = "stop_reason_limited"
    """Stop reason is backend_defaulted due to backend limitations."""

    empty_response = "empty_response"
    """Backend returned an empty response."""

    mediation_rejected = "mediation_rejected"
    """A mediated action or workflow step was rejected by policy."""

    federation_inactive_for_mediation = "federation_inactive_for_mediation"
    """Mediated action requested federation which is not active."""

    continuation_context_truncated = "continuation_context_truncated"
    """Continuation context was truncated by window policy."""


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class RunTaskRequest(BaseModel):
    task: str = Field(..., min_length=1)
    system_profile: ProfileName = ProfileName.general
    working_directory: str | None = None
    attachments: list[str] = Field(default_factory=list)
    max_turns: int = Field(default=10, ge=1, le=100)
    allow_tools: bool = True


class ContinueSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    max_turns: int = Field(default=10, ge=1, le=100)


class GetSessionRequest(BaseModel):
    session_id: str = Field(..., min_length=1)


class ListSessionsRequest(BaseModel):
    limit: int = Field(default=20, ge=1, le=200)
    status: SessionStatus | None = None


class VerifyTaskRequest(BaseModel):
    task: str = Field(..., min_length=1)
    scope: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)
    fail_closed: bool = True
    system_profile: ProfileName = ProfileName.verification


# ---------------------------------------------------------------------------
# Session and event models
# ---------------------------------------------------------------------------


class SessionRecord(BaseModel):
    """Canonical persisted session row."""

    session_id: str
    workflow: WorkflowName
    profile: ProfileName
    provider: str
    provider_session_id: str | None = None
    status: SessionStatus
    working_directory: str | None = None
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    request_count: int = 0
    turn_count: int = 0
    artifact_count: int = 0
    summary_latest: str | None = None
    locked_by: str | None = None
    lock_expires_at: datetime | None = None


class SessionEventRecord(BaseModel):
    """Single append-only session event."""

    event_id: int | None = None
    session_id: str
    event_type: EventType
    turn_index: int
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime


# ---------------------------------------------------------------------------
# Artifact models
# ---------------------------------------------------------------------------


class ArtifactReference(BaseModel):
    """Lightweight artifact pointer included in response envelopes."""

    artifact_id: str
    artifact_type: str
    logical_name: str
    mime_type: str


class ArtifactRecord(BaseModel):
    """Full artifact metadata stored in SQLite."""

    artifact_id: str
    session_id: str
    workflow: str
    profile: str
    artifact_type: str
    logical_name: str
    mime_type: str
    path: str
    size_bytes: int
    sha256: str
    created_at: datetime
    turn_index: int
    producer_tool: str


# ---------------------------------------------------------------------------
# Canonical response envelope
# ---------------------------------------------------------------------------


class ErrorObject(BaseModel):
    code: str
    message: str
    details: dict[str, Any] | None = None


class AgentResponse(BaseModel):
    """Canonical top-level envelope for all mutating/workflow tool results."""

    ok: bool
    session_id: str
    status: SessionStatus
    workflow: WorkflowName
    profile: ProfileName
    summary: str
    result: dict[str, Any] = Field(default_factory=dict)
    artifacts: list[ArtifactReference] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    errors: list[ErrorObject] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Runtime restriction contract (v1.1.0)
# ---------------------------------------------------------------------------


class RuntimeRestrictionContract(BaseModel):
    """Resolved restriction contract for a named restricted runtime mode (v1.1.0).

    Defines the exact requirements and admitted tool surface for a named mode.
    Used to drive server-side tool registration restriction and runtime-status
    proof field population.

    When mode is 'apntalk_verification':
      - required_backend = 'claude_code'
      - required_transport = 'stdio'
      - allowed_tools = ['agent_get_runtime_status', 'agent_verify_task']
      - restriction_contract_id = 'apntalk_verification_v1'
      - restriction_contract_version = 1
      - fail_closed = True
    """

    mode: str
    """The named runtime mode this contract governs."""

    policy_mode: str
    """Policy constraint mode: e.g. 'verification_only'."""

    authority_mode: str
    """Authority posture: e.g. 'advisory_only'."""

    tool_surface_mode: str
    """Tool surface constraint: 'restricted' or 'full'."""

    active_profile: str
    """Active execution profile: e.g. 'apntalk_verification'."""

    required_backend: str
    """Backend that must be active: e.g. 'claude_code'."""

    required_transport: str
    """Transport that must be active: e.g. 'stdio'."""

    allowed_tools: list[str]
    """Exact admitted MCP tool names. Server registers only these."""

    allowed_directories: list[str]
    """Normalized absolute allowed-directory paths. Must be explicit and bounded."""

    restriction_contract_id: str
    """Stable contract identity string: e.g. 'apntalk_verification_v1'."""

    restriction_contract_version: int
    """Integer contract version for machine comparison."""

    fail_closed: bool
    """If True, startup fails when any contract requirement is not satisfied."""


# ---------------------------------------------------------------------------
# Runtime status inspection (v1.0.0 / v1.1.0)
# ---------------------------------------------------------------------------


class RuntimeStatusSnapshot(BaseModel):
    """Resolved runtime status and capability snapshot (v1.0.0 / v1.1.0).

    Produced by RuntimeStatusInspector. Shows the operator what the runtime
    believes is enabled and supported at startup, without requiring inference
    from logs or env var combinations.

    Exposed via agent_get_runtime_status MCP tool and startup log.

    v1.1.0 adds restriction proof fields when a named restricted mode is active.
    These fields are None when mode is 'standard' (backward compatible).
    """

    version: str
    """Package version."""

    operator_profile_preset: str | None
    """Active operator profile preset, if set. None means no preset applied."""

    backend: str
    """Active execution backend: 'api' or 'claude_code'."""

    transport: str
    """Active transport: 'stdio' or 'streamable-http'."""

    model: str
    """Active Claude model identifier."""

    federation_enabled: bool
    """Whether federation is enabled in config."""

    federation_active: bool
    """Whether federation was successfully initialized (tools are discoverable)."""

    capability_flags: dict[str, bool]
    """Effective capability flags for the active backend and config."""

    continuation_settings: dict[str, Any]
    """Resolved continuation window policy settings."""

    mediation_settings: dict[str, Any]
    """Resolved single-action mediation settings."""

    workflow_settings: dict[str, Any]
    """Resolved bounded workflow mediation settings."""

    preserved_limitations: list[str]
    """Known, intentional limitations that are product boundaries in v1.0.0."""

    resolved_at: str
    """ISO 8601 timestamp when this snapshot was produced."""

    # --- Restriction proof fields (v1.1.0) — None when mode is 'standard' ---

    mode: str = "standard"
    """Active runtime mode: 'standard' or 'apntalk_verification'."""

    policy_mode: str | None = None
    """Restriction policy mode. None in standard mode."""

    authority_mode: str | None = None
    """Restriction authority posture. None in standard mode."""

    tool_surface_mode: str | None = None
    """Tool surface constraint: 'restricted' or None."""

    active_profile: str | None = None
    """Active restriction profile. None in standard mode."""

    exposed_tools: list[str] | None = None
    """Exact list of MCP tool names registered on the server. None in standard mode."""

    allowed_directories: list[str] | None = None
    """Normalized allowed-directory paths. None in standard mode."""

    restriction_contract_id: str | None = None
    """Stable restriction contract identity. None in standard mode."""

    restriction_contract_version: int | None = None
    """Restriction contract version. None in standard mode."""

    fail_closed_enabled: bool | None = None
    """Whether fail-closed startup enforcement is active. None in standard mode."""

    restriction_compliance: bool | None = None
    """True if all restriction contract requirements are satisfied. None in standard mode."""

    non_compliance_reasons: list[str] | None = None
    """Reasons the restriction contract is not fully satisfied. None in standard mode."""

    server_version: str | None = None
    """Server version string included in restriction proof. None in standard mode."""


# ---------------------------------------------------------------------------
# Inspection read models
# ---------------------------------------------------------------------------


class SessionSummary(BaseModel):
    """Lightweight session row for list responses."""

    session_id: str
    workflow: WorkflowName
    profile: ProfileName
    status: SessionStatus
    updated_at: datetime
    summary_latest: str | None = None


class SessionDetail(BaseModel):
    """Full session detail for get_session responses."""

    session_id: str
    workflow: WorkflowName
    profile: ProfileName
    status: SessionStatus
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    summary_latest: str | None = None
    artifact_count: int
    turn_count: int
    request_count: int
    working_directory: str | None = None


class ListSessionsResponse(BaseModel):
    sessions: list[SessionSummary]


# ---------------------------------------------------------------------------
# Execution mediation models (v0.8.0)
# ---------------------------------------------------------------------------


class MediatedActionRequest(BaseModel):
    """Structured mediated action request produced by a backend and processed by the runtime.

    The Claude Code backend may embed one or more of these in its output text
    using a strict delimited format. The runtime detects, validates, and executes
    approved requests under policy control.

    This is NOT native tool calling. It is runtime-mediated execution.
    """

    mediation_version: str
    """Mediation protocol version — must match the runtime's supported version."""

    request_id: str
    """Unique identifier for this request, generated by the backend."""

    action_type: MediatedActionType
    """The type of action being requested (read, lookup, or inspect)."""

    target_tool: str
    """Normalized tool name (federation tool identifier) to invoke."""

    arguments: dict[str, Any] = Field(default_factory=dict)
    """Arguments to pass to the target tool."""

    justification: str
    """Backend-provided rationale for why this action is needed."""


class MediatedActionResult(BaseModel):
    """Normalized result from a mediated action execution.

    Produced by the runtime after executing or rejecting a MediatedActionRequest.
    Persisted as a session event and available for continuation context summarization.
    """

    request_id: str
    """The request_id from the originating MediatedActionRequest."""

    status: MediatedActionStatus
    """Final status of the mediated action."""

    tool_name: str
    """The target tool that was (or would have been) invoked."""

    arguments_summary: str
    """Compact, bounded summary of the arguments (for operator inspection)."""

    result_summary: str
    """Compact, bounded summary of the tool result (empty if rejected or failed)."""

    failure_reason: str | None = None
    """Reason for failure or rejection (None if completed successfully)."""

    policy_decision: str
    """Policy decision code explaining the approval, rejection, or failure."""


# ---------------------------------------------------------------------------
# Bounded workflow mediation models (v0.9.0)
# ---------------------------------------------------------------------------


class MediatedWorkflowStep(BaseModel):
    """A single step within a bounded mediated workflow (v0.9.0).

    Each step is individually validated by the runtime before execution.
    """

    step_index: int
    """Zero-based position of this step in the workflow."""

    action_type: MediatedActionType
    """The type of action for this step (read, lookup, or inspect)."""

    target_tool: str
    """Normalized tool name (federation tool identifier) to invoke."""

    arguments: dict[str, Any] = Field(default_factory=dict)
    """Arguments to pass to the target tool."""

    justification: str
    """Backend-provided rationale for this specific step."""


class MediatedWorkflowRequest(BaseModel):
    """A bounded ordered workflow of mediated action steps (v0.9.0).

    The Claude Code backend may embed one of these in its output text using a
    strict delimited format. Each step is validated and executed individually
    by the runtime under policy control.

    This is NOT native tool calling. It is runtime-mediated bounded workflow
    execution — the runtime is the approving authority for every step.
    """

    mediation_version: str
    """Mediation protocol version — must match WORKFLOW_MEDIATION_VERSION."""

    workflow_id: str
    """Unique identifier for this workflow request, generated by the backend."""

    steps: list[MediatedWorkflowStep]
    """Ordered list of steps. Bounded by claude_code_max_mediated_workflow_steps."""

    justification: str = ""
    """Overall workflow justification from the backend."""


class MediatedWorkflowStepResult(BaseModel):
    """Result for a single step in a bounded mediated workflow (v0.9.0)."""

    step_index: int
    """The step_index from the originating MediatedWorkflowStep."""

    action_result: MediatedActionResult
    """The MediatedActionResult for this step."""

    rejection_reason: MediationRejectionReason | None = None
    """Normalized rejection reason enum if this step was rejected. None if approved."""


class MediatedWorkflowResult(BaseModel):
    """Normalized result for a complete bounded mediated workflow (v0.9.0).

    Produced by the runtime after processing all steps in a MediatedWorkflowRequest.
    Persisted as a workflow-level event and available for continuation summarization.
    """

    workflow_id: str
    """The workflow_id from the originating MediatedWorkflowRequest."""

    total_steps: int
    """Total number of steps in the workflow request."""

    approved_steps: int
    """Number of steps that passed validation and were approved for execution."""

    rejected_steps: int
    """Number of steps that were rejected by any policy gate."""

    completed_steps: int
    """Number of approved steps that completed execution successfully."""

    failed_steps: int
    """Number of approved steps that failed during execution."""

    step_results: list[MediatedWorkflowStepResult] = Field(default_factory=list)
    """Per-step results in workflow order."""


class MediationPolicyProfile(BaseModel):
    """Policy profile controlling mediation behavior (v0.9.0).

    Aggregates all mediation policy controls into a single inspectable object.
    Built from config by MediationEngine.build_policy_profile().
    Conservative defaults — operators must explicitly widen behavior.
    """

    name: str = "conservative"
    """Profile identifier used for logging and operator inspection."""

    allowed_action_types: list[str] = Field(default_factory=list)
    """Permitted action types. Empty means all structurally supported types are allowed."""

    allowed_tools: list[str] = Field(default_factory=list)
    """Permitted tool names. Empty means all federation-visible tools are allowed."""

    denied_tools: list[str] = Field(default_factory=list)
    """Explicitly denied tool names. Applied even if allowed_tools is empty."""

    max_steps_per_turn: int = 1
    """Maximum mediated action approvals per turn (single-action + workflow steps combined)."""

    max_approvals_per_session: int = 100
    """Maximum total mediated approvals across all turns in a session."""

    continuation_inclusion_mode: MediationContinuationInclusionMode = (
        MediationContinuationInclusionMode.approved_only
    )
    """Controls which step results appear in continuation context summaries."""

    mixed_action_types_allowed: bool = True
    """Whether a single workflow may contain steps with different action types."""


# ---------------------------------------------------------------------------
# Internal normalized result from the provider adapter
# ---------------------------------------------------------------------------


class NormalizedProviderResult(BaseModel):
    """Normalized output from the Claude adapter, before envelope wrapping."""

    output_text: str
    turn_count: int
    provider_session_id: str | None = None
    stop_reason: str | None = None
    warnings: list[str] = Field(default_factory=list)


class NormalizedVerificationResult(BaseModel):
    """Normalized verification result inside the response envelope result field."""

    verdict: VerificationVerdict
    findings: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)
    output_text: str = ""


class VerificationRequestShape(BaseModel):
    """Heuristic analysis of a verification request's breadth and specificity (v1.1.1).

    Produced by verification_preflight.analyze_request_shape().
    Used to feed preflight lint codes, scope assessment, and operator hints.
    """

    is_narrow: bool
    """True if breadth_score <= 1 (request is well-scoped)."""

    breadth_score: int
    """0–4 breadth indicator (0 = narrow, 4 = very broad)."""

    detected_targets: list[str] = Field(default_factory=list)
    """Specific named artifacts, file paths, or quoted claims found in the request."""

    detected_risks: list[str] = Field(default_factory=list)
    """Detected indicators of a broad or weak request."""


class VerificationPreflightResult(BaseModel):
    """Lightweight preflight lint result for a verification request (v1.1.1).

    Produced by verification_preflight.run_preflight().
    In restricted APNTalk mode, ok=False signals a hard mismatch that blocks execution.
    In standard mode, ok=True unless a hard policy blocker is found.
    """

    ok: bool
    """False only when a hard mismatch blocks execution in restricted mode."""

    lint_codes: list[VerificationReasonCode] = Field(default_factory=list)
    """Stable reason codes detected by the preflight pass."""

    hints: list[str] = Field(default_factory=list)
    """Short, actionable guidance strings derived from lint_codes."""

    normalized_scope_summary: str = ""
    """Short human-readable scope summary derived from the request."""


# ---------------------------------------------------------------------------
# Continuation context models (v0.7.0)
# ---------------------------------------------------------------------------


class ContinuationRelevantWarning(BaseModel):
    """A warning classified for carry-forward relevance in continuation prompts."""

    message: str
    relevance: WarningRelevance
    source: str
    """Source label, e.g. 'tool_downgrade', 'history_truncation'."""


class ForwardingContinuationSummary(BaseModel):
    """Compact summary of prior forwarding decisions for continuation context."""

    forwarding_mode: str
    """One of: 'limited_text_injection', 'disabled', 'full', 'none'."""

    compatible_tool_names: list[str] = Field(default_factory=list)
    dropped_tool_names: list[str] = Field(default_factory=list)
    recent_drop_reasons: list[str] = Field(default_factory=list)


class ContinuationWindowPolicy(BaseModel):
    """Controls how much prior context is included in continuation reconstruction."""

    max_recent_turns: int = 5
    """Maximum number of recent user/assistant turn pairs to include."""

    max_warnings: int = 3
    """Maximum number of warnings to carry forward."""

    max_forwarding_events: int = 3
    """Maximum number of forwarding events to summarize."""

    include_verification_context: bool = True
    """Whether to include verification outcome context."""

    include_tool_downgrade_context: bool = True
    """Whether to include prior tool downgrade warnings in continuation."""


class ContinuationRenderStats(BaseModel):
    """Metadata about what was included in a continuation reconstruction."""

    turns_included: int
    turns_omitted: int
    warnings_included: int
    warnings_omitted: int
    forwarding_events_included: int
    forwarding_events_omitted: int
    reconstruction_version: str


class SessionContinuationContext(BaseModel):
    """Structured continuation package built from persisted session state (v0.7.0).

    Produced by ContinuationContextBuilder and passed to the execution backend.
    Backends that support structured continuation context use this to render
    a deterministic, inspectable continuation prompt.
    """

    session_id: str
    is_continuation: bool
    session_summary: str | None = None
    recent_user_requests: list[str] = Field(default_factory=list)
    recent_agent_outputs: list[str] = Field(default_factory=list)
    relevant_warnings: list[ContinuationRelevantWarning] = Field(default_factory=list)
    forwarding_history: ForwardingContinuationSummary | None = None
    active_constraints: dict[str, Any] = Field(default_factory=dict)
    continuity_notes: list[str] = Field(default_factory=list)
    reconstruction_version: str = "v1.0.0"
    render_stats: ContinuationRenderStats | None = None
    mediated_action_summaries: list[str] = Field(default_factory=list)
    """Compact summaries of mediated action results from prior turns (v0.8.0).

    Included in continuation context when
    claude_code_include_mediated_results_in_continuation is enabled.
    Empty when mediation is disabled or no actions were executed.
    """
    mediated_workflow_summaries: list[str] = Field(default_factory=list)
    """Compact summaries of bounded workflow step results from prior turns (v0.9.0).

    Included when claude_code_include_mediated_results_in_continuation is enabled.
    Inclusion of rejected steps is controlled by
    claude_code_include_rejected_mediation_in_continuation.
    Empty when workflow mediation is disabled or no workflows were executed.
    """
