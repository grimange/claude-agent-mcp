"""Tests for v0.7.0 Claude Code session continuity features.

Covers:
- Continuation context construction from session events
- ContinuationWindowPolicy config field resolution
- Deterministic section rendering (non-empty sections only)
- Empty section omission
- Bounded truncation behavior under configured limits
- Warning relevance classification and filtering
- Forwarding continuity summarization
- BackendCapabilities v0.7.0 flags
- Cross-backend contract expectations (both backends accept continuation_context)
- Regression: v0.6 prompt framing still works without continuation_context
- Regression: existing execute() calls without continuation_context unchanged
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_mcp.backends.api_backend import ApiExecutionBackend
from claude_agent_mcp.backends.base import BackendCapabilities
from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend
from claude_agent_mcp.config import Config
from claude_agent_mcp.runtime.continuation_builder import (
    ContinuationContextBuilder,
    _RECONSTRUCTION_VERSION,
)
from claude_agent_mcp.types import (
    ContinuationRelevantWarning,
    ContinuationWindowPolicy,
    EventType,
    ForwardingContinuationSummary,
    ProfileName,
    SessionContinuationContext,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    WarningRelevance,
    WorkflowName,
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> Config:
    cfg = Config.__new__(Config)
    cfg.anthropic_api_key = kwargs.get("anthropic_api_key", "test-key")
    cfg.transport = "stdio"
    cfg.host = "127.0.0.1"
    cfg.port = 8000
    cfg.log_level = "WARNING"
    cfg.model = "claude-sonnet-4-6"
    cfg.state_dir = Path("/tmp/test_state")
    cfg.db_path = cfg.state_dir / "test.db"
    cfg.artifacts_dir = cfg.state_dir / "artifacts"
    cfg.allowed_dirs = [str(Path.cwd())]
    cfg.lock_ttl_seconds = 60
    cfg.max_artifact_bytes = 10 * 1024 * 1024
    cfg.execution_backend = kwargs.get("execution_backend", "api")
    cfg.claude_code_cli_path = kwargs.get("claude_code_cli_path", "")
    cfg.claude_code_timeout_seconds = kwargs.get("claude_code_timeout_seconds", 300)
    cfg.claude_code_enable_limited_tool_forwarding = kwargs.get(
        "claude_code_enable_limited_tool_forwarding", False
    )
    # v0.7.0 continuation window policy fields
    cfg.claude_code_max_continuation_turns = kwargs.get("claude_code_max_continuation_turns", 5)
    cfg.claude_code_max_continuation_warnings = kwargs.get(
        "claude_code_max_continuation_warnings", 3
    )
    cfg.claude_code_max_continuation_forwarding_events = kwargs.get(
        "claude_code_max_continuation_forwarding_events", 3
    )
    cfg.claude_code_include_verification_context = kwargs.get(
        "claude_code_include_verification_context", True
    )
    cfg.claude_code_include_tool_downgrade_context = kwargs.get(
        "claude_code_include_tool_downgrade_context", True
    )
    return cfg


def _make_session(
    session_id: str = "sess_abc",
    summary: str | None = "Session summary",
    working_directory: str | None = "/tmp/workspace",
    turn_count: int = 2,
    profile: ProfileName = ProfileName.general,
) -> SessionRecord:
    now = datetime.now(timezone.utc)
    return SessionRecord(
        session_id=session_id,
        workflow=WorkflowName.run_task,
        profile=profile,
        provider="claude_code",
        status=SessionStatus.completed,
        working_directory=working_directory,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        request_count=1,
        turn_count=turn_count,
        artifact_count=0,
        summary_latest=summary,
    )


def _make_event(
    event_type: EventType,
    payload: dict,
    session_id: str = "sess_abc",
    turn_index: int = 0,
) -> SessionEventRecord:
    return SessionEventRecord(
        session_id=session_id,
        event_type=event_type,
        turn_index=turn_index,
        payload=payload,
        created_at=datetime.now(timezone.utc),
    )


def _minimal_events() -> list[SessionEventRecord]:
    return [
        _make_event(EventType.user_input, {"task": "First task"}, turn_index=0),
        _make_event(
            EventType.provider_response_summary,
            {"summary": "First response", "stop_reason": "backend_defaulted"},
            turn_index=1,
        ),
        _make_event(EventType.user_input, {"message": "Follow-up"}, turn_index=2),
        _make_event(
            EventType.provider_response_summary,
            {"summary": "Second response", "stop_reason": "backend_defaulted"},
            turn_index=3,
        ),
    ]


# ---------------------------------------------------------------------------
# ContinuationWindowPolicy: config-based construction
# ---------------------------------------------------------------------------


class TestBuildPolicy:
    def test_defaults_from_config(self):
        cfg = _make_config()
        policy = ContinuationContextBuilder.build_policy(cfg)
        assert policy.max_recent_turns == 5
        assert policy.max_warnings == 3
        assert policy.max_forwarding_events == 3
        assert policy.include_verification_context is True
        assert policy.include_tool_downgrade_context is True

    def test_custom_values_from_config(self):
        cfg = _make_config(
            claude_code_max_continuation_turns=2,
            claude_code_max_continuation_warnings=1,
            claude_code_max_continuation_forwarding_events=1,
            claude_code_include_verification_context=False,
            claude_code_include_tool_downgrade_context=False,
        )
        policy = ContinuationContextBuilder.build_policy(cfg)
        assert policy.max_recent_turns == 2
        assert policy.max_warnings == 1
        assert policy.include_verification_context is False
        assert policy.include_tool_downgrade_context is False

    def test_graceful_fallback_when_attrs_missing(self):
        """build_policy should not raise if new config attrs absent (old configs)."""
        cfg = Config.__new__(Config)
        # Simulate an old config object that doesn't have v0.7.0 fields
        policy = ContinuationContextBuilder.build_policy(cfg)
        # Should return model defaults
        assert isinstance(policy, ContinuationWindowPolicy)
        assert policy.max_recent_turns == 5


# ---------------------------------------------------------------------------
# ContinuationContextBuilder: build_context
# ---------------------------------------------------------------------------


class TestBuildContext:
    def test_returns_session_continuation_context(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert isinstance(ctx, SessionContinuationContext)

    def test_is_continuation_flag_set(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.is_continuation is True

    def test_session_id_matches(self):
        session = _make_session(session_id="sess_xyz")
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.session_id == "sess_xyz"

    def test_session_summary_carried_forward(self):
        session = _make_session(summary="Important session summary")
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.session_summary == "Important session summary"

    def test_null_session_summary_preserved(self):
        session = _make_session(summary=None)
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.session_summary is None

    def test_recent_user_requests_extracted(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert "First task" in ctx.recent_user_requests
        assert "Follow-up" in ctx.recent_user_requests

    def test_recent_agent_outputs_extracted(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert "First response" in ctx.recent_agent_outputs
        assert "Second response" in ctx.recent_agent_outputs

    def test_reconstruction_version_set(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.reconstruction_version == _RECONSTRUCTION_VERSION

    def test_render_stats_present(self):
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.render_stats is not None

    def test_active_constraints_contain_working_directory(self):
        session = _make_session(working_directory="/home/user/project")
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.active_constraints.get("working_directory") == "/home/user/project"

    def test_active_constraints_contain_profile(self):
        session = _make_session(profile=ProfileName.general)
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.active_constraints.get("profile") == "general"

    def test_active_constraints_empty_when_no_working_dir(self):
        session = _make_session(working_directory=None)
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert "working_directory" not in ctx.active_constraints

    def test_deterministic_for_identical_inputs(self):
        """Identical session state must produce identical context."""
        session = _make_session()
        events = _minimal_events()
        policy = ContinuationWindowPolicy()
        ctx1 = ContinuationContextBuilder.build_context(session, events, policy)
        ctx2 = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx1.recent_user_requests == ctx2.recent_user_requests
        assert ctx1.recent_agent_outputs == ctx2.recent_agent_outputs
        assert ctx1.reconstruction_version == ctx2.reconstruction_version
        assert ctx1.session_summary == ctx2.session_summary


# ---------------------------------------------------------------------------
# Truncation behavior under configured limits
# ---------------------------------------------------------------------------


class TestTruncationBehavior:
    def _make_many_events(self, n_pairs: int) -> list[SessionEventRecord]:
        events: list[SessionEventRecord] = []
        for i in range(n_pairs):
            events.append(
                _make_event(
                    EventType.user_input,
                    {"task": f"Request {i}"},
                    turn_index=i * 2,
                )
            )
            events.append(
                _make_event(
                    EventType.provider_response_summary,
                    {"summary": f"Response {i}", "stop_reason": "backend_defaulted"},
                    turn_index=i * 2 + 1,
                )
            )
        return events

    def test_turns_truncated_to_max_recent_turns(self):
        session = _make_session(turn_count=10)
        events = self._make_many_events(10)
        policy = ContinuationWindowPolicy(max_recent_turns=3)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert len(ctx.recent_user_requests) <= 3
        assert len(ctx.recent_agent_outputs) <= 3

    def test_most_recent_turns_kept(self):
        session = _make_session(turn_count=8)
        events = self._make_many_events(8)
        policy = ContinuationWindowPolicy(max_recent_turns=2)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        # Most recent requests should be at end
        assert "Request 7" in ctx.recent_user_requests
        assert "Request 6" in ctx.recent_user_requests

    def test_turns_omitted_count_accurate(self):
        session = _make_session(turn_count=10)
        events = self._make_many_events(10)
        policy = ContinuationWindowPolicy(max_recent_turns=3)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        stats = ctx.render_stats
        assert stats is not None
        assert stats.turns_included == 3
        assert stats.turns_omitted == 7

    def test_no_truncation_when_under_limit(self):
        session = _make_session(turn_count=2)
        events = _minimal_events()
        policy = ContinuationWindowPolicy(max_recent_turns=5)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.render_stats is not None
        assert ctx.render_stats.turns_omitted == 0

    def test_empty_events_produces_empty_context(self):
        session = _make_session()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, [], policy)
        assert ctx.recent_user_requests == []
        assert ctx.recent_agent_outputs == []
        assert ctx.forwarding_history is None


# ---------------------------------------------------------------------------
# Warning classification and filtering
# ---------------------------------------------------------------------------


class TestWarningFiltering:
    def test_tool_downgrade_warning_is_continuation_relevant(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": ["tool_a", "tool_b"],
                    "forwarded": 1,
                    "dropped": 1,
                    "dropped_names": ["tool_b"],
                    "forwarding_mode": "limited_text_injection",
                },
            )
        ]
        policy = ContinuationWindowPolicy(include_tool_downgrade_context=True)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert len(ctx.relevant_warnings) > 0
        w = ctx.relevant_warnings[0]
        assert w.relevance == WarningRelevance.continuation_relevant
        assert w.source == "tool_downgrade"
        assert "tool_b" in w.message

    def test_tool_downgrade_warning_excluded_when_policy_disabled(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": ["tool_a"],
                    "forwarded": 0,
                    "dropped": 1,
                    "dropped_names": ["tool_a"],
                    "forwarding_mode": "limited_text_injection",
                },
            )
        ]
        policy = ContinuationWindowPolicy(include_tool_downgrade_context=False)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        # No tool downgrade warnings should appear when policy disabled
        tool_warnings = [
            w for w in ctx.relevant_warnings if w.source == "tool_downgrade"
        ]
        assert len(tool_warnings) == 0

    def test_verification_context_warning_when_policy_enabled(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.workflow_normalization,
                {"verdict": "pass"},
            )
        ]
        policy = ContinuationWindowPolicy(include_verification_context=True)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        ver_warnings = [w for w in ctx.relevant_warnings if w.source == "verification_context"]
        assert len(ver_warnings) > 0
        assert "pass" in ver_warnings[0].message

    def test_verification_context_excluded_when_policy_disabled(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.workflow_normalization,
                {"verdict": "pass"},
            )
        ]
        policy = ContinuationWindowPolicy(include_verification_context=False)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        ver_warnings = [w for w in ctx.relevant_warnings if w.source == "verification_context"]
        assert len(ver_warnings) == 0

    def test_warnings_truncated_to_max_warnings(self):
        session = _make_session()
        # Create many tool events each producing a warning
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": [f"tool_{i}"],
                    "forwarded": 0,
                    "dropped": 1,
                    "dropped_names": [f"tool_{i}"],
                    "forwarding_mode": "limited_text_injection",
                },
            )
            for i in range(6)
        ]
        policy = ContinuationWindowPolicy(
            include_tool_downgrade_context=True,
            max_warnings=2,
        )
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert len(ctx.relevant_warnings) <= 2

    def test_warnings_omitted_count_accurate(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": [f"t{i}"],
                    "forwarded": 0,
                    "dropped": 1,
                    "dropped_names": [f"t{i}"],
                    "forwarding_mode": "limited_text_injection",
                },
            )
            for i in range(5)
        ]
        policy = ContinuationWindowPolicy(
            include_tool_downgrade_context=True,
            max_warnings=2,
        )
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        stats = ctx.render_stats
        assert stats is not None
        assert stats.warnings_omitted == 3
        assert stats.warnings_included == 2


# ---------------------------------------------------------------------------
# Forwarding continuity summarization
# ---------------------------------------------------------------------------


class TestForwardingContinuitySummarization:
    def test_no_forwarding_events_gives_none(self):
        session = _make_session()
        events = _minimal_events()  # No forwarding events
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.forwarding_history is None

    def test_forwarding_event_produces_summary(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": ["tool_a", "tool_b"],
                    "forwarded": 1,
                    "dropped": 1,
                    "dropped_names": ["tool_b"],
                    "forwarding_mode": "limited_text_injection",
                },
            )
        ]
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.forwarding_history is not None
        fwd = ctx.forwarding_history
        assert isinstance(fwd, ForwardingContinuationSummary)
        assert fwd.forwarding_mode == "limited_text_injection"
        assert "tool_b" in fwd.dropped_tool_names

    def test_forwarding_mode_inferred_when_forwarded_false(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": ["tool_a"],
                    "forwarded": False,
                    "reason": "backend does not support downstream tools",
                },
            )
        ]
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.forwarding_history is not None
        assert ctx.forwarding_history.forwarding_mode == "disabled"

    def test_forwarding_events_truncated_by_policy(self):
        session = _make_session()
        events = [
            _make_event(
                EventType.downstream_tool_catalog_resolved,
                {
                    "visible_tools": [f"tool_{i}"],
                    "forwarded": 1,
                    "dropped": 0,
                    "dropped_names": [],
                    "forwarding_mode": "limited_text_injection",
                },
            )
            for i in range(5)
        ]
        policy = ContinuationWindowPolicy(max_forwarding_events=2)
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        stats = ctx.render_stats
        assert stats is not None
        assert stats.forwarding_events_included == 2
        assert stats.forwarding_events_omitted == 3


# ---------------------------------------------------------------------------
# BackendCapabilities v0.7.0 flags
# ---------------------------------------------------------------------------


class TestBackendCapabilitiesV07:
    def test_claude_code_supports_structured_continuation_context(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend.capabilities.supports_structured_continuation_context is True

    def test_claude_code_supports_continuation_window_policy(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend.capabilities.supports_continuation_window_policy is True

    def test_api_backend_does_not_support_structured_continuation_context(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        # API backend has native multi-turn; structured continuation context is claude_code only
        assert backend.capabilities.supports_structured_continuation_context is False

    def test_api_backend_does_not_support_continuation_window_policy(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        assert backend.capabilities.supports_continuation_window_policy is False

    def test_new_flags_are_frozen_dataclass_fields(self):
        caps = BackendCapabilities(
            supports_structured_continuation_context=True,
            supports_continuation_window_policy=True,
        )
        assert caps.supports_structured_continuation_context is True
        assert caps.supports_continuation_window_policy is True

    def test_default_values_are_false(self):
        caps = BackendCapabilities()
        assert caps.supports_structured_continuation_context is False
        assert caps.supports_continuation_window_policy is False


# ---------------------------------------------------------------------------
# Cross-backend contract: both backends accept continuation_context kwarg
# ---------------------------------------------------------------------------


class TestCrossBackendContractV07:
    @pytest.mark.asyncio
    async def test_api_backend_accepts_continuation_context_kwarg(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        mock_result = MagicMock()
        mock_result.output_text = "result"
        mock_result.turn_count = 1
        mock_result.provider_session_id = None
        mock_result.stop_reason = "end_turn"
        mock_result.warnings = []

        with patch.object(backend._adapter, "run", new_callable=AsyncMock, return_value=mock_result):
            # Should not raise — continuation_context is accepted but ignored
            result = await backend.execute(
                system_prompt="system",
                task="do something",
                max_turns=5,
                is_continuation=True,
                continuation_context={"fake": "context"},
            )
            assert result is not None

    @pytest.mark.asyncio
    async def test_claude_code_backend_accepts_continuation_context_kwarg(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)

        ctx = SessionContinuationContext(
            session_id="sess_test",
            is_continuation=True,
            session_summary="Prior summary",
            recent_user_requests=["Hello"],
            recent_agent_outputs=["Hi there"],
        )

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(
            return_value=(b"Continuation response", b"")
        )

        with patch("claude_agent_mcp.backends.claude_code_backend.ClaudeCodeExecutionBackend._find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            result = await backend.execute(
                system_prompt="You are an assistant.",
                task="Continue working on this",
                max_turns=5,
                is_continuation=True,
                continuation_context=ctx,
            )
            assert result.output_text == "Continuation response"


# ---------------------------------------------------------------------------
# Structured continuation prompt rendering
# ---------------------------------------------------------------------------


class TestContinuationPromptRendering:
    """Tests that verify the structured prompt sections are rendered correctly."""

    def _make_backend(self) -> ClaudeCodeExecutionBackend:
        cfg = _make_config(execution_backend="claude_code")
        return ClaudeCodeExecutionBackend(cfg)

    def _make_ctx(self, **kwargs) -> SessionContinuationContext:
        defaults = {
            "session_id": "sess_test",
            "is_continuation": True,
            "session_summary": "This is a summary.",
            "recent_user_requests": ["Hello"],
            "recent_agent_outputs": ["Hi there"],
        }
        defaults.update(kwargs)
        return SessionContinuationContext(**defaults)

    def test_system_section_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx()
        prompt = backend._build_continuation_prompt(
            system_prompt="You are helpful.",
            task="Do the thing.",
            continuation_context=ctx,
        )
        assert "[System]" in prompt
        assert "You are helpful." in prompt

    def test_continuation_session_section_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(session_id="sess_abc")
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Continuation Session]" in prompt
        assert "sess_abc" in prompt

    def test_session_summary_section_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(session_summary="The session has been doing X.")
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Session Summary]" in prompt
        assert "The session has been doing X." in prompt

    def test_session_summary_section_omitted_when_empty(self):
        backend = self._make_backend()
        ctx = self._make_ctx(session_summary=None)
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Session Summary]" not in prompt

    def test_recent_interaction_state_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(
            recent_user_requests=["User request"],
            recent_agent_outputs=["Agent output"],
        )
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Recent Interaction State]" in prompt
        assert "User request" in prompt
        assert "Agent output" in prompt

    def test_recent_interaction_omitted_when_empty(self):
        backend = self._make_backend()
        ctx = self._make_ctx(recent_user_requests=[], recent_agent_outputs=[])
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Recent Interaction State]" not in prompt

    def test_relevant_warnings_section_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(
            relevant_warnings=[
                ContinuationRelevantWarning(
                    message="Tool X was dropped.",
                    relevance=WarningRelevance.continuation_relevant,
                    source="tool_downgrade",
                )
            ]
        )
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Relevant Warnings]" in prompt
        assert "Tool X was dropped." in prompt

    def test_relevant_warnings_omitted_when_empty(self):
        backend = self._make_backend()
        ctx = self._make_ctx(relevant_warnings=[])
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Relevant Warnings]" not in prompt

    def test_tool_forwarding_context_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(
            forwarding_history=ForwardingContinuationSummary(
                forwarding_mode="limited_text_injection",
                compatible_tool_names=["tool_a"],
                dropped_tool_names=["tool_b"],
            )
        )
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Tool Forwarding Context]" in prompt
        assert "limited_text_injection" in prompt
        assert "tool_a" in prompt
        assert "tool_b" in prompt

    def test_tool_forwarding_omitted_when_no_history(self):
        backend = self._make_backend()
        ctx = self._make_ctx(forwarding_history=None)
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Tool Forwarding Context]" not in prompt

    def test_active_constraints_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx(
            active_constraints={"working_directory": "/home/user", "profile": "general"}
        )
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Active Constraints]" in prompt
        assert "/home/user" in prompt

    def test_active_constraints_omitted_when_empty(self):
        backend = self._make_backend()
        ctx = self._make_ctx(active_constraints={})
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Active Constraints]" not in prompt

    def test_current_request_section_always_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx()
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Do the thing!",
            continuation_context=ctx,
        )
        assert "[Current Request]" in prompt
        assert "Do the thing!" in prompt

    def test_instructions_section_present(self):
        backend = self._make_backend()
        ctx = self._make_ctx()
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        assert "[Instructions]" in prompt
        assert "continuing this session" in prompt.lower()

    def test_section_ordering_deterministic(self):
        """Sections must appear in canonical order."""
        backend = self._make_backend()
        ctx = self._make_ctx(
            relevant_warnings=[
                ContinuationRelevantWarning(
                    message="W",
                    relevance=WarningRelevance.continuation_relevant,
                    source="tool_downgrade",
                )
            ],
            forwarding_history=ForwardingContinuationSummary(
                forwarding_mode="limited_text_injection",
            ),
            active_constraints={"profile": "general"},
        )
        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="Task",
            continuation_context=ctx,
        )
        positions = {
            section: prompt.index(section)
            for section in [
                "[System]",
                "[Continuation Session]",
                "[Session Summary]",
                "[Recent Interaction State]",
                "[Relevant Warnings]",
                "[Tool Forwarding Context]",
                "[Active Constraints]",
                "[Current Request]",
                "[Instructions]",
            ]
        }
        ordered_keys = sorted(positions, key=lambda k: positions[k])
        expected_order = [
            "[System]",
            "[Continuation Session]",
            "[Session Summary]",
            "[Recent Interaction State]",
            "[Relevant Warnings]",
            "[Tool Forwarding Context]",
            "[Active Constraints]",
            "[Current Request]",
            "[Instructions]",
        ]
        assert ordered_keys == expected_order

    def test_rendering_deterministic_for_identical_context(self):
        """Same context must produce identical prompt string."""
        backend = self._make_backend()
        ctx = self._make_ctx()
        p1 = backend._build_continuation_prompt("System", "Task", ctx)
        p2 = backend._build_continuation_prompt("System", "Task", ctx)
        assert p1 == p2


# ---------------------------------------------------------------------------
# Regression: v0.6 behavior preserved without continuation_context
# ---------------------------------------------------------------------------


class TestV06Regression:
    """Ensure v0.6 prompting behavior is unchanged when continuation_context is not provided."""

    def _make_backend(self) -> ClaudeCodeExecutionBackend:
        cfg = _make_config(execution_backend="claude_code")
        return ClaudeCodeExecutionBackend(cfg)

    def test_initial_prompt_uses_session_context_header(self):
        backend = self._make_backend()
        prompt, truncated = backend._build_structured_prompt(
            system_prompt="System",
            task="Task",
            conversation_history=None,
            session_summary="A summary",
            is_continuation=False,
        )
        assert "[Session Context]" in prompt
        assert "[Continuation Session]" not in prompt

    def test_continuation_without_context_uses_continuation_session_header(self):
        backend = self._make_backend()
        prompt, truncated = backend._build_structured_prompt(
            system_prompt="System",
            task="Task",
            conversation_history=None,
            session_summary="A summary",
            is_continuation=True,
        )
        assert "[Continuation Session]" in prompt

    def test_execute_without_continuation_context_uses_old_path(self):
        """When continuation_context is None, _build_structured_prompt is used."""
        backend = self._make_backend()
        calls: list[str] = []

        original = backend._build_structured_prompt

        def patched(*args, **kwargs):
            calls.append("structured")
            return original(*args, **kwargs)

        backend._build_structured_prompt = patched  # type: ignore

        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Result", b""))

        import asyncio

        with patch(
            "claude_agent_mcp.backends.claude_code_backend.ClaudeCodeExecutionBackend._find_cli",
            return_value="/usr/bin/claude",
        ), patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
            asyncio.get_event_loop().run_until_complete(
                backend.execute(
                    system_prompt="System",
                    task="Task",
                    max_turns=5,
                    is_continuation=True,
                    continuation_context=None,  # Explicitly None → old path
                )
            )

        assert "structured" in calls

    def test_new_capability_flags_do_not_break_old_tests(self):
        """BackendCapabilities with new v0.7.0 flags still works as a frozen dataclass."""
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        caps = backend.capabilities
        # Old flags still present
        assert caps.supports_downstream_tools is False
        assert caps.supports_workspace_assumptions is True
        assert caps.supports_limited_downstream_tools is True
        # New flags
        assert caps.supports_structured_continuation_context is True
        assert caps.supports_continuation_window_policy is True


# ---------------------------------------------------------------------------
# EventType: new continuation event types
# ---------------------------------------------------------------------------


class TestNewEventTypes:
    def test_session_continuation_context_built_exists(self):
        assert hasattr(EventType, "session_continuation_context_built")
        assert EventType.session_continuation_context_built.value == "session_continuation_context_built"

    def test_session_continuation_context_truncated_exists(self):
        assert hasattr(EventType, "session_continuation_context_truncated")

    def test_session_continuation_prompt_rendered_exists(self):
        assert hasattr(EventType, "session_continuation_prompt_rendered")
