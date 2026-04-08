"""Audit and observability presentation helpers for claude-agent-mcp (v1.0.0).

Produces stable, summarized views of session event logs for operator consumption.
All methods are read-only projections over append-only event data — no mutations.

Design intent:
  Raw session events are the source of truth and are preserved intact.
  This module adds clearer, summarized presentation on top of raw event data
  without replacing or filtering the underlying log.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from claude_agent_mcp.types import EventType

if TYPE_CHECKING:
    from claude_agent_mcp.types import SessionEventRecord, SessionRecord


class AuditPresenter:
    """Produces human-readable summaries from session event logs.

    All methods are static and stateless. Input is always a list of
    SessionEventRecord (append-only log). Output is a plain dict for
    easy JSON serialization.

    Usage:
        summary = AuditPresenter.mediation_summary(events)
        totals = AuditPresenter.session_totals(session, events)
    """

    # ------------------------------------------------------------------
    # Continuation summary
    # ------------------------------------------------------------------

    @staticmethod
    def continuation_summary(events: list["SessionEventRecord"]) -> dict[str, Any]:
        """Summarize continuation reconstruction events from the event log.

        Returns:
            {
              "total_continuation_calls": int,
              "truncations_occurred": int,
              "last_reconstruction_version": str | None,
              "last_policy": { max_recent_turns, max_warnings, ... } | None,
              "last_render_stats": { turns_included, turns_omitted, ... } | None,
            }
        """
        built_events = [
            e for e in events
            if e.event_type == EventType.session_continuation_context_built
        ]
        truncated_events = [
            e for e in events
            if e.event_type == EventType.session_continuation_context_truncated
        ]
        rendered_events = [
            e for e in events
            if e.event_type == EventType.session_continuation_prompt_rendered
        ]

        last_built = built_events[-1] if built_events else None
        last_rendered = rendered_events[-1] if rendered_events else None

        return {
            "total_continuation_calls": len(built_events),
            "truncations_occurred": len(truncated_events),
            "last_reconstruction_version": (
                last_rendered.payload.get("reconstruction_version")
                if last_rendered
                else (
                    last_built.payload.get("reconstruction_version")
                    if last_built
                    else None
                )
            ),
            "last_policy": last_built.payload.get("policy") if last_built else None,
            "last_render_stats": last_built.payload.get("render_stats") if last_built else None,
        }

    # ------------------------------------------------------------------
    # Mediation summary
    # ------------------------------------------------------------------

    @staticmethod
    def mediation_summary(events: list["SessionEventRecord"]) -> dict[str, Any]:
        """Summarize mediation request/approval/rejection/completion events.

        Covers both single-action (v0.8.0) and bounded workflow (v0.9.0) events.

        Returns:
            {
              "single_action": {
                "requested": int, "approved": int, "rejected": int,
                "completed": int, "failed": int,
                "rejection_reasons": {reason_code: count, ...},
              },
              "workflow": {
                "requested": int, "completed": int,
                "total_steps_approved": int, "total_steps_rejected": int,
                "total_steps_completed": int,
              },
            }
        """
        single: dict[str, Any] = {
            "requested": 0,
            "approved": 0,
            "rejected": 0,
            "completed": 0,
            "failed": 0,
            "rejection_reasons": {},
        }
        workflow: dict[str, Any] = {
            "requested": 0,
            "completed": 0,
            "total_steps_approved": 0,
            "total_steps_rejected": 0,
            "total_steps_completed": 0,
        }

        for event in events:
            etype = event.event_type
            payload = event.payload

            if etype == EventType.mediated_action_requested:
                single["requested"] += 1
            elif etype == EventType.mediated_action_approved:
                single["approved"] += 1
            elif etype == EventType.mediated_action_rejected:
                single["rejected"] += 1
                reason = payload.get("rejection_reason") or payload.get("policy_decision", "unknown")
                single["rejection_reasons"][reason] = (
                    single["rejection_reasons"].get(reason, 0) + 1
                )
            elif etype == EventType.mediated_action_completed:
                status = payload.get("status", "")
                if status == "completed":
                    single["completed"] += 1
                elif status == "failed":
                    single["failed"] += 1

            elif etype == EventType.mediated_workflow_requested:
                workflow["requested"] += 1
            elif etype == EventType.mediated_workflow_completed:
                workflow["completed"] += 1
                workflow["total_steps_approved"] += payload.get("approved_steps", 0)
                workflow["total_steps_rejected"] += payload.get("rejected_steps", 0)
                workflow["total_steps_completed"] += payload.get("completed_steps", 0)

        return {"single_action": single, "workflow": workflow}

    # ------------------------------------------------------------------
    # Workflow summary
    # ------------------------------------------------------------------

    @staticmethod
    def workflow_summary(events: list["SessionEventRecord"]) -> dict[str, Any]:
        """Summarize high-level workflow events (provider calls, artifacts, etc.).

        Returns:
            {
              "provider_calls": int,
              "artifact_emissions": int,
              "error_events": int,
              "policy_decisions": list[str],
            }
        """
        provider_calls = sum(
            1 for e in events if e.event_type == EventType.provider_request_start
        )
        artifact_count = sum(
            1 for e in events if e.event_type == EventType.artifact_emission
        )
        error_count = sum(
            1 for e in events if e.event_type == EventType.error_event
        )
        policy_decisions = [
            e.payload.get("decision", "")
            for e in events
            if e.event_type == EventType.policy_decision
            and e.payload.get("decision")
        ]

        return {
            "provider_calls": provider_calls,
            "artifact_emissions": artifact_count,
            "error_events": error_count,
            "policy_decisions": policy_decisions,
        }

    # ------------------------------------------------------------------
    # Session-level totals
    # ------------------------------------------------------------------

    @staticmethod
    def session_totals(
        session: "SessionRecord",
        events: list["SessionEventRecord"],
    ) -> dict[str, Any]:
        """Produce session-level totals and truncation indicators.

        Returns:
            {
              "session_id": str,
              "status": str,
              "turn_count": int,
              "request_count": int,
              "artifact_count": int,
              "continuation_calls": int,
              "continuation_truncations": int,
              "mediated_actions_approved": int,
              "mediated_actions_rejected": int,
              "mediated_workflow_steps_approved": int,
              "mediated_workflow_steps_rejected": int,
              "error_events": int,
            }
        """
        med = AuditPresenter.mediation_summary(events)
        cont = AuditPresenter.continuation_summary(events)
        wf = AuditPresenter.workflow_summary(events)

        return {
            "session_id": session.session_id,
            "status": session.status.value if hasattr(session.status, "value") else str(session.status),
            "turn_count": session.turn_count,
            "request_count": session.request_count,
            "artifact_count": session.artifact_count,
            "continuation_calls": cont["total_continuation_calls"],
            "continuation_truncations": cont["truncations_occurred"],
            "mediated_actions_approved": med["single_action"]["approved"],
            "mediated_actions_rejected": med["single_action"]["rejected"],
            "mediated_workflow_steps_approved": med["workflow"]["total_steps_approved"],
            "mediated_workflow_steps_rejected": med["workflow"]["total_steps_rejected"],
            "error_events": wf["error_events"],
        }

    # ------------------------------------------------------------------
    # Normalized warning formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_tool_downgrade_warning(
        tool_count: int,
        backend_name: str,
    ) -> str:
        """Normalized tool downgrade warning message (v1.0.0).

        Replaces ad-hoc warning strings with a stable, categorized format.
        """
        return (
            f"[tool_downgrade] {tool_count} downstream tool(s) resolved but not forwarded "
            f"to the '{backend_name}' backend. The backend does not support tool invocation. "
            f"Use the 'api' backend to enable full tool use."
        )

    @staticmethod
    def format_tool_forwarding_incompatible_warning(
        tool_name: str,
        reason: str,
    ) -> str:
        """Normalized per-tool forwarding incompatibility warning (v1.0.0)."""
        return (
            f"[tool_forwarding_incompatible] Tool '{tool_name}' was not injected: {reason}."
        )

    @staticmethod
    def format_history_truncated_warning(
        kept: int,
        omitted: int,
        max_exchanges: int,
    ) -> str:
        """Normalized history truncation warning (v1.0.0)."""
        return (
            f"[history_truncated] Continuation history truncated to {kept} exchange(s) "
            f"({omitted} omitted). Window limit: {max_exchanges}."
        )

    @staticmethod
    def format_stop_reason_limited_warning() -> str:
        """Normalized stop-reason precision warning (v1.0.0)."""
        return (
            "[stop_reason_limited] Stop reason is 'backend_defaulted' — the Claude Code "
            "backend does not report precise stop-reason semantics. Do not write downstream "
            "logic that depends on specific stop_reason values with this backend."
        )

    @staticmethod
    def format_empty_response_warning() -> str:
        """Normalized empty response warning (v1.0.0)."""
        return (
            "[empty_response] The Claude Code CLI returned an empty response. "
            "Check that the CLI is authenticated and that the task prompt is valid."
        )

    @staticmethod
    def format_mediation_rejected_warning(
        request_id: str,
        tool_name: str,
        rejection_reason: str,
        policy_decision: str,
    ) -> str:
        """Normalized mediation rejection warning (v1.0.0)."""
        return (
            f"[mediation_rejected] Mediated action '{request_id}' for tool '{tool_name}' "
            f"was rejected. Reason: {rejection_reason}. Policy decision: {policy_decision}."
        )

    @staticmethod
    def format_federation_inactive_warning(request_id: str, tool_name: str) -> str:
        """Normalized federation-inactive rejection warning (v1.0.0)."""
        return (
            f"[federation_inactive] Mediated action '{request_id}' for tool '{tool_name}' "
            "was rejected: federation is not active in this runtime. "
            "Enable and configure federation to use mediated tool execution."
        )
