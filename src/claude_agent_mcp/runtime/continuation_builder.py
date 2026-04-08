"""Structured continuation context builder for claude-agent-mcp (v0.7.0).

Produces a deterministic SessionContinuationContext from persisted session events
and a ContinuationWindowPolicy. Used by the workflow executor before invoking
backends that support structured continuation.

Design principles:
- Deterministic: identical session state → identical context
- Bounded: window policy limits what is included
- Derived from events: no reliance on ephemeral state
- Fail-safe: missing or malformed event data is silently ignored
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_mcp.types import (
    ContinuationRelevantWarning,
    ContinuationRenderStats,
    ContinuationWindowPolicy,
    EventType,
    ForwardingContinuationSummary,
    SessionContinuationContext,
    WarningRelevance,
)

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.types import SessionEventRecord, SessionRecord

logger = logging.getLogger(__name__)

_RECONSTRUCTION_VERSION = "v0.9.0"


class ContinuationContextBuilder:
    """Builds structured continuation context from persisted session state.

    This is a stateless utility class. All methods are class methods.
    The workflow executor instantiates it implicitly via build_context().
    """

    @staticmethod
    def build_policy(config: "Config") -> ContinuationWindowPolicy:
        """Construct a ContinuationWindowPolicy from the runtime config.

        Reads continuation-specific config fields added in v0.7.0.
        Falls back to model defaults if fields are absent (e.g., when
        running against an older config snapshot in tests).
        """
        return ContinuationWindowPolicy(
            max_recent_turns=getattr(config, "claude_code_max_continuation_turns", 5),
            max_warnings=getattr(config, "claude_code_max_continuation_warnings", 3),
            max_forwarding_events=getattr(
                config, "claude_code_max_continuation_forwarding_events", 3
            ),
            include_verification_context=getattr(
                config, "claude_code_include_verification_context", True
            ),
            include_tool_downgrade_context=getattr(
                config, "claude_code_include_tool_downgrade_context", True
            ),
        )

    @classmethod
    def build_context(
        cls,
        session: "SessionRecord",
        events: list["SessionEventRecord"],
        policy: ContinuationWindowPolicy,
        config: "Config | None" = None,
    ) -> SessionContinuationContext:
        """Build a structured continuation context from persisted session state.

        Args:
            session: The canonical session record.
            events: All events for the session (append-only log).
            policy: The continuation window policy controlling what is included.
            config: Optional runtime config for v0.8.0 mediation inclusion policy.
                    When None, mediated action summaries are not included.

        Returns:
            A SessionContinuationContext ready for use in prompt rendering.
        """
        # Extract interaction pairs from events
        user_requests = cls._extract_user_requests(events)
        agent_outputs = cls._extract_agent_outputs(events)

        # Apply turn window limit
        total_user = len(user_requests)
        total_agent = len(agent_outputs)
        max_turns = policy.max_recent_turns

        selected_user = user_requests[-max_turns:] if len(user_requests) > max_turns else user_requests
        selected_agent = agent_outputs[-max_turns:] if len(agent_outputs) > max_turns else agent_outputs

        turns_included = min(len(selected_user), len(selected_agent))
        turns_omitted = max(0, min(total_user, total_agent) - turns_included)

        # Classify and filter warnings derived from events
        all_warnings = cls._derive_warnings_from_events(events, policy)
        total_warnings = len(all_warnings)
        selected_warnings = all_warnings[: policy.max_warnings]
        warnings_omitted = max(0, total_warnings - len(selected_warnings))

        # Summarize forwarding history
        forwarding_events = cls._extract_forwarding_events(events)
        total_forwarding = len(forwarding_events)
        selected_forwarding = forwarding_events[-policy.max_forwarding_events :]
        forwarding_omitted = max(0, total_forwarding - len(selected_forwarding))

        forwarding_summary = (
            cls._summarize_forwarding(selected_forwarding) if selected_forwarding else None
        )

        # Build active constraints from session metadata
        active_constraints = cls._extract_active_constraints(session)

        render_stats = ContinuationRenderStats(
            turns_included=turns_included,
            turns_omitted=turns_omitted,
            warnings_included=len(selected_warnings),
            warnings_omitted=warnings_omitted,
            forwarding_events_included=len(selected_forwarding),
            forwarding_events_omitted=forwarding_omitted,
            reconstruction_version=_RECONSTRUCTION_VERSION,
        )

        # v0.8.0/v0.9.0: Include mediated action and workflow summaries when config allows.
        mediated_summaries = cls._extract_mediated_summaries(events, config)
        workflow_summaries = cls._extract_workflow_summaries(events, config)

        ctx = SessionContinuationContext(
            session_id=session.session_id,
            is_continuation=True,
            session_summary=session.summary_latest,
            recent_user_requests=selected_user,
            recent_agent_outputs=selected_agent,
            relevant_warnings=selected_warnings,
            forwarding_history=forwarding_summary,
            active_constraints=active_constraints,
            continuity_notes=cls._build_continuity_notes(session, render_stats),
            reconstruction_version=_RECONSTRUCTION_VERSION,
            render_stats=render_stats,
            mediated_action_summaries=mediated_summaries,
            mediated_workflow_summaries=workflow_summaries,
        )

        if turns_omitted > 0 or warnings_omitted > 0 or forwarding_omitted > 0:
            logger.debug(
                "continuation_builder: context truncated — turns_omitted=%d "
                "warnings_omitted=%d forwarding_omitted=%d",
                turns_omitted,
                warnings_omitted,
                forwarding_omitted,
            )

        return ctx

    # ------------------------------------------------------------------
    # Private extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_user_requests(events: list["SessionEventRecord"]) -> list[str]:
        """Extract user request strings from user_input events."""
        requests: list[str] = []
        for event in events:
            if event.event_type == EventType.user_input:
                text = event.payload.get("task") or event.payload.get("message", "")
                if text and isinstance(text, str):
                    requests.append(text.strip())
        return requests

    @staticmethod
    def _extract_agent_outputs(events: list["SessionEventRecord"]) -> list[str]:
        """Extract agent output summaries from provider_response_summary events."""
        outputs: list[str] = []
        for event in events:
            if event.event_type == EventType.provider_response_summary:
                summary = event.payload.get("summary", "")
                if summary and isinstance(summary, str):
                    outputs.append(summary.strip())
        return outputs

    @staticmethod
    def _extract_forwarding_events(
        events: list["SessionEventRecord"],
    ) -> list[dict[str, Any]]:
        """Extract downstream_tool_catalog_resolved event payloads."""
        return [
            event.payload
            for event in events
            if event.event_type == EventType.downstream_tool_catalog_resolved
        ]

    @staticmethod
    def _derive_warnings_from_events(
        events: list["SessionEventRecord"],
        policy: ContinuationWindowPolicy,
    ) -> list[ContinuationRelevantWarning]:
        """Derive and classify warnings that are carry-forward eligible.

        Warnings are derived from event data rather than stored verbatim,
        since prior backends did not persist warnings in events. This
        approach is deterministic and event-driven.

        Warning relevance rules:
        - Tool downgrade events → continuation_relevant (if policy allows)
        - History truncation → continuation_relevant (inferred from event counts)
        - Verification context → continuation_relevant (if policy allows)
        """
        warnings: list[ContinuationRelevantWarning] = []

        for event in events:
            if event.event_type == EventType.downstream_tool_catalog_resolved:
                dropped_names: list[str] = event.payload.get("dropped_names", [])
                forwarding_mode: str = event.payload.get("forwarding_mode", "")
                forwarded_count: int | bool = event.payload.get("forwarded", 0)

                if dropped_names and policy.include_tool_downgrade_context:
                    dropped_list = ", ".join(dropped_names)
                    warnings.append(
                        ContinuationRelevantWarning(
                            message=(
                                f"Prior turn: {len(dropped_names)} tool(s) were not forwarded "
                                f"({dropped_list}). "
                                f"Forwarding mode: {forwarding_mode or 'limited_text_injection'}."
                            ),
                            relevance=WarningRelevance.continuation_relevant,
                            source="tool_downgrade",
                        )
                    )
                elif forwarded_count is False and not dropped_names:
                    # Tools were visible but not forwarded at all
                    reason = event.payload.get("reason", "backend does not support downstream tools")
                    if policy.include_tool_downgrade_context:
                        warnings.append(
                            ContinuationRelevantWarning(
                                message=f"Prior turn: federation tools were not forwarded — {reason}.",
                                relevance=WarningRelevance.continuation_relevant,
                                source="tool_downgrade",
                            )
                        )

            elif event.event_type == EventType.workflow_normalization:
                if policy.include_verification_context:
                    verdict = event.payload.get("verdict")
                    if verdict:
                        warnings.append(
                            ContinuationRelevantWarning(
                                message=f"Prior verification verdict: {verdict}.",
                                relevance=WarningRelevance.continuation_relevant,
                                source="verification_context",
                            )
                        )

        return warnings

    @staticmethod
    def _summarize_forwarding(
        forwarding_events: list[dict[str, Any]],
    ) -> ForwardingContinuationSummary:
        """Summarize a list of forwarding event payloads into a compact summary."""
        # Use the most recent event as the primary source for mode
        latest = forwarding_events[-1] if forwarding_events else {}

        mode = latest.get("forwarding_mode", "")
        if not mode:
            # Infer mode from payload shape
            if latest.get("forwarded") is False:
                mode = "disabled"
            elif latest.get("forwarded", 0):
                mode = "limited_text_injection"
            else:
                mode = "none"

        # Aggregate compatible and dropped names across all selected events
        compatible_names: list[str] = []
        dropped_names: list[str] = []
        drop_reasons: list[str] = []

        for evt in forwarding_events:
            visible: list[str] = evt.get("visible_tools", [])
            dropped: list[str] = evt.get("dropped_names", [])
            fwd_count = evt.get("forwarded", 0)

            if isinstance(fwd_count, int) and visible:
                forwarded_set = set(visible) - set(dropped)
                for name in forwarded_set:
                    if name not in compatible_names:
                        compatible_names.append(name)

            for name in dropped:
                if name not in dropped_names:
                    dropped_names.append(name)

            reason = evt.get("reason")
            if reason and reason not in drop_reasons:
                drop_reasons.append(reason)

        return ForwardingContinuationSummary(
            forwarding_mode=mode,
            compatible_tool_names=compatible_names,
            dropped_tool_names=dropped_names,
            recent_drop_reasons=drop_reasons,
        )

    @staticmethod
    def _extract_active_constraints(session: "SessionRecord") -> dict[str, Any]:
        """Extract active execution constraints from the session record."""
        constraints: dict[str, Any] = {}
        if session.working_directory:
            constraints["working_directory"] = session.working_directory
        if session.profile:
            constraints["profile"] = session.profile.value
        return constraints

    @staticmethod
    def _build_continuity_notes(
        session: "SessionRecord",
        stats: ContinuationRenderStats,
    ) -> list[str]:
        """Build human-readable continuity notes for the continuation context."""
        notes: list[str] = []

        if stats.turns_omitted > 0:
            notes.append(
                f"{stats.turns_omitted} earlier turn(s) omitted from reconstruction "
                f"(window limit: {stats.turns_included + stats.turns_omitted} total)."
            )
        if stats.warnings_omitted > 0:
            notes.append(
                f"{stats.warnings_omitted} warning(s) omitted (window limit applied)."
            )
        if session.turn_count:
            notes.append(f"Session has completed {session.turn_count} turn(s) so far.")

        return notes

    @staticmethod
    def _extract_mediated_summaries(
        events: list["SessionEventRecord"],
        config: "Config | None",
    ) -> list[str]:
        """Extract compact mediated action result summaries from session events (v0.8.0).

        Only included when ``claude_code_include_mediated_results_in_continuation``
        is enabled in config. When config is None or the flag is disabled, returns [].

        Produces one compact line per completed single-action mediated action:
            "Tool <name> (<action_type>): <result_summary>"

        Rejected step summaries are included when
        ``claude_code_include_rejected_mediation_in_continuation`` is enabled (v0.9.0).

        Args:
            events: All session events.
            config: Runtime config controlling inclusion.

        Returns:
            List of compact summary strings. Empty when disabled or no events found.
        """
        if config is None:
            return []
        if not getattr(config, "claude_code_include_mediated_results_in_continuation", False):
            return []

        include_rejected = getattr(
            config, "claude_code_include_rejected_mediation_in_continuation", False
        )

        summaries: list[str] = []
        for event in events:
            if event.event_type == EventType.mediated_action_completed:
                tool_name = event.payload.get("tool_name", event.payload.get("target_tool", ""))
                status = event.payload.get("status", "")
                result_summary = event.payload.get("result_summary", "")
                action_type = event.payload.get("action_type", "")

                if status == "completed" and tool_name:
                    label = f"Tool {tool_name}"
                    if action_type:
                        label += f" ({action_type})"
                    if result_summary:
                        summary_text = result_summary[:150]
                        if len(result_summary) > 150:
                            summary_text += " [truncated]"
                        summaries.append(f"{label}: {summary_text}")
                    else:
                        summaries.append(f"{label}: (completed, no result summary)")

            elif event.event_type == EventType.mediated_action_rejected and include_rejected:
                tool_name = event.payload.get("target_tool", "")
                policy_decision = event.payload.get("policy_decision", "")
                failure_reason = event.payload.get("failure_reason", "")
                if tool_name:
                    reason_text = failure_reason or policy_decision or "rejected by policy"
                    summaries.append(f"Tool {tool_name} (rejected): {reason_text[:100]}")

        return summaries

    @staticmethod
    def _extract_workflow_summaries(
        events: list["SessionEventRecord"],
        config: "Config | None",
    ) -> list[str]:
        """Extract compact bounded workflow step summaries from session events (v0.9.0).

        Only included when ``claude_code_include_mediated_results_in_continuation``
        is enabled in config. When config is None or the flag is disabled, returns [].

        Produces one summary line per completed or rejected workflow step.
        Rejected step summaries are included when
        ``claude_code_include_rejected_mediation_in_continuation`` is enabled.

        Args:
            events: All session events.
            config: Runtime config controlling inclusion.

        Returns:
            List of compact summary strings. Empty when disabled or no workflow events.
        """
        if config is None:
            return []
        if not getattr(config, "claude_code_include_mediated_results_in_continuation", False):
            return []

        include_rejected = getattr(
            config, "claude_code_include_rejected_mediation_in_continuation", False
        )

        summaries: list[str] = []
        for event in events:
            if event.event_type == EventType.mediated_workflow_step_completed:
                wf_id = event.payload.get("workflow_id", "")
                step_index = event.payload.get("step_index", "?")
                tool_name = event.payload.get("target_tool", "")
                status = event.payload.get("status", "")
                result_summary = event.payload.get("result_summary", "")

                if status == "completed" and tool_name:
                    label = f"Workflow {wf_id} step {step_index}: tool {tool_name}"
                    if result_summary:
                        summary_text = result_summary[:120]
                        if len(result_summary) > 120:
                            summary_text += " [truncated]"
                        summaries.append(f"{label}: {summary_text}")
                    else:
                        summaries.append(f"{label}: (completed, no result summary)")
                elif status == "failed" and tool_name:
                    failure_reason = event.payload.get("failure_reason", "execution failed")
                    summaries.append(
                        f"Workflow {wf_id} step {step_index}: tool {tool_name} "
                        f"(failed): {failure_reason[:80]}"
                    )

            elif event.event_type == EventType.mediated_workflow_step_rejected and include_rejected:
                wf_id = event.payload.get("workflow_id", "")
                step_index = event.payload.get("step_index", "?")
                tool_name = event.payload.get("target_tool", "")
                rejection_reason = event.payload.get("rejection_reason", "")
                failure_reason = event.payload.get("failure_reason", "")
                if tool_name:
                    reason_text = failure_reason or rejection_reason or "rejected by policy"
                    summaries.append(
                        f"Workflow {wf_id} step {step_index}: tool {tool_name} "
                        f"(rejected): {reason_text[:80]}"
                    )

        return summaries
