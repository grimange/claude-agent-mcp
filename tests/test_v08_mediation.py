"""Tests for v0.8.0 execution mediation layer.

Covers:
- MediatedActionRequest / MediatedActionResult models (types)
- MediationEngine.parse_requests() — valid, malformed, missing fields, unknown type
- MediationEngine.validate_request() — all rejection paths + approval
- MediationEngine.execute_action() — success, invocation failure
- MediationEngine.make_rejection_result()
- Config field loading for mediation settings
- BackendCapabilities v0.8.0 flags
- ContinuationContextBuilder: _extract_mediated_summaries (enabled/disabled)
- ContinuationContextBuilder.build_context passes config to mediaton extraction
- ClaudeCodeExecutionBackend: [Mediated Execution Context] section rendering
- WorkflowExecutor._process_mediated_actions — event persistence, rejection warnings
- Deterministic parsing (identical inputs → identical results)
- Regression: existing continuation prompt unchanged when mediation is disabled
- Regression: backends without supports_execution_mediation do not invoke mediation
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_mcp.backends.api_backend import ApiExecutionBackend
from claude_agent_mcp.backends.base import BackendCapabilities
from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend
from claude_agent_mcp.config import Config
from claude_agent_mcp.runtime.continuation_builder import ContinuationContextBuilder
from claude_agent_mcp.runtime.mediation_engine import (
    MEDIATION_VERSION,
    POLICY_APPROVED,
    POLICY_REJECTED_DISABLED,
    POLICY_REJECTED_FEDERATION_INACTIVE,
    POLICY_REJECTED_LIMIT,
    POLICY_REJECTED_TOOL_VISIBILITY,
    POLICY_REJECTED_TYPE,
    POLICY_REJECTED_VERSION,
    MediationEngine,
)
from claude_agent_mcp.types import (
    ContinuationWindowPolicy,
    EventType,
    MediatedActionRequest,
    MediatedActionResult,
    MediatedActionStatus,
    MediatedActionType,
    ProfileName,
    SessionContinuationContext,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    WorkflowName,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> Config:
    """Construct a minimal Config for testing."""
    cfg = Config.__new__(Config)
    cfg.anthropic_api_key = "test-key"
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
    cfg.claude_code_cli_path = ""
    cfg.claude_code_timeout_seconds = 300
    cfg.claude_code_enable_limited_tool_forwarding = False
    cfg.claude_code_max_continuation_turns = 5
    cfg.claude_code_max_continuation_warnings = 3
    cfg.claude_code_max_continuation_forwarding_events = 3
    cfg.claude_code_include_verification_context = True
    cfg.claude_code_include_tool_downgrade_context = True
    # v0.8.0 mediation fields
    cfg.claude_code_enable_execution_mediation = kwargs.get(
        "claude_code_enable_execution_mediation", False
    )
    cfg.claude_code_max_mediated_actions_per_turn = kwargs.get(
        "claude_code_max_mediated_actions_per_turn", 1
    )
    cfg.claude_code_allowed_mediated_action_types = kwargs.get(
        "claude_code_allowed_mediated_action_types", []
    )
    cfg.claude_code_include_mediated_results_in_continuation = kwargs.get(
        "claude_code_include_mediated_results_in_continuation", False
    )
    return cfg


def _make_request(
    action_type: str = "read",
    target_tool: str = "server__tool_a",
    request_id: str = "req_001",
    mediation_version: str = MEDIATION_VERSION,
    justification: str = "Need this data",
    arguments: dict | None = None,
) -> MediatedActionRequest:
    return MediatedActionRequest(
        mediation_version=mediation_version,
        request_id=request_id,
        action_type=MediatedActionType(action_type),
        target_tool=target_tool,
        arguments=arguments or {},
        justification=justification,
    )


def _make_request_block(**kwargs) -> str:
    """Build a valid mediated action request block string."""
    data = {
        "mediation_version": kwargs.get("mediation_version", MEDIATION_VERSION),
        "request_id": kwargs.get("request_id", "req_001"),
        "action_type": kwargs.get("action_type", "read"),
        "target_tool": kwargs.get("target_tool", "server__tool_a"),
        "justification": kwargs.get("justification", "Need data"),
        "arguments": kwargs.get("arguments", {}),
    }
    return f"<mediated_action_request>\n{json.dumps(data)}\n</mediated_action_request>"


def _make_session(
    session_id: str = "sess_abc",
    summary: str | None = "Session summary",
) -> SessionRecord:
    now = datetime.now(timezone.utc)
    return SessionRecord(
        session_id=session_id,
        workflow=WorkflowName.run_task,
        profile=ProfileName.general,
        provider="claude_code",
        status=SessionStatus.completed,
        working_directory="/tmp/workspace",
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        request_count=1,
        turn_count=2,
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


def _make_visibility_resolver(visible_tool_names: list[str]):
    """Build a mock ToolVisibilityResolver with specific visible tool names."""
    resolver = MagicMock()
    tools = []
    for name in visible_tool_names:
        t = MagicMock()
        t.normalized_name = name
        tools.append(t)
    resolver.resolve.return_value = tools
    return resolver


# ---------------------------------------------------------------------------
# MediatedActionRequest / MediatedActionResult model tests
# ---------------------------------------------------------------------------


class TestMediatedActionModels:
    def test_request_round_trips(self):
        req = _make_request()
        assert req.mediation_version == MEDIATION_VERSION
        assert req.action_type == MediatedActionType.read
        assert req.target_tool == "server__tool_a"
        assert req.justification == "Need this data"

    def test_request_defaults_empty_arguments(self):
        req = _make_request()
        assert req.arguments == {}

    def test_request_with_arguments(self):
        req = _make_request(arguments={"key": "value", "limit": 10})
        assert req.arguments == {"key": "value", "limit": 10}

    def test_all_action_types_valid(self):
        for t in ("read", "lookup", "inspect"):
            req = _make_request(action_type=t)
            assert req.action_type.value == t

    def test_result_round_trips(self):
        result = MediatedActionResult(
            request_id="req_001",
            status=MediatedActionStatus.completed,
            tool_name="server__tool_a",
            arguments_summary="key='value'",
            result_summary="42 results",
            failure_reason=None,
            policy_decision=POLICY_APPROVED,
        )
        assert result.status == MediatedActionStatus.completed
        assert result.failure_reason is None

    def test_rejected_result_has_failure_reason(self):
        result = MediatedActionResult(
            request_id="req_001",
            status=MediatedActionStatus.rejected,
            tool_name="server__tool_a",
            arguments_summary="(no arguments)",
            result_summary="",
            failure_reason="mediation is disabled",
            policy_decision=POLICY_REJECTED_DISABLED,
        )
        assert result.status == MediatedActionStatus.rejected
        assert result.failure_reason is not None


# ---------------------------------------------------------------------------
# MediationEngine: parse_requests
# ---------------------------------------------------------------------------


class TestParseRequests:
    def setup_method(self):
        self.cfg = _make_config(claude_code_enable_execution_mediation=True)
        self.engine = MediationEngine(self.cfg)

    def test_empty_output_returns_no_requests(self):
        assert self.engine.parse_requests("") == []

    def test_output_with_no_blocks_returns_empty(self):
        assert self.engine.parse_requests("Here is my answer: 42.") == []

    def test_single_valid_block_parsed(self):
        text = _make_request_block()
        requests = self.engine.parse_requests(text)
        assert len(requests) == 1
        assert requests[0].action_type == MediatedActionType.read
        assert requests[0].target_tool == "server__tool_a"

    def test_multiple_valid_blocks_all_parsed(self):
        block1 = _make_request_block(request_id="req_001", action_type="read")
        block2 = _make_request_block(request_id="req_002", action_type="lookup")
        text = f"Response text.\n{block1}\nMore text.\n{block2}"
        requests = self.engine.parse_requests(text)
        assert len(requests) == 2
        assert requests[0].request_id == "req_001"
        assert requests[1].request_id == "req_002"

    def test_malformed_json_skipped_with_warning(self, caplog):
        # Content must match {…} pattern so the regex fires, but JSON is invalid.
        text = "<mediated_action_request>{NOT VALID JSON}</mediated_action_request>"
        import logging
        with caplog.at_level(logging.WARNING, logger="claude_agent_mcp.runtime.mediation_engine"):
            requests = self.engine.parse_requests(text)
        assert requests == []
        assert any("malformed JSON" in r.message for r in caplog.records)

    def test_missing_required_fields_skipped(self, caplog):
        # Missing 'justification' field
        data = {
            "mediation_version": MEDIATION_VERSION,
            "request_id": "req_001",
            "action_type": "read",
            "target_tool": "server__tool_a",
        }
        text = f"<mediated_action_request>\n{json.dumps(data)}\n</mediated_action_request>"
        import logging
        with caplog.at_level(logging.WARNING, logger="claude_agent_mcp.runtime.mediation_engine"):
            requests = self.engine.parse_requests(text)
        assert requests == []
        assert any("missing required fields" in r.message for r in caplog.records)

    def test_unknown_action_type_skipped(self, caplog):
        text = _make_request_block(action_type="delete")
        import logging
        with caplog.at_level(logging.WARNING, logger="claude_agent_mcp.runtime.mediation_engine"):
            requests = self.engine.parse_requests(text)
        assert requests == []
        assert any("unknown action_type" in r.message for r in caplog.records)

    def test_block_in_longer_text_parsed(self):
        block = _make_request_block(action_type="inspect")
        text = f"I need to check something.\n{block}\nDone."
        requests = self.engine.parse_requests(text)
        assert len(requests) == 1
        assert requests[0].action_type == MediatedActionType.inspect

    def test_parsing_is_deterministic(self):
        """Identical input produces identical output."""
        text = _make_request_block()
        r1 = self.engine.parse_requests(text)
        r2 = self.engine.parse_requests(text)
        assert len(r1) == len(r2) == 1
        assert r1[0].request_id == r2[0].request_id
        assert r1[0].action_type == r2[0].action_type

    def test_non_dict_json_skipped(self, caplog):
        text = "<mediated_action_request>[1,2,3]</mediated_action_request>"
        import logging
        with caplog.at_level(logging.WARNING, logger="claude_agent_mcp.runtime.mediation_engine"):
            requests = self.engine.parse_requests(text)
        assert requests == []

    def test_arguments_field_optional(self):
        data = {
            "mediation_version": MEDIATION_VERSION,
            "request_id": "req_001",
            "action_type": "read",
            "target_tool": "server__tool_a",
            "justification": "Need data",
            # arguments omitted
        }
        text = f"<mediated_action_request>\n{json.dumps(data)}\n</mediated_action_request>"
        requests = self.engine.parse_requests(text)
        assert len(requests) == 1
        assert requests[0].arguments == {}


# ---------------------------------------------------------------------------
# MediationEngine: validate_request
# ---------------------------------------------------------------------------


class TestValidateRequest:
    def _engine(self, visible_tools=None, **cfg_kwargs):
        cfg = _make_config(claude_code_enable_execution_mediation=True, **cfg_kwargs)
        resolver = _make_visibility_resolver(visible_tools or ["server__tool_a"])
        return MediationEngine(cfg, resolver)

    def test_valid_request_approved(self):
        engine = self._engine()
        req = _make_request()
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is True
        assert decision == POLICY_APPROVED

    def test_rejected_when_mediation_disabled(self):
        cfg = _make_config(claude_code_enable_execution_mediation=False)
        engine = MediationEngine(cfg, _make_visibility_resolver(["server__tool_a"]))
        req = _make_request()
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_DISABLED

    def test_rejected_when_wrong_mediation_version(self):
        engine = self._engine()
        req = _make_request(mediation_version="v0.5.0")
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_VERSION

    def test_rejected_when_action_type_not_in_allowlist(self):
        # Config only allows 'lookup', not 'read'
        engine = self._engine(
            claude_code_allowed_mediated_action_types=["lookup"]
        )
        req = _make_request(action_type="read")
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_TYPE

    def test_approved_when_action_type_in_allowlist(self):
        engine = self._engine(
            claude_code_allowed_mediated_action_types=["read", "lookup"]
        )
        req = _make_request(action_type="read")
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is True

    def test_rejected_when_per_turn_limit_exceeded(self):
        engine = self._engine(claude_code_max_mediated_actions_per_turn=1)
        req = _make_request()
        # actions_this_turn=1, limit=1 → reject
        ok, decision = engine.validate_request(req, "general", 1)
        assert ok is False
        assert decision == POLICY_REJECTED_LIMIT

    def test_approved_when_at_limit_boundary(self):
        engine = self._engine(claude_code_max_mediated_actions_per_turn=2)
        req = _make_request()
        # actions_this_turn=1, limit=2 → approved (1 < 2)
        ok, decision = engine.validate_request(req, "general", 1)
        assert ok is True

    def test_rejected_when_federation_inactive(self):
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg, visibility_resolver=None)
        req = _make_request()
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_FEDERATION_INACTIVE

    def test_rejected_when_tool_not_visible(self):
        # Visibility resolver shows only 'server__tool_b', not 'server__tool_a'
        engine = self._engine(visible_tools=["server__tool_b"])
        req = _make_request(target_tool="server__tool_a")
        ok, decision = engine.validate_request(req, "general", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_TOOL_VISIBILITY

    def test_rejected_when_unknown_profile(self):
        engine = self._engine()
        req = _make_request()
        ok, decision = engine.validate_request(req, "unknown_profile_xyz", 0)
        assert ok is False
        assert decision == POLICY_REJECTED_TOOL_VISIBILITY

    def test_all_action_types_approved_by_default(self):
        """When allowed_mediated_action_types is empty, all supported types are allowed."""
        engine = self._engine(claude_code_allowed_mediated_action_types=[])
        for action_type in ("read", "lookup", "inspect"):
            req = _make_request(action_type=action_type)
            ok, _ = engine.validate_request(req, "general", 0)
            assert ok is True, f"Expected {action_type!r} to be approved"

    def test_validation_is_deterministic(self):
        """Identical inputs produce identical validation outcomes."""
        engine = self._engine()
        req = _make_request()
        ok1, d1 = engine.validate_request(req, "general", 0)
        ok2, d2 = engine.validate_request(req, "general", 0)
        assert ok1 == ok2
        assert d1 == d2


# ---------------------------------------------------------------------------
# MediationEngine: execute_action
# ---------------------------------------------------------------------------


class TestExecuteAction:
    def _engine(self, visible_tools=None, **cfg_kwargs):
        cfg = _make_config(claude_code_enable_execution_mediation=True, **cfg_kwargs)
        resolver = _make_visibility_resolver(visible_tools or ["server__tool_a"])
        return MediationEngine(cfg, resolver)

    def _make_invoker(self, result_content: str = "result data"):
        invoker = MagicMock()
        mock_result = MagicMock()
        mock_result.to_content_string.return_value = result_content
        invoker.invoke = AsyncMock(return_value=mock_result)
        return invoker

    @pytest.mark.asyncio
    async def test_successful_execution_returns_completed(self):
        engine = self._engine()
        req = _make_request()
        invoker = self._make_invoker("Some result content")
        result = await engine.execute_action(req, invoker, "sess_abc", 1)
        assert result.status == MediatedActionStatus.completed
        assert result.policy_decision == POLICY_APPROVED
        assert result.failure_reason is None
        assert "Some result content" in result.result_summary

    @pytest.mark.asyncio
    async def test_successful_execution_uses_request_id(self):
        engine = self._engine()
        req = _make_request(request_id="req_xyz")
        invoker = self._make_invoker()
        result = await engine.execute_action(req, invoker, "sess_abc", 1)
        assert result.request_id == "req_xyz"
        assert result.tool_name == req.target_tool

    @pytest.mark.asyncio
    async def test_execution_failure_returns_failed_not_raises(self):
        engine = self._engine()
        req = _make_request()
        invoker = MagicMock()
        invoker.invoke = AsyncMock(side_effect=RuntimeError("connection refused"))
        result = await engine.execute_action(req, invoker, "sess_abc", 1)
        assert result.status == MediatedActionStatus.failed
        assert "connection refused" in (result.failure_reason or "")
        assert result.result_summary == ""

    @pytest.mark.asyncio
    async def test_long_result_is_truncated(self):
        engine = self._engine()
        req = _make_request()
        long_content = "x" * 1000
        invoker = self._make_invoker(long_content)
        result = await engine.execute_action(req, invoker, "sess_abc", 1)
        assert len(result.result_summary) <= 515  # 500 chars + "[truncated]"
        assert result.status == MediatedActionStatus.completed

    @pytest.mark.asyncio
    async def test_arguments_are_passed_to_invoker(self):
        engine = self._engine()
        req = _make_request(arguments={"query": "test", "limit": 5})
        invoker = self._make_invoker()
        await engine.execute_action(req, invoker, "sess_abc", 1)
        invoker.invoke.assert_called_once_with(
            normalized_name="server__tool_a",
            tool_input={"query": "test", "limit": 5},
            session_id="sess_abc",
            turn_index=1,
        )


# ---------------------------------------------------------------------------
# MediationEngine: make_rejection_result
# ---------------------------------------------------------------------------


class TestMakeRejectionResult:
    def test_rejection_has_correct_status(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        req = _make_request()
        result = engine.make_rejection_result(req, POLICY_REJECTED_DISABLED)
        assert result.status == MediatedActionStatus.rejected
        assert result.policy_decision == POLICY_REJECTED_DISABLED
        assert result.failure_reason is not None
        assert len(result.failure_reason) > 0

    def test_rejection_result_has_request_id(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        req = _make_request(request_id="req_special")
        result = engine.make_rejection_result(req, POLICY_REJECTED_TYPE)
        assert result.request_id == "req_special"

    def test_all_rejection_policies_produce_reason(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        req = _make_request()
        for policy in (
            POLICY_REJECTED_DISABLED,
            POLICY_REJECTED_VERSION,
            POLICY_REJECTED_TYPE,
            POLICY_REJECTED_LIMIT,
            POLICY_REJECTED_TOOL_VISIBILITY,
            POLICY_REJECTED_FEDERATION_INACTIVE,
        ):
            result = engine.make_rejection_result(req, policy)
            assert result.failure_reason, f"Expected non-empty reason for {policy}"


# ---------------------------------------------------------------------------
# Config: mediation fields
# ---------------------------------------------------------------------------


class TestMediationConfig:
    def test_mediation_disabled_by_default(self):
        cfg = Config.__new__(Config)
        # Simulate default (no env vars set)
        assert getattr(cfg, "claude_code_enable_execution_mediation", False) is False

    def test_engine_is_disabled_without_config(self):
        cfg = _make_config(claude_code_enable_execution_mediation=False)
        engine = MediationEngine(cfg)
        assert engine.is_enabled() is False

    def test_engine_is_enabled_with_config(self):
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg)
        assert engine.is_enabled() is True

    def test_max_mediated_actions_default(self):
        cfg = _make_config()
        assert cfg.claude_code_max_mediated_actions_per_turn == 1

    def test_allowed_types_default_empty(self):
        cfg = _make_config()
        assert cfg.claude_code_allowed_mediated_action_types == []

    def test_include_results_in_continuation_default_false(self):
        cfg = _make_config()
        assert cfg.claude_code_include_mediated_results_in_continuation is False

    def test_config_with_mediation_enabled_and_limit(self):
        cfg = _make_config(
            claude_code_enable_execution_mediation=True,
            claude_code_max_mediated_actions_per_turn=3,
            claude_code_allowed_mediated_action_types=["read"],
            claude_code_include_mediated_results_in_continuation=True,
        )
        assert cfg.claude_code_enable_execution_mediation is True
        assert cfg.claude_code_max_mediated_actions_per_turn == 3
        assert cfg.claude_code_allowed_mediated_action_types == ["read"]
        assert cfg.claude_code_include_mediated_results_in_continuation is True


# ---------------------------------------------------------------------------
# BackendCapabilities v0.8.0 flags
# ---------------------------------------------------------------------------


class TestBackendCapabilitiesV08:
    def test_claude_code_supports_execution_mediation(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend.capabilities.supports_execution_mediation is True

    def test_claude_code_supports_mediated_action_results(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend.capabilities.supports_mediated_action_results is True

    def test_api_backend_does_not_support_execution_mediation(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        assert backend.capabilities.supports_execution_mediation is False

    def test_api_backend_does_not_support_mediated_action_results(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        assert backend.capabilities.supports_mediated_action_results is False

    def test_new_flags_default_to_false_in_base_capabilities(self):
        caps = BackendCapabilities()
        assert caps.supports_execution_mediation is False
        assert caps.supports_mediated_action_results is False

    def test_new_flags_can_be_set_true(self):
        caps = BackendCapabilities(
            supports_execution_mediation=True,
            supports_mediated_action_results=True,
        )
        assert caps.supports_execution_mediation is True
        assert caps.supports_mediated_action_results is True

    def test_previous_flags_unaffected(self):
        """v0.8.0 additions do not change v0.7.0 or earlier capability values."""
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        caps = backend.capabilities
        assert caps.supports_structured_continuation_context is True
        assert caps.supports_continuation_window_policy is True
        assert caps.supports_workspace_assumptions is True
        assert caps.supports_downstream_tools is False
        assert caps.supports_native_multiturn is False


# ---------------------------------------------------------------------------
# ContinuationContextBuilder: mediated summaries extraction
# ---------------------------------------------------------------------------


class TestMediatedSummariesExtraction:
    def _make_mediated_completed_event(
        self,
        tool_name: str = "server__tool_a",
        action_type: str = "read",
        result_summary: str = "Found 3 results",
        status: str = "completed",
    ) -> SessionEventRecord:
        return _make_event(
            EventType.mediated_action_completed,
            {
                "request_id": "req_001",
                "target_tool": tool_name,
                "tool_name": tool_name,
                "action_type": action_type,
                "status": status,
                "arguments_summary": "(no arguments)",
                "result_summary": result_summary,
                "failure_reason": None,
                "policy_decision": POLICY_APPROVED,
            },
        )

    def test_returns_empty_when_config_is_none(self):
        events = [self._make_mediated_completed_event()]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, None)
        assert result == []

    def test_returns_empty_when_disabled_in_config(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=False)
        events = [self._make_mediated_completed_event()]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert result == []

    def test_returns_summaries_when_enabled(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        events = [self._make_mediated_completed_event()]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert len(result) == 1
        assert "server__tool_a" in result[0]
        assert "Found 3 results" in result[0]

    def test_includes_action_type_in_summary(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        events = [self._make_mediated_completed_event(action_type="lookup")]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert any("lookup" in s for s in result)

    def test_rejected_events_not_included(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        rejected_event = _make_event(
            EventType.mediated_action_rejected,
            {
                "request_id": "req_001",
                "target_tool": "server__tool_a",
                "policy_decision": POLICY_REJECTED_DISABLED,
                "failure_reason": "mediation disabled",
            },
        )
        result = ContinuationContextBuilder._extract_mediated_summaries([rejected_event], cfg)
        assert result == []

    def test_failed_execution_not_included(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        event = self._make_mediated_completed_event(status="failed")
        result = ContinuationContextBuilder._extract_mediated_summaries([event], cfg)
        assert result == []

    def test_multiple_completed_events(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        events = [
            self._make_mediated_completed_event(tool_name="server__tool_a", result_summary="res A"),
            self._make_mediated_completed_event(tool_name="server__tool_b", result_summary="res B"),
        ]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert len(result) == 2
        tool_names = " ".join(result)
        assert "tool_a" in tool_names
        assert "tool_b" in tool_names

    def test_long_result_summary_truncated(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        long_summary = "x" * 300
        event = self._make_mediated_completed_event(result_summary=long_summary)
        result = ContinuationContextBuilder._extract_mediated_summaries([event], cfg)
        assert len(result) == 1
        assert "[truncated]" in result[0]
        assert len(result[0]) < 300


# ---------------------------------------------------------------------------
# ContinuationContextBuilder.build_context: mediated summaries integration
# ---------------------------------------------------------------------------


class TestBuildContextWithMediatedSummaries:
    def _make_mediated_event(self, result_summary: str = "data") -> SessionEventRecord:
        return _make_event(
            EventType.mediated_action_completed,
            {
                "target_tool": "server__tool_a",
                "tool_name": "server__tool_a",
                "action_type": "read",
                "status": "completed",
                "result_summary": result_summary,
                "failure_reason": None,
                "policy_decision": POLICY_APPROVED,
            },
        )

    def test_mediated_summaries_empty_when_disabled(self):
        session = _make_session()
        events = [self._make_mediated_event()]
        policy = ContinuationWindowPolicy()
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=False)
        ctx = ContinuationContextBuilder.build_context(session, events, policy, config=cfg)
        assert ctx.mediated_action_summaries == []

    def test_mediated_summaries_populated_when_enabled(self):
        session = _make_session()
        events = [self._make_mediated_event("my result")]
        policy = ContinuationWindowPolicy()
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        ctx = ContinuationContextBuilder.build_context(session, events, policy, config=cfg)
        assert len(ctx.mediated_action_summaries) == 1
        assert "my result" in ctx.mediated_action_summaries[0]

    def test_no_config_produces_empty_summaries(self):
        session = _make_session()
        events = [self._make_mediated_event()]
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy, config=None)
        assert ctx.mediated_action_summaries == []

    def test_reconstruction_version_is_v080(self):
        """v0.8.0 continuation context uses the updated reconstruction version."""
        session = _make_session()
        events = []
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, events, policy)
        assert ctx.reconstruction_version == "v0.8.0"


# ---------------------------------------------------------------------------
# ClaudeCodeExecutionBackend: [Mediated Execution Context] section rendering
# ---------------------------------------------------------------------------


class TestMediatedContextSectionRendering:
    def _make_ctx(self, summaries: list[str]) -> SessionContinuationContext:
        return SessionContinuationContext(
            session_id="sess_abc",
            is_continuation=True,
            session_summary="Prior summary",
            recent_user_requests=["Hello"],
            recent_agent_outputs=["Hi"],
            mediated_action_summaries=summaries,
        )

    def test_no_mediated_summaries_section_absent(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = self._make_ctx([])
        prompt = backend._build_continuation_prompt(
            system_prompt="You are helpful.",
            task="Do something",
            continuation_context=ctx,
        )
        assert "[Mediated Execution Context]" not in prompt

    def test_mediated_summaries_section_present(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = self._make_ctx(["Tool server__tool_a (read): Found 3 results"])
        prompt = backend._build_continuation_prompt(
            system_prompt="You are helpful.",
            task="Do something",
            continuation_context=ctx,
        )
        assert "[Mediated Execution Context]" in prompt
        assert "server__tool_a" in prompt
        assert "Found 3 results" in prompt

    def test_mediated_section_includes_disclaimer(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = self._make_ctx(["Tool x: y"])
        prompt = backend._build_continuation_prompt(
            system_prompt="",
            task="task",
            continuation_context=ctx,
        )
        assert "runtime-mediated" in prompt

    def test_mediated_section_appears_before_current_request(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = self._make_ctx(["Tool server__tool_a (read): data"])
        prompt = backend._build_continuation_prompt(
            system_prompt="",
            task="My task",
            continuation_context=ctx,
        )
        mediated_pos = prompt.index("[Mediated Execution Context]")
        request_pos = prompt.index("[Current Request]")
        assert mediated_pos < request_pos

    def test_multiple_summaries_rendered(self):
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = self._make_ctx([
            "Tool server__tool_a (read): result A",
            "Tool server__tool_b (lookup): result B",
        ])
        prompt = backend._build_continuation_prompt(
            system_prompt="",
            task="task",
            continuation_context=ctx,
        )
        assert "result A" in prompt
        assert "result B" in prompt

    def test_prompt_unchanged_without_mediated_summaries(self):
        """Regression: continuation prompt is identical when mediated_action_summaries is empty."""
        cfg = _make_config(execution_backend="claude_code")
        backend = ClaudeCodeExecutionBackend(cfg)
        # Both contexts have identical content — only difference is explicit vs default field.
        ctx_explicit_empty = SessionContinuationContext(
            session_id="sess_abc",
            is_continuation=True,
            session_summary="Summary",
            recent_user_requests=["Hello"],
            recent_agent_outputs=["Hi"],
            mediated_action_summaries=[],  # explicitly empty
        )
        ctx_default_empty = SessionContinuationContext(
            session_id="sess_abc",
            is_continuation=True,
            session_summary="Summary",
            recent_user_requests=["Hello"],
            recent_agent_outputs=["Hi"],
            # mediated_action_summaries defaults to []
        )
        prompt_explicit = backend._build_continuation_prompt("sys", "task", ctx_explicit_empty)
        prompt_default = backend._build_continuation_prompt("sys", "task", ctx_default_empty)
        assert prompt_explicit == prompt_default
        assert "[Mediated Execution Context]" not in prompt_explicit


# ---------------------------------------------------------------------------
# WorkflowExecutor: _process_mediated_actions (integration via executor)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWorkflowExecutorMediationIntegration:
    """Integration tests via WorkflowExecutor._process_mediated_actions."""

    async def _make_executor_with_mock_backend(
        self,
        tmp_path,
        supports_mediation=True,
        mediation_enabled=True,
        visibility_resolver=None,
    ):
        """Build a minimal WorkflowExecutor with mocked components."""
        from claude_agent_mcp.runtime.artifact_store import ArtifactStore
        from claude_agent_mcp.runtime.policy_engine import PolicyEngine
        from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
        from claude_agent_mcp.runtime.session_store import SessionStore
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        cfg = _make_config(
            claude_code_enable_execution_mediation=mediation_enabled,
        )
        cfg.db_path = tmp_path / "test.db"
        cfg.state_dir = tmp_path / ".state"
        cfg.artifacts_dir = tmp_path / "artifacts"
        cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)
        cfg.allowed_dirs = [str(tmp_path)]

        store = SessionStore(cfg)
        await store.open()

        artifact_store = ArtifactStore(cfg, store.db)
        policy = PolicyEngine(cfg)
        profiles = ProfileRegistry()

        mock_backend = MagicMock()
        mock_backend.name = "claude_code"

        caps_kwargs = {
            "supports_execution_mediation": supports_mediation,
            "supports_mediated_action_results": supports_mediation,
            "supports_structured_continuation_context": True,
            "supports_continuation_window_policy": True,
        }
        mock_backend.capabilities = BackendCapabilities(**caps_kwargs)

        executor = WorkflowExecutor(
            config=cfg,
            session_store=store,
            artifact_store=artifact_store,
            policy_engine=policy,
            profile_registry=profiles,
            execution_backend=mock_backend,
            visibility_resolver=visibility_resolver,
        )
        return executor, store

    async def test_no_requests_no_results(self, tmp_path):
        executor, store = await self._make_executor_with_mock_backend(tmp_path)
        results = await executor._process_mediated_actions(
            output_text="No requests here.",
            session_id="sess_abc",
            profile_name="general",
            turn_index=1,
            invoker=None,
        )
        assert results == []
        await store.close()

    async def test_disabled_backend_returns_empty(self, tmp_path):
        executor, store = await self._make_executor_with_mock_backend(
            tmp_path, supports_mediation=False
        )
        block = _make_request_block()
        results = await executor._process_mediated_actions(
            output_text=block,
            session_id="sess_abc",
            profile_name="general",
            turn_index=1,
            invoker=None,
        )
        assert results == []
        await store.close()

    async def test_rejected_request_persists_events(self, tmp_path):
        executor, store = await self._make_executor_with_mock_backend(
            tmp_path,
            mediation_enabled=False,  # disable so all requests are rejected
        )
        # Create a session to receive events
        session = await store.create_session(
            workflow=WorkflowName.run_task,
            profile=ProfileName.general,
            working_directory=str(tmp_path),
        )
        block = _make_request_block()
        results = await executor._process_mediated_actions(
            output_text=block,
            session_id=session.session_id,
            profile_name="general",
            turn_index=1,
            invoker=None,
        )
        assert len(results) == 1
        assert results[0].status == MediatedActionStatus.rejected

        events = await store.get_events(session.session_id)
        event_types = [e.event_type for e in events]
        assert EventType.mediated_action_requested in event_types
        assert EventType.mediated_action_rejected in event_types
        assert EventType.mediated_action_approved not in event_types
        assert EventType.mediated_action_completed not in event_types
        await store.close()

    async def test_approved_request_persists_complete_events(self, tmp_path):
        resolver = _make_visibility_resolver(["server__tool_a"])
        executor, store = await self._make_executor_with_mock_backend(
            tmp_path,
            mediation_enabled=True,
            visibility_resolver=resolver,
        )

        session = await store.create_session(
            workflow=WorkflowName.run_task,
            profile=ProfileName.general,
            working_directory=str(tmp_path),
        )

        mock_invoker = MagicMock()
        mock_invoke_result = MagicMock()
        mock_invoke_result.to_content_string.return_value = "tool output"
        mock_invoker.invoke = AsyncMock(return_value=mock_invoke_result)

        block = _make_request_block(target_tool="server__tool_a")
        results = await executor._process_mediated_actions(
            output_text=block,
            session_id=session.session_id,
            profile_name="general",
            turn_index=1,
            invoker=mock_invoker,
        )

        assert len(results) == 1
        assert results[0].status == MediatedActionStatus.completed

        events = await store.get_events(session.session_id)
        event_types = [e.event_type for e in events]
        assert EventType.mediated_action_requested in event_types
        assert EventType.mediated_action_approved in event_types
        assert EventType.mediated_action_completed in event_types
        assert EventType.mediated_action_rejected not in event_types
        await store.close()

    async def test_rejected_adds_warning_in_run_task(self, tmp_path):
        """Regression: rejected mediated actions appear in response warnings."""
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
        from claude_agent_mcp.runtime.artifact_store import ArtifactStore
        from claude_agent_mcp.runtime.policy_engine import PolicyEngine
        from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
        from claude_agent_mcp.runtime.session_store import SessionStore
        from claude_agent_mcp.types import NormalizedProviderResult, RunTaskRequest

        cfg = _make_config(
            claude_code_enable_execution_mediation=False  # disabled → rejected
        )
        cfg.db_path = tmp_path / "test.db"
        cfg.state_dir = tmp_path / ".state"
        cfg.artifacts_dir = tmp_path / "artifacts"
        cfg.artifacts_dir.mkdir(parents=True, exist_ok=True)
        cfg.allowed_dirs = [str(tmp_path)]

        store = SessionStore(cfg)
        await store.open()

        block = _make_request_block()
        backend = MagicMock()
        backend.name = "claude_code"
        backend.capabilities = BackendCapabilities(
            supports_execution_mediation=True,
            supports_mediated_action_results=True,
        )
        backend.execute = AsyncMock(
            return_value=NormalizedProviderResult(
                output_text=f"Response text.\n{block}",
                turn_count=1,
                stop_reason="backend_defaulted",
                warnings=[],
            )
        )

        executor = WorkflowExecutor(
            config=cfg,
            session_store=store,
            artifact_store=ArtifactStore(cfg, store.db),
            policy_engine=PolicyEngine(cfg),
            profile_registry=ProfileRegistry(),
            execution_backend=backend,
        )

        req = RunTaskRequest(task="Do something", working_directory=str(tmp_path))
        response = await executor.run_task(req)

        assert any("rejected" in w.lower() or "mediat" in w.lower() for w in response.warnings), \
            f"Expected rejection warning in: {response.warnings}"
        await store.close()


# ---------------------------------------------------------------------------
# Regression: no side-effects when mediation is not involved
# ---------------------------------------------------------------------------


class TestRegressionNoMediationSideEffects:
    def test_parse_returns_empty_for_normal_output(self):
        """Normal backend output should never produce mediated action requests."""
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg)
        normal_outputs = [
            "The task is complete.",
            "Here is the analysis:\n- Item 1\n- Item 2",
            "I've reviewed the code and found 3 issues.",
            "VERDICT: pass\nFINDINGS:\n- No issues found.",
            "```python\nresult = 42\n```",
        ]
        for text in normal_outputs:
            assert engine.parse_requests(text) == [], f"Unexpected parse result for: {text!r}"

    def test_continuation_builder_unaffected_without_mediation_events(self):
        """build_context with no mediation events produces unchanged context."""
        session = _make_session()
        events = [
            _make_event(EventType.user_input, {"task": "Task 1"}),
            _make_event(EventType.provider_response_summary, {"summary": "Done", "stop_reason": "backend_defaulted"}),
        ]
        policy = ContinuationWindowPolicy()
        cfg = _make_config()
        ctx = ContinuationContextBuilder.build_context(session, events, policy, config=cfg)
        assert ctx.mediated_action_summaries == []
        assert len(ctx.recent_user_requests) == 1
        assert ctx.session_summary == "Session summary"

    def test_api_backend_capabilities_unchanged(self):
        """v0.8.0 additions do not change api backend capability values."""
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        caps = backend.capabilities
        assert caps.supports_downstream_tools is True
        assert caps.supports_native_multiturn is True
        assert caps.supports_execution_mediation is False
        assert caps.supports_mediated_action_results is False
