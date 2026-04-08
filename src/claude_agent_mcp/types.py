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


class VerificationVerdict(str, Enum):
    pass_ = "pass"
    pass_with_restrictions = "pass_with_restrictions"
    fail_closed = "fail_closed"
    insufficient_evidence = "insufficient_evidence"


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
    reconstruction_version: str = "v0.7.0"
    render_stats: ContinuationRenderStats | None = None
