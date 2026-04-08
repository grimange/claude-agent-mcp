"""Execution mediation engine for claude-agent-mcp (v0.8.0/v0.9.0).

Provides runtime-mediated execution for Claude Code backend mode.

The Claude Code backend may embed structured mediated action requests or bounded
workflow requests in its output text. The runtime (this module) detects, validates,
and executes approved requests under policy control.

This is NOT native tool calling in Claude Code mode.
It is runtime-mediated execution under explicit governance.

Architecture:
    1. Claude Code backend produces normal response text.
    2. MediationEngine.parse_requests() extracts single-action request blocks (v0.8.0).
    3. MediationEngine.parse_workflow() extracts bounded workflow request blocks (v0.9.0).
    4. MediationEngine.validate_request() checks all policy gates for each step.
    5. Approved requests are executed through the federation invoker.
    6. Results are normalized as MediatedActionResult and returned to the caller.
    7. The workflow executor persists results as session events.
    8. Continuation builder summarizes results into continuation context.

Single-action request format (v0.8.0, backward compatible):

    <mediated_action_request>
    {"mediation_version":"v0.8.0","request_id":"...","action_type":"read","target_tool":"...","arguments":{...},"justification":"..."}
    </mediated_action_request>

Bounded workflow request format (v0.9.0):

    <mediated_workflow_request>
    {"mediation_version":"v0.9.0","workflow_id":"...","justification":"...","steps":[{"step_index":0,"action_type":"read","target_tool":"...","arguments":{...},"justification":"..."}]}
    </mediated_workflow_request>

Allowed action types (both v0.8.0 and v0.9.0):
    - read   — read-style, non-mutating data access
    - lookup — bounded enumeration or search
    - inspect — non-destructive inspection or verification

Validation gates (all must pass for approval):
    1. Mediation is enabled in config.
    2. mediation_version matches a supported version.
    3. action_type is in the configured allowed types (or the default set).
    4. Per-turn action count is within the configured limit.
    5. Session-level approval total is within the configured limit (v0.9.0).
    6. target_tool is not in the denied tools list (v0.9.0).
    7. target_tool is in the allowed tools list, if non-empty (v0.9.0).
    8. Federation is active (visibility resolver is available).
    9. target_tool is visible for the active profile.

Rejected requests produce an explicit MediatedActionResult with status=rejected
and a policy_decision code. All results are returned to the caller for event
persistence and operator inspection. There is no silent degradation.
"""

from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from claude_agent_mcp.types import (
    MediatedActionRequest,
    MediatedActionResult,
    MediatedActionStatus,
    MediatedActionType,
    MediationContinuationInclusionMode,
    MediationPolicyProfile,
    MediationRejectionReason,
    MediatedWorkflowRequest,
    MediatedWorkflowStep,
    ProfileName,
)

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.federation.invoker import DownstreamToolInvoker
    from claude_agent_mcp.federation.visibility import ToolVisibilityResolver

logger = logging.getLogger(__name__)

# Mediation version supported for single-action requests (v0.8.0 format).
MEDIATION_VERSION = "v0.8.0"

# Mediation version supported for bounded workflow requests (v0.9.0 format).
WORKFLOW_MEDIATION_VERSION = "v0.9.0"

# Supported mediation versions across both formats.
SUPPORTED_MEDIATION_VERSIONS: frozenset[str] = frozenset({
    MEDIATION_VERSION,
    WORKFLOW_MEDIATION_VERSION,
})

# Delimiters for single-action request blocks (v0.8.0).
_REQUEST_PATTERN = re.compile(
    r"<mediated_action_request>\s*(\{.*?\})\s*</mediated_action_request>",
    re.DOTALL,
)

# Delimiters for bounded workflow request blocks (v0.9.0).
_WORKFLOW_REQUEST_PATTERN = re.compile(
    r"<mediated_workflow_request>\s*(\{.*?\})\s*</mediated_workflow_request>",
    re.DOTALL,
)

# All action types that are structurally supported (regardless of operator config).
SUPPORTED_ACTION_TYPES: frozenset[MediatedActionType] = frozenset({
    MediatedActionType.read,
    MediatedActionType.lookup,
    MediatedActionType.inspect,
})

# Policy decision codes — stable identifiers for operator inspection.
# v0.8.0 codes (preserved for backward compatibility):
POLICY_APPROVED = "approved"
POLICY_REJECTED_DISABLED = "rejected:mediation_disabled"
POLICY_REJECTED_VERSION = "rejected:unsupported_mediation_version"
POLICY_REJECTED_TYPE = "rejected:action_type_not_allowed"
POLICY_REJECTED_LIMIT = "rejected:per_turn_action_limit_exceeded"
POLICY_REJECTED_TOOL_VISIBILITY = "rejected:tool_not_visible"
POLICY_REJECTED_FEDERATION_INACTIVE = "rejected:federation_inactive"
POLICY_EXECUTION_FAILED = "completed_with_execution_failure"

# v0.9.0 additional codes:
POLICY_REJECTED_TOOL_NOT_ALLOWED = "rejected:tool_not_allowed"
POLICY_REJECTED_WORKFLOW_STEP_LIMIT = "rejected:workflow_step_limit_exceeded"
POLICY_REJECTED_SESSION_APPROVAL_LIMIT = "rejected:session_approval_limit_exceeded"
POLICY_REJECTED_MALFORMED = "rejected:malformed_request"

# Mapping from policy code to MediationRejectionReason (v0.9.0).
_POLICY_CODE_TO_REJECTION_REASON: dict[str, MediationRejectionReason] = {
    POLICY_REJECTED_DISABLED: MediationRejectionReason.feature_disabled,
    POLICY_REJECTED_VERSION: MediationRejectionReason.invalid_version,
    POLICY_REJECTED_TYPE: MediationRejectionReason.unsupported_action_type,
    POLICY_REJECTED_LIMIT: MediationRejectionReason.per_turn_limit_exceeded,
    POLICY_REJECTED_TOOL_VISIBILITY: MediationRejectionReason.tool_not_visible,
    POLICY_REJECTED_FEDERATION_INACTIVE: MediationRejectionReason.federation_inactive,
    POLICY_REJECTED_TOOL_NOT_ALLOWED: MediationRejectionReason.tool_not_allowed,
    POLICY_REJECTED_WORKFLOW_STEP_LIMIT: MediationRejectionReason.workflow_step_limit_exceeded,
    POLICY_REJECTED_SESSION_APPROVAL_LIMIT: MediationRejectionReason.session_approval_limit_exceeded,
    POLICY_REJECTED_MALFORMED: MediationRejectionReason.malformed_request,
}

# Maximum character length for result summaries stored in events.
_RESULT_SUMMARY_MAX_CHARS = 500

# Maximum character length for argument value reprs in summaries.
_ARG_VALUE_REPR_MAX_CHARS = 30


class MediationEngine:
    """Runtime-mediated execution engine for Claude Code backend output (v0.8.0/v0.9.0).

    Owned by the WorkflowExecutor. Stateless per-call; all state lives in the
    session store via event persistence (handled by the caller).

    v0.8.0 usage (single-action):
        engine = MediationEngine(config, visibility_resolver)
        requests = engine.parse_requests(output_text)
        for i, req in enumerate(requests):
            ok, policy = engine.validate_request(req, profile_name, actions_so_far)
            if ok:
                result = await engine.execute_action(req, invoker, session_id, turn_idx)
            else:
                result = engine.make_rejection_result(req, policy)

    v0.9.0 usage (bounded workflow):
        workflows = engine.parse_workflow(output_text)
        for wf in workflows:
            ok, policy = engine.validate_workflow_request(wf)
            for step in wf.steps:
                req = engine.step_to_action_request(step, wf.mediation_version)
                ok, policy = engine.validate_request(req, profile_name, actions_so_far,
                                                     session_approved_total=...)
                ...
    """

    def __init__(
        self,
        config: "Config",
        visibility_resolver: "ToolVisibilityResolver | None" = None,
    ) -> None:
        self._config = config
        self._visibility_resolver = visibility_resolver

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """Return True if execution mediation is enabled in config."""
        return bool(getattr(self._config, "claude_code_enable_execution_mediation", False))

    def parse_requests(self, output_text: str) -> list[MediatedActionRequest]:
        """Parse single-action mediated request blocks (v0.8.0 format) from backend output.

        Extracts all ``<mediated_action_request>…</mediated_action_request>``
        blocks and attempts to deserialize each as a MediatedActionRequest.

        Blocks with malformed JSON, missing required fields, or unknown
        action_type values are silently skipped with a WARNING log entry.

        Args:
            output_text: Raw text output from the backend execution.

        Returns:
            Ordered list of successfully parsed MediatedActionRequest objects.
            Empty list if no valid request blocks are found.
        """
        requests: list[MediatedActionRequest] = []

        for match in _REQUEST_PATTERN.finditer(output_text):
            json_str = match.group(1).strip()

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "mediation_engine: malformed JSON in mediated_action_request block: %s", exc
                )
                continue

            if not isinstance(data, dict):
                logger.warning(
                    "mediation_engine: mediated_action_request block is not a JSON object"
                )
                continue

            # Validate required fields before constructing the model.
            required_fields = {
                "mediation_version", "request_id", "action_type", "target_tool", "justification"
            }
            missing = required_fields - set(data.keys())
            if missing:
                logger.warning(
                    "mediation_engine: mediated_action_request missing required fields: %s",
                    sorted(missing),
                )
                continue

            # Validate action_type is a known value.
            try:
                action_type = MediatedActionType(data["action_type"])
            except ValueError:
                logger.warning(
                    "mediation_engine: unknown action_type %r in mediated_action_request",
                    data.get("action_type"),
                )
                continue

            try:
                req = MediatedActionRequest(
                    mediation_version=str(data["mediation_version"]),
                    request_id=str(data["request_id"]),
                    action_type=action_type,
                    target_tool=str(data["target_tool"]),
                    arguments=data.get("arguments") or {},
                    justification=str(data["justification"]),
                )
                requests.append(req)
                logger.debug(
                    "mediation_engine: parsed mediated_action_request id=%r type=%r tool=%r",
                    req.request_id,
                    req.action_type.value,
                    req.target_tool,
                )
            except Exception as exc:
                logger.warning(
                    "mediation_engine: failed to construct MediatedActionRequest: %s", exc
                )

        return requests

    def parse_workflow(self, output_text: str) -> list[MediatedWorkflowRequest]:
        """Parse bounded workflow request blocks (v0.9.0 format) from backend output.

        Extracts all ``<mediated_workflow_request>…</mediated_workflow_request>``
        blocks and attempts to deserialize each as a MediatedWorkflowRequest.

        Blocks with malformed JSON, missing fields, or invalid step definitions
        are skipped with a WARNING log entry.

        Args:
            output_text: Raw text output from the backend execution.

        Returns:
            Ordered list of successfully parsed MediatedWorkflowRequest objects.
            Empty list if no valid workflow blocks are found.
        """
        workflows: list[MediatedWorkflowRequest] = []

        for match in _WORKFLOW_REQUEST_PATTERN.finditer(output_text):
            json_str = match.group(1).strip()

            try:
                data = json.loads(json_str)
            except json.JSONDecodeError as exc:
                logger.warning(
                    "mediation_engine: malformed JSON in mediated_workflow_request block: %s", exc
                )
                continue

            if not isinstance(data, dict):
                logger.warning(
                    "mediation_engine: mediated_workflow_request block is not a JSON object"
                )
                continue

            required_fields = {"mediation_version", "workflow_id", "steps"}
            missing = required_fields - set(data.keys())
            if missing:
                logger.warning(
                    "mediation_engine: mediated_workflow_request missing required fields: %s",
                    sorted(missing),
                )
                continue

            if not isinstance(data.get("steps"), list):
                logger.warning(
                    "mediation_engine: mediated_workflow_request 'steps' must be a list"
                )
                continue

            # Parse each step.
            steps: list[MediatedWorkflowStep] = []
            steps_ok = True
            for i, raw_step in enumerate(data["steps"]):
                if not isinstance(raw_step, dict):
                    logger.warning(
                        "mediation_engine: workflow step %d is not a dict — skipping workflow", i
                    )
                    steps_ok = False
                    break

                step_required = {"action_type", "target_tool", "justification"}
                step_missing = step_required - set(raw_step.keys())
                if step_missing:
                    logger.warning(
                        "mediation_engine: workflow step %d missing required fields: %s — "
                        "skipping workflow",
                        i,
                        sorted(step_missing),
                    )
                    steps_ok = False
                    break

                try:
                    action_type = MediatedActionType(raw_step["action_type"])
                except ValueError:
                    logger.warning(
                        "mediation_engine: unknown action_type %r in workflow step %d — "
                        "skipping workflow",
                        raw_step.get("action_type"),
                        i,
                    )
                    steps_ok = False
                    break

                steps.append(
                    MediatedWorkflowStep(
                        step_index=int(raw_step.get("step_index", i)),
                        action_type=action_type,
                        target_tool=str(raw_step["target_tool"]),
                        arguments=raw_step.get("arguments") or {},
                        justification=str(raw_step["justification"]),
                    )
                )

            if not steps_ok:
                continue

            if not steps:
                logger.warning(
                    "mediation_engine: mediated_workflow_request has no steps — skipping"
                )
                continue

            try:
                wf = MediatedWorkflowRequest(
                    mediation_version=str(data["mediation_version"]),
                    workflow_id=str(data["workflow_id"]),
                    steps=steps,
                    justification=str(data.get("justification", "")),
                )
                workflows.append(wf)
                logger.debug(
                    "mediation_engine: parsed mediated_workflow_request id=%r steps=%d",
                    wf.workflow_id,
                    len(wf.steps),
                )
            except Exception as exc:
                logger.warning(
                    "mediation_engine: failed to construct MediatedWorkflowRequest: %s", exc
                )

        return workflows

    def validate_workflow_request(
        self,
        workflow: MediatedWorkflowRequest,
    ) -> tuple[bool, str]:
        """Validate workflow-level constraints before processing individual steps.

        Checks that mediation is enabled and the workflow does not exceed
        the configured maximum step count. Individual step validation is
        done separately via validate_request().

        Args:
            workflow: The parsed workflow request to validate.

        Returns:
            Tuple of (is_approved, policy_decision_code).
        """
        # Gate: mediation must be enabled.
        if not self.is_enabled():
            return False, POLICY_REJECTED_DISABLED

        # Gate: mediation_version must be the workflow version.
        if workflow.mediation_version != WORKFLOW_MEDIATION_VERSION:
            return False, POLICY_REJECTED_VERSION

        # Gate: step count must not exceed the configured max.
        max_steps = int(
            getattr(self._config, "claude_code_max_mediated_workflow_steps", 1)
        )
        if len(workflow.steps) > max_steps:
            return False, POLICY_REJECTED_WORKFLOW_STEP_LIMIT

        return True, POLICY_APPROVED

    def step_to_action_request(
        self,
        step: MediatedWorkflowStep,
        mediation_version: str,
    ) -> MediatedActionRequest:
        """Convert a MediatedWorkflowStep to a MediatedActionRequest for validation/execution.

        Args:
            step: The workflow step.
            mediation_version: The mediation version from the parent workflow (used for
                version checking in validate_request — converted to v0.8.0 gate compat).

        Returns:
            A MediatedActionRequest wrapping the step's data.
        """
        return MediatedActionRequest(
            mediation_version=MEDIATION_VERSION,  # normalize to single-action version for gates
            request_id=f"wf_step_{step.step_index}",
            action_type=step.action_type,
            target_tool=step.target_tool,
            arguments=step.arguments,
            justification=step.justification,
        )

    def validate_request(
        self,
        request: MediatedActionRequest,
        profile_name: str,
        actions_this_turn: int,
        session_approved_total: int = 0,
    ) -> tuple[bool, str]:
        """Validate a mediated action request against all policy gates.

        Validation is ordered from cheapest to most expensive check.
        Returns on the first failure — callers receive an explicit reason.

        Args:
            request: The parsed mediated action request to validate.
            profile_name: Active profile name for federation visibility checks.
            actions_this_turn: Count of mediated actions already approved/executed
                in the current turn (used to enforce per-turn limits).
            session_approved_total: Total mediated approvals across the session so far,
                including prior turns but not the current in-progress count
                (used to enforce session-level limits, v0.9.0).

        Returns:
            Tuple of (is_approved, policy_decision_code).
            is_approved is True only when all gates pass.
            policy_decision_code is one of the POLICY_* module-level constants.
        """
        # Gate 1: mediation must be enabled in config.
        if not self.is_enabled():
            return False, POLICY_REJECTED_DISABLED

        # Gate 2: request must use a supported mediation version.
        if request.mediation_version not in SUPPORTED_MEDIATION_VERSIONS:
            return False, POLICY_REJECTED_VERSION

        # Gate 3: action_type must be in the configured allowed set.
        allowed_types_cfg: list[str] = getattr(
            self._config, "claude_code_allowed_mediated_action_types", []
        )
        if allowed_types_cfg:
            if request.action_type.value not in allowed_types_cfg:
                return False, POLICY_REJECTED_TYPE
        else:
            # Default: allow all structurally supported types.
            if request.action_type not in SUPPORTED_ACTION_TYPES:
                return False, POLICY_REJECTED_TYPE

        # Gate 4: per-turn action count must be within limit.
        max_actions: int = int(
            getattr(self._config, "claude_code_max_mediated_actions_per_turn", 1)
        )
        if actions_this_turn >= max_actions:
            return False, POLICY_REJECTED_LIMIT

        # Gate 5 (v0.9.0): session-level approval total must be within limit.
        max_session_approvals: int = int(
            getattr(self._config, "claude_code_max_session_mediated_approvals", 100)
        )
        if session_approved_total + actions_this_turn >= max_session_approvals:
            return False, POLICY_REJECTED_SESSION_APPROVAL_LIMIT

        # Gate 6 (v0.9.0): tool must not be in the denied list.
        denied_tools: list[str] = getattr(
            self._config, "claude_code_denied_mediated_tools", []
        )
        if denied_tools and request.target_tool in denied_tools:
            return False, POLICY_REJECTED_TOOL_NOT_ALLOWED

        # Gate 7 (v0.9.0): tool must be in the allowed list (when non-empty).
        allowed_tools: list[str] = getattr(
            self._config, "claude_code_allowed_mediated_tools", []
        )
        if allowed_tools and request.target_tool not in allowed_tools:
            return False, POLICY_REJECTED_TOOL_NOT_ALLOWED

        # Gate 8: federation must be active.
        if self._visibility_resolver is None:
            return False, POLICY_REJECTED_FEDERATION_INACTIVE

        # Gate 9: target_tool must be visible for the active profile.
        try:
            profile_enum = ProfileName(profile_name)
        except ValueError:
            logger.warning(
                "mediation_engine: unknown profile_name %r in validate_request", profile_name
            )
            return False, POLICY_REJECTED_TOOL_VISIBILITY

        visible_tools = self._visibility_resolver.resolve(profile_enum)
        visible_names = {t.normalized_name for t in visible_tools}

        if request.target_tool not in visible_names:
            return False, POLICY_REJECTED_TOOL_VISIBILITY

        return True, POLICY_APPROVED

    async def execute_action(
        self,
        request: MediatedActionRequest,
        invoker: "DownstreamToolInvoker",
        session_id: str,
        turn_index: int,
    ) -> MediatedActionResult:
        """Execute an approved mediated action through the federation invoker.

        The request must have been approved by validate_request() before
        this method is called.

        Args:
            request: The validated and approved mediated action request.
            invoker: The DownstreamToolInvoker for this session's profile.
            session_id: The session identifier (for invoker context).
            turn_index: Current turn index (for invoker context).

        Returns:
            MediatedActionResult with status=completed or status=failed.
            Never raises — execution failures produce a failed result.
        """
        args_summary = _compact_args_summary(request.arguments)

        try:
            invocation_result = await invoker.invoke(
                normalized_name=request.target_tool,
                tool_input=request.arguments,
                session_id=session_id,
                turn_index=turn_index,
            )
            result_text = invocation_result.to_content_string()
            result_summary = (
                result_text[:_RESULT_SUMMARY_MAX_CHARS] + " [truncated]"
                if len(result_text) > _RESULT_SUMMARY_MAX_CHARS
                else result_text
            )

            logger.debug(
                "mediation_engine: executed mediated action id=%r tool=%r status=completed",
                request.request_id,
                request.target_tool,
            )

            return MediatedActionResult(
                request_id=request.request_id,
                status=MediatedActionStatus.completed,
                tool_name=request.target_tool,
                arguments_summary=args_summary,
                result_summary=result_summary,
                failure_reason=None,
                policy_decision=POLICY_APPROVED,
            )

        except Exception as exc:
            logger.warning(
                "mediation_engine: mediated action id=%r tool=%r execution failed: %s",
                request.request_id,
                request.target_tool,
                exc,
            )
            return MediatedActionResult(
                request_id=request.request_id,
                status=MediatedActionStatus.failed,
                tool_name=request.target_tool,
                arguments_summary=args_summary,
                result_summary="",
                failure_reason=str(exc),
                policy_decision=POLICY_EXECUTION_FAILED,
            )

    def make_rejection_result(
        self,
        request: MediatedActionRequest,
        policy_decision: str,
    ) -> MediatedActionResult:
        """Create a MediatedActionResult for a rejected request.

        Args:
            request: The rejected request.
            policy_decision: Policy decision code explaining the rejection.

        Returns:
            MediatedActionResult with status=rejected.
        """
        reason = _rejection_reason_for(policy_decision)
        return MediatedActionResult(
            request_id=request.request_id,
            status=MediatedActionStatus.rejected,
            tool_name=request.target_tool,
            arguments_summary=_compact_args_summary(request.arguments),
            result_summary="",
            failure_reason=reason,
            policy_decision=policy_decision,
        )

    def rejection_reason_enum(self, policy_decision: str) -> MediationRejectionReason:
        """Map a policy decision code to a MediationRejectionReason enum value (v0.9.0).

        Args:
            policy_decision: One of the POLICY_* module-level constants.

        Returns:
            The corresponding MediationRejectionReason, or malformed_request as fallback.
        """
        return _POLICY_CODE_TO_REJECTION_REASON.get(
            policy_decision, MediationRejectionReason.malformed_request
        )

    def build_policy_profile(self) -> MediationPolicyProfile:
        """Build a MediationPolicyProfile from the current config (v0.9.0).

        Aggregates all mediation config fields into a single inspectable object.
        Provides a stable view of the active policy for logging and audit.

        Returns:
            MediationPolicyProfile reflecting the current config.
        """
        include_rejected = getattr(
            self._config, "claude_code_include_rejected_mediation_in_continuation", False
        )
        inclusion_mode = (
            MediationContinuationInclusionMode.all_steps
            if include_rejected
            else MediationContinuationInclusionMode.approved_only
        )

        return MediationPolicyProfile(
            name=getattr(self._config, "claude_code_mediation_policy_profile", "conservative"),
            allowed_action_types=getattr(
                self._config, "claude_code_allowed_mediated_action_types", []
            ),
            allowed_tools=getattr(self._config, "claude_code_allowed_mediated_tools", []),
            denied_tools=getattr(self._config, "claude_code_denied_mediated_tools", []),
            max_steps_per_turn=int(
                getattr(self._config, "claude_code_max_mediated_actions_per_turn", 1)
            ),
            max_approvals_per_session=int(
                getattr(self._config, "claude_code_max_session_mediated_approvals", 100)
            ),
            continuation_inclusion_mode=inclusion_mode,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _compact_args_summary(arguments: dict[str, Any]) -> str:
    """Build a compact, bounded argument summary for operator inspection."""
    if not arguments:
        return "(no arguments)"
    parts = []
    for k, v in arguments.items():
        v_repr = repr(v)
        if len(v_repr) > _ARG_VALUE_REPR_MAX_CHARS:
            v_repr = v_repr[:_ARG_VALUE_REPR_MAX_CHARS] + "…"
        parts.append(f"{k}={v_repr}")
    return ", ".join(parts)


def _rejection_reason_for(policy_decision: str) -> str:
    """Return a human-readable reason string for a rejection policy code."""
    _reasons: dict[str, str] = {
        POLICY_REJECTED_DISABLED: "execution mediation is disabled in config",
        POLICY_REJECTED_VERSION: (
            f"unsupported mediation_version (expected {MEDIATION_VERSION!r} or "
            f"{WORKFLOW_MEDIATION_VERSION!r})"
        ),
        POLICY_REJECTED_TYPE: "action_type is not in the configured allowed set",
        POLICY_REJECTED_LIMIT: "per-turn mediated action count limit exceeded",
        POLICY_REJECTED_TOOL_VISIBILITY: "target_tool is not visible for the active profile",
        POLICY_REJECTED_FEDERATION_INACTIVE: "federation is not active in this runtime",
        POLICY_REJECTED_TOOL_NOT_ALLOWED: (
            "target_tool is in the denied list or not in the allowed list"
        ),
        POLICY_REJECTED_WORKFLOW_STEP_LIMIT: (
            "workflow step count exceeds the configured maximum"
        ),
        POLICY_REJECTED_SESSION_APPROVAL_LIMIT: (
            "session-level mediated approval limit exceeded"
        ),
        POLICY_REJECTED_MALFORMED: "request could not be parsed (malformed)",
    }
    return _reasons.get(policy_decision, f"policy decision: {policy_decision}")
