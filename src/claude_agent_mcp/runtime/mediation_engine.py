"""Execution mediation engine for claude-agent-mcp (v0.8.0).

Provides runtime-mediated execution for Claude Code backend mode.

The Claude Code backend may embed structured mediated action requests in its
output text. The runtime (this module) detects, validates, and executes approved
requests under policy control.

This is NOT native tool calling in Claude Code mode.
It is runtime-mediated execution under explicit governance.

Architecture:
    1. Claude Code backend produces normal response text.
    2. MediationEngine.parse_requests() extracts structured request blocks.
    3. MediationEngine.validate_request() checks policy, visibility, and allowlist.
    4. Approved requests are executed through the federation invoker.
    5. Results are normalized as MediatedActionResult and returned to the caller.
    6. The workflow executor persists results as session events.
    7. Continuation builder summarizes results into continuation context.

Request format (strict, deterministic):
    Backend output may contain zero or more request blocks, each using this
    exact delimiter/JSON format:

        <mediated_action_request>
        {"mediation_version":"v0.8.0","request_id":"...","action_type":"read","target_tool":"...","arguments":{...},"justification":"..."}
        </mediated_action_request>

    The JSON must be valid. All required fields must be present. Unknown
    action_type values cause the request to be skipped with a WARNING log.
    No freeform or ambiguous parsing is attempted.

Allowed action types in v0.8.0:
    - read   — read-style, non-mutating data access
    - lookup — bounded enumeration or search
    - inspect — non-destructive inspection or verification

Validation rules (all must pass for approval):
    1. Mediation is enabled in config.
    2. mediation_version matches the runtime's supported version.
    3. action_type is in the configured allowed types (or the default set).
    4. Per-turn action count is within the configured limit.
    5. target_tool is visible for the active profile.
    6. Federation is active (visibility resolver is available).

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
    ProfileName,
)

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.federation.invoker import DownstreamToolInvoker
    from claude_agent_mcp.federation.visibility import ToolVisibilityResolver

logger = logging.getLogger(__name__)

# The only mediation version supported by this runtime.
MEDIATION_VERSION = "v0.8.0"

# Delimiters used to detect request blocks in backend output.
_REQUEST_PATTERN = re.compile(
    r"<mediated_action_request>\s*(\{.*?\})\s*</mediated_action_request>",
    re.DOTALL,
)

# All action types that are structurally supported (regardless of operator config).
SUPPORTED_ACTION_TYPES: frozenset[MediatedActionType] = frozenset({
    MediatedActionType.read,
    MediatedActionType.lookup,
    MediatedActionType.inspect,
})

# Policy decision codes — stable identifiers for operator inspection.
POLICY_APPROVED = "approved"
POLICY_REJECTED_DISABLED = "rejected:mediation_disabled"
POLICY_REJECTED_VERSION = "rejected:unsupported_mediation_version"
POLICY_REJECTED_TYPE = "rejected:action_type_not_allowed"
POLICY_REJECTED_LIMIT = "rejected:per_turn_action_limit_exceeded"
POLICY_REJECTED_TOOL_VISIBILITY = "rejected:tool_not_visible"
POLICY_REJECTED_FEDERATION_INACTIVE = "rejected:federation_inactive"
POLICY_EXECUTION_FAILED = "completed_with_execution_failure"

# Maximum character length for result summaries stored in events.
_RESULT_SUMMARY_MAX_CHARS = 500

# Maximum character length for argument value reprs in summaries.
_ARG_VALUE_REPR_MAX_CHARS = 30


class MediationEngine:
    """Runtime-mediated execution engine for Claude Code backend output.

    Owned by the WorkflowExecutor. Stateless per-call; all state lives in the
    session store via event persistence (handled by the caller).

    Usage:
        engine = MediationEngine(config, visibility_resolver)
        requests = engine.parse_requests(output_text)
        for i, req in enumerate(requests):
            ok, policy = engine.validate_request(req, profile_name, actions_so_far)
            if ok:
                result = await engine.execute_action(req, invoker, session_id, turn_idx)
            else:
                result = engine.make_rejection_result(req, policy, reason_from_policy)
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
        """Parse mediated action request blocks from backend output text.

        Extracts all ``<mediated_action_request>…</mediated_action_request>``
        blocks and attempts to deserialize each as a MediatedActionRequest.

        Blocks with malformed JSON, missing required fields, or unknown
        action_type values are silently skipped with a WARNING log entry.
        This prevents malformed requests from interrupting normal response
        processing.

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

    def validate_request(
        self,
        request: MediatedActionRequest,
        profile_name: str,
        actions_this_turn: int,
    ) -> tuple[bool, str]:
        """Validate a mediated action request against all policy gates.

        Validation is ordered from cheapest to most expensive check.
        Returns on the first failure — callers receive an explicit reason.

        Args:
            request: The parsed mediated action request to validate.
            profile_name: Active profile name for federation visibility checks.
            actions_this_turn: Count of mediated actions already approved/executed
                in the current turn (used to enforce per-turn limits).

        Returns:
            Tuple of (is_approved, policy_decision_code).
            is_approved is True only when all gates pass.
            policy_decision_code is one of the POLICY_* module-level constants.
        """
        # Gate 1: mediation must be enabled in config.
        if not self.is_enabled():
            return False, POLICY_REJECTED_DISABLED

        # Gate 2: request must use the supported mediation version.
        if request.mediation_version != MEDIATION_VERSION:
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

        # Gate 5: federation must be active.
        if self._visibility_resolver is None:
            return False, POLICY_REJECTED_FEDERATION_INACTIVE

        # Gate 6: target_tool must be visible for the active profile.
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
            f"unsupported mediation_version (expected {MEDIATION_VERSION!r})"
        ),
        POLICY_REJECTED_TYPE: "action_type is not in the configured allowed set",
        POLICY_REJECTED_LIMIT: "per-turn mediated action count limit exceeded",
        POLICY_REJECTED_TOOL_VISIBILITY: "target_tool is not visible for the active profile",
        POLICY_REJECTED_FEDERATION_INACTIVE: "federation is not active in this runtime",
    }
    return _reasons.get(policy_decision, f"policy decision: {policy_decision}")
