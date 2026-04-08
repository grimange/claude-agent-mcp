"""Workflow executor — mediates between MCP tools and the runtime.

Responsibilities:
- resolve profile
- consult policy engine
- manage session lifecycle
- invoke agent adapter
- store transcript events
- produce canonical AgentResponse envelopes
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any

from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import (
    AgentMCPError,
    PolicyDeniedError,
    ProviderRuntimeError,
    SessionStatusError,
    ValidationError,
)
from claude_agent_mcp.backends.base import ExecutionBackend
from claude_agent_mcp.federation.invoker import DownstreamToolInvoker, build_invoker
from claude_agent_mcp.federation.visibility import ToolVisibilityResolver
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.continuation_builder import ContinuationContextBuilder
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import Profile, ProfileRegistry
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.runtime.mediation_engine import (
    MediationEngine,
    POLICY_APPROVED,
)
from claude_agent_mcp.types import (
    AgentResponse,
    ArtifactReference,
    ContinueSessionRequest,
    ErrorObject,
    EventType,
    MediatedActionRequest,
    MediatedActionResult,
    MediatedActionStatus,
    MediatedWorkflowRequest,
    MediatedWorkflowResult,
    MediatedWorkflowStepResult,
    NormalizedProviderResult,
    NormalizedVerificationResult,
    ProfileName,
    RunTaskRequest,
    SessionStatus,
    VerificationVerdict,
    VerifyTaskRequest,
    WorkflowName,
)

logger = logging.getLogger(__name__)


class WorkflowExecutor:
    """Shared executor for all workflow tools."""

    def __init__(
        self,
        config: Config,
        session_store: SessionStore,
        artifact_store: ArtifactStore,
        policy_engine: PolicyEngine,
        profile_registry: ProfileRegistry,
        execution_backend: ExecutionBackend,
        # Optional federation components (v0.3) — None means federation is inactive
        visibility_resolver: ToolVisibilityResolver | None = None,
        federation_server_configs: list | None = None,
    ) -> None:
        self._config = config
        self._sessions = session_store
        self._artifacts = artifact_store
        self._policy = policy_engine
        self._profiles = profile_registry
        self._backend = execution_backend
        self._visibility_resolver: ToolVisibilityResolver | None = visibility_resolver
        self._federation_server_configs: list = federation_server_configs or []
        # v0.8.0: Execution mediation engine
        self._mediation = MediationEngine(config, visibility_resolver)

    # ------------------------------------------------------------------
    # run_task
    # ------------------------------------------------------------------

    async def run_task(self, req: RunTaskRequest) -> AgentResponse:
        profile = self._profiles.get(req.system_profile)
        max_turns = self._profiles.resolve_turns(profile, req.max_turns)

        warnings: list[str] = []
        errors: list[ErrorObject] = []

        # Policy validation
        try:
            resolved_dir = self._policy.validate_run_request(
                profile, req.working_directory, max_turns, req.attachments
            )
        except (PolicyDeniedError, ValidationError) as exc:
            return self._error_response(
                session_id="",
                workflow=WorkflowName.run_task,
                profile=req.system_profile,
                error=exc,
            )

        # Create session
        session = await self._sessions.create_session(
            workflow=WorkflowName.run_task,
            profile=req.system_profile,
            working_directory=resolved_dir,
        )
        session_id = session.session_id
        lock_owner = f"exec_{uuid.uuid4().hex[:8]}"

        try:
            await self._sessions.acquire_lock(session_id, lock_owner)
            await self._sessions.update_session(
                session_id,
                status=SessionStatus.running,
                request_count_delta=1,
            )
            await self._sessions.append_event(
                session_id, EventType.user_input, 0, {"task": req.task}
            )
            await self._sessions.append_event(
                session_id, EventType.system_prompt_resolved, 0,
                {"profile": profile.name.value}
            )
            await self._sessions.append_event(
                session_id, EventType.policy_decision, 0,
                {"working_directory": resolved_dir, "max_turns": max_turns}
            )
            await self._sessions.append_event(
                session_id, EventType.provider_request_start, 0, {}
            )

            # Resolve federation tools for this profile
            invoker = self._build_invoker(req.system_profile, session_id)
            visible_tools = self._visible_tool_dicts(req.system_profile)

            # Capability check: warn if tools are resolved but backend doesn't support them.
            caps = self._backend.capabilities
            if visible_tools and not caps.supports_downstream_tools:
                if (
                    caps.supports_limited_downstream_tools
                    and getattr(self._config, "claude_code_enable_limited_tool_forwarding", False)
                ):
                    # v0.6: Limited tool forwarding — screen tools, inject compatible ones as text
                    from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend
                    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools(visible_tools)
                    await self._sessions.append_event(
                        session_id, EventType.downstream_tool_catalog_resolved, 0,
                        {
                            "visible_tools": [t["name"] for t in visible_tools],
                            "forwarded": len(compatible),
                            "dropped": len(screened_out),
                            "dropped_names": [r.tool_name for r in screened_out],
                            "forwarding_mode": "limited_text_injection",
                        },
                    )
                    result = await self._backend.execute(
                        system_prompt=profile.system_prompt,
                        task=req.task,
                        max_turns=max_turns,
                        tools=compatible if compatible else None,
                        is_continuation=False,
                    )
                else:
                    cap_warning = (
                        f"Backend '{self._backend.name}' does not support downstream federation "
                        "tools. Visible tools will not be forwarded. Switch to the 'api' backend "
                        "to use federation tools."
                    )
                    warnings.append(cap_warning)
                    await self._sessions.append_event(
                        session_id, EventType.downstream_tool_catalog_resolved, 0,
                        {
                            "visible_tools": [t["name"] for t in visible_tools],
                            "forwarded": False,
                            "reason": f"backend '{self._backend.name}' does not support downstream tools",
                        },
                    )
                    result = await self._backend.execute(
                        system_prompt=profile.system_prompt,
                        task=req.task,
                        max_turns=max_turns,
                        is_continuation=False,
                    )
            elif visible_tools and invoker is not None:
                await self._sessions.append_event(
                    session_id, EventType.downstream_tool_catalog_resolved, 0,
                    {"visible_tools": [t["name"] for t in visible_tools]},
                )
                result = await self._backend.execute(
                    system_prompt=profile.system_prompt,
                    task=req.task,
                    max_turns=max_turns,
                    tools=visible_tools,
                    tool_executor=self._make_tool_executor(invoker, session_id, 0),
                    is_continuation=False,
                )
            else:
                result = await self._backend.execute(
                    system_prompt=profile.system_prompt,
                    task=req.task,
                    max_turns=max_turns,
                    is_continuation=False,
                )

            warnings.extend(result.warnings)

            # v0.8.0: Process mediated action requests from backend output.
            mediated_results = await self._process_mediated_actions(
                output_text=result.output_text,
                session_id=session_id,
                profile_name=req.system_profile.value,
                turn_index=result.turn_count,
                invoker=invoker,
            )
            for mr in mediated_results:
                if mr.status == MediatedActionStatus.rejected:
                    warnings.append(
                        f"Mediated action {mr.request_id!r} for tool {mr.tool_name!r} "
                        f"rejected — {mr.failure_reason} (policy: {mr.policy_decision})"
                    )

            summary = self._make_summary(result.output_text)

            await self._sessions.append_event(
                session_id, EventType.provider_response_summary, result.turn_count,
                {"summary": summary, "stop_reason": result.stop_reason}
            )

            provider_sid = result.provider_session_id
            await self._sessions.update_session(
                session_id,
                status=SessionStatus.completed,
                turn_count=result.turn_count,
                summary_latest=summary,
                provider_session_id=provider_sid,
            )

            artifact_refs = await self._maybe_save_output_artifact(
                session_id, result, profile, WorkflowName.run_task, result.turn_count
            )

            response = AgentResponse(
                ok=True,
                session_id=session_id,
                status=SessionStatus.completed,
                workflow=WorkflowName.run_task,
                profile=req.system_profile,
                summary=summary,
                result={"output_text": result.output_text},
                artifacts=artifact_refs,
                warnings=warnings,
                errors=errors,
            )

        except AgentMCPError as exc:
            logger.exception("Workflow error in run_task: %s", exc)
            await self._sessions.update_session(session_id, status=SessionStatus.failed)
            await self._sessions.append_event(
                session_id, EventType.error_event, 0, {"error": exc.to_dict()}
            )
            response = self._error_response(
                session_id=session_id,
                workflow=WorkflowName.run_task,
                profile=req.system_profile,
                error=exc,
            )
        except Exception as exc:
            logger.exception("Unexpected error in run_task")
            await self._sessions.update_session(session_id, status=SessionStatus.failed)
            wrapped = ProviderRuntimeError(str(exc))
            response = self._error_response(
                session_id=session_id,
                workflow=WorkflowName.run_task,
                profile=req.system_profile,
                error=wrapped,
            )
        finally:
            await self._sessions.release_lock(session_id, lock_owner)

        return response

    # ------------------------------------------------------------------
    # continue_session
    # ------------------------------------------------------------------

    async def continue_session(self, req: ContinueSessionRequest) -> AgentResponse:
        lock_owner = f"cont_{uuid.uuid4().hex[:8]}"

        try:
            session = await self._sessions.get_session(req.session_id)
        except AgentMCPError as exc:
            return self._error_response(
                session_id=req.session_id,
                workflow=WorkflowName.continue_session,
                profile=ProfileName.general,
                error=exc,
            )

        profile = self._profiles.get(session.profile)
        max_turns = self._profiles.resolve_turns(profile, req.max_turns)

        # Policy validation must happen before the operational try block so that a
        # denial returns a clean error without corrupting the session status.
        try:
            self._policy.validate_continuation(
                profile, session.status, session.turn_count, max_turns
            )
        except (PolicyDeniedError, ValidationError) as exc:
            return self._error_response(
                session_id=req.session_id,
                workflow=WorkflowName.continue_session,
                profile=session.profile,
                error=exc,
            )

        warnings: list[str] = []

        try:
            await self._sessions.acquire_lock(req.session_id, lock_owner)
            await self._sessions.update_session(
                req.session_id,
                status=SessionStatus.running,
                request_count_delta=1,
            )

            # Reconstruct conversation history from session events (for API backend)
            history = await self._build_conversation_history(req.session_id)

            # v0.7.0: Build structured continuation context for backends that support it.
            caps = self._backend.capabilities
            continuation_context = None
            if caps.supports_structured_continuation_context:
                all_events = await self._sessions.get_events(req.session_id)
                policy = ContinuationContextBuilder.build_policy(self._config)
                continuation_context = ContinuationContextBuilder.build_context(
                    session=session,
                    events=all_events,
                    policy=policy,
                    config=self._config,
                )
                # Record continuation context built event
                stats_dict = {}
                if continuation_context.render_stats:
                    s = continuation_context.render_stats
                    stats_dict = {
                        "turns_included": s.turns_included,
                        "turns_omitted": s.turns_omitted,
                        "warnings_included": s.warnings_included,
                        "warnings_omitted": s.warnings_omitted,
                        "forwarding_events_included": s.forwarding_events_included,
                        "forwarding_events_omitted": s.forwarding_events_omitted,
                        "reconstruction_version": s.reconstruction_version,
                    }
                await self._sessions.append_event(
                    req.session_id,
                    EventType.session_continuation_context_built,
                    session.turn_count,
                    {"policy": policy.model_dump(), "stats": stats_dict},
                )
                if continuation_context.render_stats and (
                    continuation_context.render_stats.turns_omitted > 0
                    or continuation_context.render_stats.warnings_omitted > 0
                ):
                    await self._sessions.append_event(
                        req.session_id,
                        EventType.session_continuation_context_truncated,
                        session.turn_count,
                        {"stats": stats_dict},
                    )

            await self._sessions.append_event(
                req.session_id, EventType.user_input, session.turn_count,
                {"message": req.message}
            )
            await self._sessions.append_event(
                req.session_id, EventType.provider_request_start, session.turn_count, {}
            )

            # Resolve federation tools for this profile
            invoker = self._build_invoker(session.profile, req.session_id)
            visible_tools = self._visible_tool_dicts(session.profile)

            # Capability check: warn if tools are resolved but backend doesn't support them.
            if visible_tools and not caps.supports_downstream_tools:
                if (
                    caps.supports_limited_downstream_tools
                    and getattr(self._config, "claude_code_enable_limited_tool_forwarding", False)
                ):
                    # v0.6: Limited tool forwarding — screen tools, inject compatible ones as text
                    from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend
                    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools(visible_tools)
                    await self._sessions.append_event(
                        req.session_id, EventType.downstream_tool_catalog_resolved, session.turn_count,
                        {
                            "visible_tools": [t["name"] for t in visible_tools],
                            "forwarded": len(compatible),
                            "dropped": len(screened_out),
                            "dropped_names": [r.tool_name for r in screened_out],
                            "forwarding_mode": "limited_text_injection",
                        },
                    )
                    result = await self._backend.execute(
                        system_prompt=profile.system_prompt,
                        task=req.message,
                        max_turns=max_turns,
                        tools=compatible if compatible else None,
                        conversation_history=history,
                        session_summary=session.summary_latest,
                        is_continuation=True,
                        continuation_context=continuation_context,
                    )
                else:
                    cap_warning = (
                        f"Backend '{self._backend.name}' does not support downstream federation "
                        "tools. Visible tools will not be forwarded. Switch to the 'api' backend "
                        "to use federation tools."
                    )
                    warnings.append(cap_warning)
                    await self._sessions.append_event(
                        req.session_id, EventType.downstream_tool_catalog_resolved, session.turn_count,
                        {
                            "visible_tools": [t["name"] for t in visible_tools],
                            "forwarded": False,
                            "reason": f"backend '{self._backend.name}' does not support downstream tools",
                        },
                    )
                    result = await self._backend.execute(
                        system_prompt=profile.system_prompt,
                        task=req.message,
                        max_turns=max_turns,
                        conversation_history=history,
                        session_summary=session.summary_latest,
                        is_continuation=True,
                        continuation_context=continuation_context,
                    )
            elif visible_tools and invoker is not None:
                await self._sessions.append_event(
                    req.session_id, EventType.downstream_tool_catalog_resolved, session.turn_count,
                    {"visible_tools": [t["name"] for t in visible_tools]},
                )
                result = await self._backend.execute(
                    system_prompt=profile.system_prompt,
                    task=req.message,
                    max_turns=max_turns,
                    tools=visible_tools,
                    tool_executor=self._make_tool_executor(invoker, req.session_id, session.turn_count),
                    conversation_history=history,
                    session_summary=session.summary_latest,
                    is_continuation=True,
                    continuation_context=continuation_context,
                )
            else:
                result = await self._backend.execute(
                    system_prompt=profile.system_prompt,
                    task=req.message,
                    max_turns=max_turns,
                    conversation_history=history,
                    session_summary=session.summary_latest,
                    is_continuation=True,
                    continuation_context=continuation_context,
                )

            # v0.7.0: Record that continuation prompt was rendered
            if continuation_context is not None:
                await self._sessions.append_event(
                    req.session_id,
                    EventType.session_continuation_prompt_rendered,
                    session.turn_count,
                    {"reconstruction_version": continuation_context.reconstruction_version},
                )

            warnings.extend(result.warnings)

            # v0.8.0: Process mediated action requests from backend output.
            new_turn_count = session.turn_count + result.turn_count
            mediated_results = await self._process_mediated_actions(
                output_text=result.output_text,
                session_id=req.session_id,
                profile_name=session.profile.value,
                turn_index=new_turn_count,
                invoker=invoker,
            )
            for mr in mediated_results:
                if mr.status == MediatedActionStatus.rejected:
                    warnings.append(
                        f"Mediated action {mr.request_id!r} for tool {mr.tool_name!r} "
                        f"rejected — {mr.failure_reason} (policy: {mr.policy_decision})"
                    )

            summary = self._make_summary(result.output_text)

            await self._sessions.append_event(
                req.session_id, EventType.provider_response_summary, new_turn_count,
                {"summary": summary, "stop_reason": result.stop_reason}
            )
            await self._sessions.update_session(
                req.session_id,
                status=SessionStatus.completed,
                turn_count=new_turn_count,
                summary_latest=summary,
            )

            artifact_refs = await self._maybe_save_output_artifact(
                req.session_id, result, profile, WorkflowName.continue_session, new_turn_count
            )

            response = AgentResponse(
                ok=True,
                session_id=req.session_id,
                status=SessionStatus.completed,
                workflow=WorkflowName.continue_session,
                profile=session.profile,
                summary=summary,
                result={"output_text": result.output_text},
                artifacts=artifact_refs,
                warnings=warnings,
                errors=[],
            )

        except AgentMCPError as exc:
            logger.exception("Workflow error in continue_session: %s", exc)
            await self._sessions.update_session(req.session_id, status=SessionStatus.failed)
            await self._sessions.append_event(
                req.session_id, EventType.error_event, session.turn_count,
                {"error": exc.to_dict()}
            )
            response = self._error_response(
                session_id=req.session_id,
                workflow=WorkflowName.continue_session,
                profile=session.profile,
                error=exc,
            )
        except Exception as exc:
            logger.exception("Unexpected error in continue_session")
            await self._sessions.update_session(req.session_id, status=SessionStatus.failed)
            wrapped = ProviderRuntimeError(str(exc))
            response = self._error_response(
                session_id=req.session_id,
                workflow=WorkflowName.continue_session,
                profile=session.profile,
                error=wrapped,
            )
        finally:
            await self._sessions.release_lock(req.session_id, lock_owner)

        return response

    # ------------------------------------------------------------------
    # verify_task
    # ------------------------------------------------------------------

    async def verify_task(self, req: VerifyTaskRequest) -> AgentResponse:
        # Force verification profile
        profile = self._profiles.get(ProfileName.verification)
        max_turns = self._profiles.resolve_turns(profile, req.max_turns if hasattr(req, 'max_turns') else None)

        warnings: list[str] = []
        errors: list[ErrorObject] = []

        # Validate evidence paths exist before policy (so missing paths return a
        # structured verification error rather than a generic policy error).
        evidence_issues: list[str] = []
        for ep in req.evidence_paths:
            p = Path(ep) if Path(ep).is_absolute() else Path.cwd() / ep
            if not p.exists():
                evidence_issues.append(f"Evidence path not found: {ep}")

        if evidence_issues and req.fail_closed:
            return self._verification_error_response(
                session_id="",
                verdict=VerificationVerdict.fail_closed,
                warnings=warnings,
                errors=[ErrorObject(code="missing_evidence", message=msg) for msg in evidence_issues],
            )
        elif evidence_issues:
            warnings.extend(evidence_issues)

        # Policy validation — pass empty attachment list since evidence paths were
        # already checked above (only existing paths reach here).
        try:
            resolved_dir = self._policy.validate_run_request(
                profile,
                None,  # use cwd for verification
                max_turns,
                [ep for ep in req.evidence_paths if not any(ep in iss for iss in evidence_issues)],
            )
        except (PolicyDeniedError, ValidationError) as exc:
            return self._error_response(
                session_id="",
                workflow=WorkflowName.verify_task,
                profile=ProfileName.verification,
                error=exc,
            )

        session = await self._sessions.create_session(
            workflow=WorkflowName.verify_task,
            profile=ProfileName.verification,
            working_directory=resolved_dir,
        )
        session_id = session.session_id
        lock_owner = f"ver_{uuid.uuid4().hex[:8]}"

        try:
            await self._sessions.acquire_lock(session_id, lock_owner)
            await self._sessions.update_session(
                session_id,
                status=SessionStatus.running,
                request_count_delta=1,
            )

            # Build verification task prompt
            task_prompt = self._build_verification_prompt(req)

            await self._sessions.append_event(
                session_id, EventType.user_input, 0, {"task": req.task, "scope": req.scope}
            )
            await self._sessions.append_event(
                session_id, EventType.policy_decision, 0,
                {"fail_closed": req.fail_closed, "evidence_paths": req.evidence_paths}
            )
            await self._sessions.append_event(
                session_id, EventType.provider_request_start, 0, {}
            )

            raw_result = await self._backend.execute(
                system_prompt=profile.system_prompt,
                task=task_prompt,
                max_turns=max_turns,
            )

            warnings.extend(raw_result.warnings)

            ver_result = self._parse_verification_result(
                raw_result.output_text, req.fail_closed
            )
            summary = f"Verification {ver_result.verdict.value}: {len(ver_result.findings)} finding(s)"

            await self._sessions.append_event(
                session_id, EventType.provider_response_summary, raw_result.turn_count,
                {"verdict": ver_result.verdict.value, "summary": summary}
            )
            await self._sessions.append_event(
                session_id, EventType.workflow_normalization, raw_result.turn_count,
                {"verdict": ver_result.verdict.value}
            )

            await self._sessions.update_session(
                session_id,
                status=SessionStatus.completed,
                turn_count=raw_result.turn_count,
                summary_latest=summary,
            )

            artifact_refs = await self._save_verification_report(
                session_id, ver_result, raw_result.output_text, profile, raw_result.turn_count
            )

            response = AgentResponse(
                ok=True,
                session_id=session_id,
                status=SessionStatus.completed,
                workflow=WorkflowName.verify_task,
                profile=ProfileName.verification,
                summary=summary,
                result={
                    "verdict": ver_result.verdict.value,
                    "findings": ver_result.findings,
                    "contradictions": ver_result.contradictions,
                    "missing_evidence": ver_result.missing_evidence,
                    "restrictions": ver_result.restrictions,
                },
                artifacts=artifact_refs,
                warnings=warnings,
                errors=errors,
            )

        except AgentMCPError as exc:
            logger.exception("Workflow error in verify_task: %s", exc)
            await self._sessions.update_session(session_id, status=SessionStatus.failed)
            await self._sessions.append_event(
                session_id, EventType.error_event, 0, {"error": exc.to_dict()}
            )
            response = self._error_response(
                session_id=session_id,
                workflow=WorkflowName.verify_task,
                profile=ProfileName.verification,
                error=exc,
            )
        except Exception as exc:
            logger.exception("Unexpected error in verify_task")
            await self._sessions.update_session(session_id, status=SessionStatus.failed)
            wrapped = ProviderRuntimeError(str(exc))
            response = self._error_response(
                session_id=session_id,
                workflow=WorkflowName.verify_task,
                profile=ProfileName.verification,
                error=wrapped,
            )
        finally:
            await self._sessions.release_lock(session_id, lock_owner)

        return response

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_summary(self, text: str, max_len: int = 200) -> str:
        first_line = text.strip().split("\n")[0] if text.strip() else "(empty response)"
        if len(first_line) > max_len:
            return first_line[:max_len] + "..."
        return first_line

    async def _build_conversation_history(
        self, session_id: str
    ) -> list[dict[str, Any]]:
        """Reconstruct a minimal Messages API conversation from session events."""
        events = await self._sessions.get_events(session_id)
        messages: list[dict[str, Any]] = []

        for event in events:
            if event.event_type == EventType.user_input:
                text = event.payload.get("task") or event.payload.get("message", "")
                if text:
                    messages.append({"role": "user", "content": text})
            elif event.event_type == EventType.provider_response_summary:
                summary = event.payload.get("summary", "")
                if summary:
                    messages.append({"role": "assistant", "content": summary})

        # Remove the last message if it's a user input (it will be added by the adapter)
        if messages and messages[-1]["role"] == "user":
            messages = messages[:-1]

        return messages

    def _build_verification_prompt(self, req: VerifyTaskRequest) -> str:
        parts = [f"Task to verify: {req.task}"]
        if req.scope:
            parts.append(f"Scope: {req.scope}")
        if req.evidence_paths:
            parts.append("Evidence paths:")
            for ep in req.evidence_paths:
                parts.append(f"  - {ep}")
        parts.append("")
        parts.append(
            "Please evaluate the evidence and provide a structured verification result."
        )
        return "\n".join(parts)

    def _parse_verification_result(
        self, text: str, fail_closed: bool
    ) -> NormalizedVerificationResult:
        """Parse structured verification output from the model.

        Looks for labeled sections. Falls back to fail_closed / insufficient_evidence.
        """
        verdict = None
        findings: list[str] = []
        contradictions: list[str] = []
        missing_evidence: list[str] = []
        restrictions: list[str] = []

        # Extract VERDICT line
        verdict_match = re.search(r"VERDICT\s*:\s*(\S+)", text, re.IGNORECASE)
        if verdict_match:
            raw = verdict_match.group(1).strip().lower().rstrip(".")
            # Map common synonyms
            mapping = {
                "pass": VerificationVerdict.pass_,
                "pass_with_restrictions": VerificationVerdict.pass_with_restrictions,
                "fail_closed": VerificationVerdict.fail_closed,
                "fail": VerificationVerdict.fail_closed,
                "insufficient_evidence": VerificationVerdict.insufficient_evidence,
                "insufficient": VerificationVerdict.insufficient_evidence,
            }
            verdict = mapping.get(raw)

        if verdict is None:
            verdict = (
                VerificationVerdict.fail_closed
                if fail_closed
                else VerificationVerdict.insufficient_evidence
            )

        def _extract_list(label: str) -> list[str]:
            pattern = rf"{label}\s*:\s*\n((?:\s*[-*•]\s*.+\n?)*)"
            m = re.search(pattern, text, re.IGNORECASE)
            if not m:
                return []
            block = m.group(1)
            items = re.findall(r"[-*•]\s*(.+)", block)
            return [i.strip() for i in items if i.strip()]

        findings = _extract_list("FINDINGS")
        contradictions = _extract_list("CONTRADICTIONS")
        missing_evidence = _extract_list("MISSING_EVIDENCE")
        restrictions = _extract_list("RESTRICTIONS")

        return NormalizedVerificationResult(
            verdict=verdict,
            findings=findings,
            contradictions=contradictions,
            missing_evidence=missing_evidence,
            restrictions=restrictions,
            output_text=text,
        )

    async def _maybe_save_output_artifact(
        self,
        session_id: str,
        result: NormalizedProviderResult,
        profile: Profile,
        workflow: WorkflowName,
        turn_count: int,
    ) -> list[ArtifactReference]:
        """Save a text artifact if the profile allows it and output is non-trivial."""
        if not profile.artifact_policy.allow_write:
            return []
        if not result.output_text or len(result.output_text) < 50:
            return []
        try:
            rec = await self._artifacts.save_artifact(
                session_id,
                result.output_text.encode("utf-8"),
                workflow=workflow.value,
                profile=profile.name.value,
                artifact_type="output",
                logical_name="output.txt",
                mime_type="text/plain",
                turn_index=turn_count,
                producer_tool=workflow.value,
            )
            await self._sessions.update_session(session_id, artifact_count_delta=1)
            return [self._artifacts.to_reference(rec)]
        except Exception as exc:
            logger.warning("Failed to save output artifact: %s", exc)
            return []

    async def _save_verification_report(
        self,
        session_id: str,
        ver_result: NormalizedVerificationResult,
        raw_text: str,
        profile: Profile,
        turn_count: int,
    ) -> list[ArtifactReference]:
        """Save verification report as an artifact."""
        if not profile.artifact_policy.allow_write:
            return []
        try:
            content = raw_text.encode("utf-8")
            rec = await self._artifacts.save_artifact(
                session_id,
                content,
                workflow=WorkflowName.verify_task.value,
                profile=profile.name.value,
                artifact_type="verification-report",
                logical_name="verification-report.md",
                mime_type="text/markdown",
                turn_index=turn_count,
                producer_tool="verify_task",
            )
            await self._sessions.update_session(session_id, artifact_count_delta=1)
            return [self._artifacts.to_reference(rec)]
        except Exception as exc:
            logger.warning("Failed to save verification report: %s", exc)
            return []

    # ------------------------------------------------------------------
    # Federation helpers
    # ------------------------------------------------------------------

    def _build_invoker(
        self,
        profile: ProfileName,
        session_id: str,
    ) -> DownstreamToolInvoker | None:
        """Build a DownstreamToolInvoker for the given profile, or None if federation inactive."""
        if self._visibility_resolver is None:
            return None
        return build_invoker(
            profile=profile,
            visibility_resolver=self._visibility_resolver,
            server_configs=self._federation_server_configs,
            session_store=self._sessions,
        )

    def _visible_tool_dicts(self, profile: ProfileName) -> list[dict]:
        """Return Anthropic tool definition dicts for tools visible to this profile."""
        if self._visibility_resolver is None:
            return []
        visible = self._visibility_resolver.resolve(profile)
        return [t.to_anthropic_tool_dict() for t in visible]

    def _make_tool_executor(
        self,
        invoker: DownstreamToolInvoker,
        session_id: str,
        turn_index: int,
    ):
        """Return an async callable suitable for passing to run_with_tools."""
        async def _executor(tool_name: str, tool_input: dict) -> str:
            result = await invoker.invoke(
                normalized_name=tool_name,
                tool_input=tool_input,
                session_id=session_id,
                turn_index=turn_index,
            )
            return result.to_content_string()
        return _executor

    # ------------------------------------------------------------------
    # Execution mediation (v0.8.0/v0.9.0)
    # ------------------------------------------------------------------

    async def _count_session_approvals(self, session_id: str) -> int:
        """Count all mediated approvals persisted for this session so far (v0.9.0).

        Used to enforce session-level approval limits across turns.
        Counts both single-action (v0.8.0) and workflow step (v0.9.0) approval events.

        Args:
            session_id: The session to count approvals for.

        Returns:
            Total count of mediated_action_approved + mediated_workflow_step_approved events.
        """
        try:
            events = await self._sessions.get_events(session_id)
        except Exception:
            return 0

        approval_types = {
            EventType.mediated_action_approved,
            EventType.mediated_workflow_step_approved,
        }
        return sum(1 for e in events if e.event_type in approval_types)

    async def _process_mediated_actions(
        self,
        output_text: str,
        session_id: str,
        profile_name: str,
        turn_index: int,
        invoker: DownstreamToolInvoker | None,
    ) -> list[MediatedActionResult]:
        """Parse, validate, and execute mediated action and workflow requests from backend output.

        Called after each backend execution. Handles both v0.8.0 single-action request
        blocks and v0.9.0 bounded workflow request blocks. Only runs if the backend
        declares supports_execution_mediation.

        Returns all results (approved+executed, approved+failed, rejected) for the
        caller to emit warnings and persist events.

        Args:
            output_text: Raw backend output text to parse requests from.
            session_id: Active session identifier.
            profile_name: Active profile name for visibility validation.
            turn_index: Current turn index for session event persistence.
            invoker: Federation invoker for executing approved actions.
                     When None, all requests requiring federation are rejected.

        Returns:
            List of MediatedActionResult objects for all parsed requests
            (from both single-action and workflow formats).
        """
        caps = self._backend.capabilities
        if not getattr(caps, "supports_execution_mediation", False):
            return []

        # Count session-level approvals from prior turns for limit enforcement (v0.9.0).
        session_approved_total = await self._count_session_approvals(session_id)

        all_results: list[MediatedActionResult] = []
        actions_approved_this_turn = 0

        # --- v0.8.0: process single-action request blocks ---
        single_action_requests = self._mediation.parse_requests(output_text)
        for req in single_action_requests:
            result = await self._process_single_action(
                req=req,
                session_id=session_id,
                profile_name=profile_name,
                turn_index=turn_index,
                invoker=invoker,
                actions_approved_this_turn=actions_approved_this_turn,
                session_approved_total=session_approved_total,
            )
            all_results.append(result)
            if result.status == MediatedActionStatus.approved or result.status == MediatedActionStatus.completed:
                actions_approved_this_turn += 1

        # --- v0.9.0: process bounded workflow request blocks ---
        if getattr(caps, "supports_bounded_mediated_workflows", False):
            workflows = self._mediation.parse_workflow(output_text)
            for wf in workflows:
                wf_results = await self._process_workflow(
                    workflow=wf,
                    session_id=session_id,
                    profile_name=profile_name,
                    turn_index=turn_index,
                    invoker=invoker,
                    actions_approved_this_turn=actions_approved_this_turn,
                    session_approved_total=session_approved_total,
                )
                for step_result in wf_results.step_results:
                    all_results.append(step_result.action_result)
                    if step_result.action_result.status in (
                        MediatedActionStatus.approved,
                        MediatedActionStatus.completed,
                    ):
                        actions_approved_this_turn += 1

        return all_results

    async def _process_single_action(
        self,
        req: "MediatedActionRequest",
        session_id: str,
        profile_name: str,
        turn_index: int,
        invoker: DownstreamToolInvoker | None,
        actions_approved_this_turn: int,
        session_approved_total: int,
    ) -> MediatedActionResult:
        """Process a single v0.8.0 mediated action request end-to-end.

        Persists requested → (approved|rejected) → completed events.
        Returns the final MediatedActionResult.
        """

        # Persist: action requested
        await self._sessions.append_event(
            session_id,
            EventType.mediated_action_requested,
            turn_index,
            {
                "request_id": req.request_id,
                "action_type": req.action_type.value,
                "target_tool": req.target_tool,
                "justification": req.justification[:200],
                "mediation_version": req.mediation_version,
            },
        )

        is_approved, policy_decision = self._mediation.validate_request(
            req,
            profile_name,
            actions_approved_this_turn,
            session_approved_total=session_approved_total,
        )

        if not is_approved:
            result = self._mediation.make_rejection_result(req, policy_decision)
            await self._sessions.append_event(
                session_id,
                EventType.mediated_action_rejected,
                turn_index,
                {
                    "request_id": req.request_id,
                    "target_tool": req.target_tool,
                    "policy_decision": policy_decision,
                    "failure_reason": result.failure_reason,
                },
            )
            logger.info(
                "Mediated action %r rejected: %s", req.request_id, policy_decision
            )
            return result

        # Persist: action approved
        await self._sessions.append_event(
            session_id,
            EventType.mediated_action_approved,
            turn_index,
            {
                "request_id": req.request_id,
                "target_tool": req.target_tool,
                "action_type": req.action_type.value,
                "policy_decision": policy_decision,
            },
        )

        # Execute the approved action.
        if invoker is None:
            logger.error(
                "mediation: invoker is None for approved action %r — this is a bug",
                req.request_id,
            )
            result = self._mediation.make_rejection_result(
                req, "rejected:internal_invoker_unavailable"
            )
            return result

        result = await self._mediation.execute_action(req, invoker, session_id, turn_index)

        # Persist: action completed (or failed)
        await self._sessions.append_event(
            session_id,
            EventType.mediated_action_completed,
            turn_index,
            {
                "request_id": req.request_id,
                "target_tool": req.target_tool,
                "status": result.status.value,
                "arguments_summary": result.arguments_summary,
                "result_summary": result.result_summary[:200],
                "failure_reason": result.failure_reason,
                "policy_decision": result.policy_decision,
            },
        )
        return result

    async def _process_workflow(
        self,
        workflow: "MediatedWorkflowRequest",
        session_id: str,
        profile_name: str,
        turn_index: int,
        invoker: DownstreamToolInvoker | None,
        actions_approved_this_turn: int,
        session_approved_total: int,
    ) -> MediatedWorkflowResult:
        """Process a v0.9.0 bounded mediated workflow request end-to-end.

        Emits workflow-level events (requested, completed) and per-step events
        (step_requested, step_approved/rejected, step_completed).

        Each step is individually validated. A rejected step does not stop
        subsequent step processing (each step fails independently).

        Args:
            workflow: The parsed MediatedWorkflowRequest.
            session_id: Active session identifier.
            profile_name: Active profile name.
            turn_index: Current turn index.
            invoker: Federation invoker for approved steps.
            actions_approved_this_turn: Approvals in the current turn so far.
            session_approved_total: Session-level approval count from prior turns.

        Returns:
            MediatedWorkflowResult with per-step results and aggregate counts.
        """
        # Emit workflow-level requested event.
        await self._sessions.append_event(
            session_id,
            EventType.mediated_workflow_requested,
            turn_index,
            {
                "workflow_id": workflow.workflow_id,
                "step_count": len(workflow.steps),
                "mediation_version": workflow.mediation_version,
                "justification": workflow.justification[:200],
            },
        )

        # Validate workflow-level constraints first.
        wf_ok, wf_policy = self._mediation.validate_workflow_request(workflow)
        if not wf_ok:
            # All steps rejected at the workflow level.
            step_results: list[MediatedWorkflowStepResult] = []
            rejection_reason = self._mediation.rejection_reason_enum(wf_policy)
            for step in workflow.steps:
                dummy_req = self._mediation.step_to_action_request(step, workflow.mediation_version)
                action_result = self._mediation.make_rejection_result(dummy_req, wf_policy)
                step_results.append(MediatedWorkflowStepResult(
                    step_index=step.step_index,
                    action_result=action_result,
                    rejection_reason=rejection_reason,
                ))
                await self._sessions.append_event(
                    session_id,
                    EventType.mediated_workflow_step_rejected,
                    turn_index,
                    {
                        "workflow_id": workflow.workflow_id,
                        "step_index": step.step_index,
                        "target_tool": step.target_tool,
                        "policy_decision": wf_policy,
                        "rejection_reason": rejection_reason.value,
                    },
                )
                logger.info(
                    "Workflow %r step %d rejected at workflow level: %s",
                    workflow.workflow_id,
                    step.step_index,
                    wf_policy,
                )

            wf_result = MediatedWorkflowResult(
                workflow_id=workflow.workflow_id,
                total_steps=len(workflow.steps),
                approved_steps=0,
                rejected_steps=len(workflow.steps),
                completed_steps=0,
                failed_steps=0,
                step_results=step_results,
            )
            await self._sessions.append_event(
                session_id,
                EventType.mediated_workflow_completed,
                turn_index,
                {
                    "workflow_id": workflow.workflow_id,
                    "total_steps": wf_result.total_steps,
                    "approved_steps": wf_result.approved_steps,
                    "rejected_steps": wf_result.rejected_steps,
                    "completed_steps": wf_result.completed_steps,
                    "failed_steps": wf_result.failed_steps,
                },
            )
            return wf_result

        # Process each step individually.
        step_results = []
        step_approved = 0
        step_rejected = 0
        step_completed = 0
        step_failed = 0
        step_actions_approved = actions_approved_this_turn  # track within this workflow

        for step in workflow.steps:
            req = self._mediation.step_to_action_request(step, workflow.mediation_version)

            # Emit step_requested event.
            await self._sessions.append_event(
                session_id,
                EventType.mediated_workflow_step_requested,
                turn_index,
                {
                    "workflow_id": workflow.workflow_id,
                    "step_index": step.step_index,
                    "action_type": step.action_type.value,
                    "target_tool": step.target_tool,
                    "justification": step.justification[:200],
                },
            )

            is_approved, policy_decision = self._mediation.validate_request(
                req,
                profile_name,
                step_actions_approved,
                session_approved_total=session_approved_total,
            )

            if not is_approved:
                rejection_reason = self._mediation.rejection_reason_enum(policy_decision)
                action_result = self._mediation.make_rejection_result(req, policy_decision)
                await self._sessions.append_event(
                    session_id,
                    EventType.mediated_workflow_step_rejected,
                    turn_index,
                    {
                        "workflow_id": workflow.workflow_id,
                        "step_index": step.step_index,
                        "target_tool": step.target_tool,
                        "policy_decision": policy_decision,
                        "rejection_reason": rejection_reason.value,
                        "failure_reason": action_result.failure_reason,
                    },
                )
                logger.info(
                    "Workflow %r step %d rejected: %s",
                    workflow.workflow_id,
                    step.step_index,
                    policy_decision,
                )
                step_results.append(MediatedWorkflowStepResult(
                    step_index=step.step_index,
                    action_result=action_result,
                    rejection_reason=rejection_reason,
                ))
                step_rejected += 1
                continue

            # Emit step_approved event.
            await self._sessions.append_event(
                session_id,
                EventType.mediated_workflow_step_approved,
                turn_index,
                {
                    "workflow_id": workflow.workflow_id,
                    "step_index": step.step_index,
                    "target_tool": step.target_tool,
                    "action_type": step.action_type.value,
                    "policy_decision": policy_decision,
                },
            )
            step_approved += 1
            step_actions_approved += 1

            # Execute the approved step.
            if invoker is None:
                logger.error(
                    "mediation: invoker is None for approved workflow %r step %d — this is a bug",
                    workflow.workflow_id,
                    step.step_index,
                )
                action_result = self._mediation.make_rejection_result(
                    req, "rejected:internal_invoker_unavailable"
                )
                step_results.append(MediatedWorkflowStepResult(
                    step_index=step.step_index,
                    action_result=action_result,
                    rejection_reason=None,
                ))
                step_failed += 1
                continue

            action_result = await self._mediation.execute_action(
                req, invoker, session_id, turn_index
            )

            # Emit step_completed event.
            await self._sessions.append_event(
                session_id,
                EventType.mediated_workflow_step_completed,
                turn_index,
                {
                    "workflow_id": workflow.workflow_id,
                    "step_index": step.step_index,
                    "target_tool": step.target_tool,
                    "status": action_result.status.value,
                    "arguments_summary": action_result.arguments_summary,
                    "result_summary": action_result.result_summary[:200],
                    "failure_reason": action_result.failure_reason,
                    "policy_decision": action_result.policy_decision,
                },
            )

            step_results.append(MediatedWorkflowStepResult(
                step_index=step.step_index,
                action_result=action_result,
                rejection_reason=None,
            ))

            if action_result.status == MediatedActionStatus.completed:
                step_completed += 1
            else:
                step_failed += 1

        wf_result = MediatedWorkflowResult(
            workflow_id=workflow.workflow_id,
            total_steps=len(workflow.steps),
            approved_steps=step_approved,
            rejected_steps=step_rejected,
            completed_steps=step_completed,
            failed_steps=step_failed,
            step_results=step_results,
        )

        # Emit workflow-level completed event with aggregate stats.
        await self._sessions.append_event(
            session_id,
            EventType.mediated_workflow_completed,
            turn_index,
            {
                "workflow_id": workflow.workflow_id,
                "total_steps": wf_result.total_steps,
                "approved_steps": wf_result.approved_steps,
                "rejected_steps": wf_result.rejected_steps,
                "completed_steps": wf_result.completed_steps,
                "failed_steps": wf_result.failed_steps,
            },
        )

        logger.info(
            "Workflow %r completed: %d/%d steps approved, %d completed, %d rejected, %d failed",
            workflow.workflow_id,
            wf_result.approved_steps,
            wf_result.total_steps,
            wf_result.completed_steps,
            wf_result.rejected_steps,
            wf_result.failed_steps,
        )

        return wf_result

    def _error_response(
        self,
        session_id: str,
        workflow: WorkflowName,
        profile: ProfileName,
        error: AgentMCPError,
    ) -> AgentResponse:
        return AgentResponse(
            ok=False,
            session_id=session_id,
            status=SessionStatus.failed,
            workflow=workflow,
            profile=profile,
            summary=error.message,
            result={},
            artifacts=[],
            warnings=[],
            errors=[ErrorObject(**error.to_dict())],
        )

    def _verification_error_response(
        self,
        session_id: str,
        verdict: VerificationVerdict,
        warnings: list[str],
        errors: list[ErrorObject],
    ) -> AgentResponse:
        return AgentResponse(
            ok=False,
            session_id=session_id,
            status=SessionStatus.failed,
            workflow=WorkflowName.verify_task,
            profile=ProfileName.verification,
            summary=f"Verification {verdict.value}",
            result={
                "verdict": verdict.value,
                "findings": [],
                "contradictions": [],
                "missing_evidence": [],
                "restrictions": [],
            },
            artifacts=[],
            warnings=warnings,
            errors=errors,
        )
