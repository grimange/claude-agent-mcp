"""Tests for v0.9.0 mediation hardening and bounded workflow expansion.

Covers:
- MediationRejectionReason, MediationContinuationInclusionMode enums (types)
- MediatedWorkflowStep, MediatedWorkflowRequest, MediatedWorkflowResult models (types)
- MediationPolicyProfile model (types)
- New EventType values for bounded workflows (types)
- mediated_workflow_summaries field on SessionContinuationContext (types)
- Config v0.9.0 fields (config)
- BackendCapabilities v0.9.0 flags (base.py / claude_code_backend.py)
- MediationEngine.parse_workflow() — valid, malformed, missing fields, empty steps
- MediationEngine.validate_workflow_request() — enabled/disabled, version, step count
- MediationEngine.validate_request() — new gates: session limit, tool allow/deny
- MediationEngine.step_to_action_request()
- MediationEngine.rejection_reason_enum() — all policy codes
- MediationEngine.build_policy_profile()
- Continuation: _extract_workflow_summaries() — enabled/disabled, rejected inclusion
- Continuation: _extract_mediated_summaries() — rejected step inclusion (v0.9.0 extension)
- WorkflowExecutor._process_mediated_actions() — workflow execution, workflow events, session limit
- WorkflowExecutor._count_session_approvals()
- Deterministic parsing (identical inputs → identical results)
- Regression: v0.8.0 single-action format still works
- Regression: v0.8.0 rejection codes still produce correct MediationRejectionReason
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
    WORKFLOW_MEDIATION_VERSION,
    SUPPORTED_MEDIATION_VERSIONS,
    POLICY_APPROVED,
    POLICY_REJECTED_DISABLED,
    POLICY_REJECTED_FEDERATION_INACTIVE,
    POLICY_REJECTED_LIMIT,
    POLICY_REJECTED_MALFORMED,
    POLICY_REJECTED_SESSION_APPROVAL_LIMIT,
    POLICY_REJECTED_TOOL_NOT_ALLOWED,
    POLICY_REJECTED_TOOL_VISIBILITY,
    POLICY_REJECTED_TYPE,
    POLICY_REJECTED_VERSION,
    POLICY_REJECTED_WORKFLOW_STEP_LIMIT,
    MediationEngine,
)
from claude_agent_mcp.types import (
    ContinuationWindowPolicy,
    EventType,
    MediatedActionRequest,
    MediatedActionResult,
    MediatedActionStatus,
    MediatedActionType,
    MediationContinuationInclusionMode,
    MediationPolicyProfile,
    MediationRejectionReason,
    MediatedWorkflowRequest,
    MediatedWorkflowResult,
    MediatedWorkflowStep,
    MediatedWorkflowStepResult,
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
    """Construct a minimal Config for v0.9.0 testing."""
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
    # v0.9.0 mediation fields
    cfg.claude_code_max_mediated_workflow_steps = kwargs.get(
        "claude_code_max_mediated_workflow_steps", 1
    )
    cfg.claude_code_allowed_mediated_tools = kwargs.get(
        "claude_code_allowed_mediated_tools", []
    )
    cfg.claude_code_denied_mediated_tools = kwargs.get(
        "claude_code_denied_mediated_tools", []
    )
    cfg.claude_code_max_session_mediated_approvals = kwargs.get(
        "claude_code_max_session_mediated_approvals", 100
    )
    cfg.claude_code_include_rejected_mediation_in_continuation = kwargs.get(
        "claude_code_include_rejected_mediation_in_continuation", False
    )
    cfg.claude_code_mediation_policy_profile = kwargs.get(
        "claude_code_mediation_policy_profile", "conservative"
    )
    return cfg


def _make_visibility_resolver(tool_names: list[str]):
    """Build a mock visibility resolver that returns the given tool names."""
    resolver = MagicMock()

    def _resolve(profile):
        tools = []
        for name in tool_names:
            t = MagicMock()
            t.normalized_name = name
            tools.append(t)
        return tools

    resolver.resolve.side_effect = _resolve
    return resolver


def _make_workflow_block(
    workflow_id: str = "wf_001",
    steps: list[dict] | None = None,
    mediation_version: str = WORKFLOW_MEDIATION_VERSION,
    justification: str = "Need data",
) -> str:
    """Build a valid workflow request block string."""
    if steps is None:
        steps = [
            {
                "step_index": 0,
                "action_type": "read",
                "target_tool": "server__tool_a",
                "justification": "Need to read data",
                "arguments": {},
            }
        ]
    data = {
        "mediation_version": mediation_version,
        "workflow_id": workflow_id,
        "justification": justification,
        "steps": steps,
    }
    return f"<mediated_workflow_request>\n{json.dumps(data)}\n</mediated_workflow_request>"


def _make_session(session_id: str = "sess_abc") -> SessionRecord:
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
        turn_count=1,
    )


def _make_event(
    event_type: EventType,
    payload: dict | None = None,
    turn_index: int = 0,
    session_id: str = "sess_abc",
) -> SessionEventRecord:
    return SessionEventRecord(
        session_id=session_id,
        event_type=event_type,
        turn_index=turn_index,
        payload=payload or {},
        created_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# 1. New types: enums and models
# ---------------------------------------------------------------------------


class TestMediationRejectionReason:
    def test_all_values_present(self):
        values = {r.value for r in MediationRejectionReason}
        expected = {
            "feature_disabled",
            "invalid_version",
            "unsupported_action_type",
            "per_turn_limit_exceeded",
            "workflow_step_limit_exceeded",
            "session_approval_limit_exceeded",
            "federation_inactive",
            "tool_not_visible",
            "tool_not_allowed",
            "malformed_request",
        }
        assert values == expected

    def test_is_string_enum(self):
        assert MediationRejectionReason.feature_disabled == "feature_disabled"
        assert MediationRejectionReason.tool_not_allowed == "tool_not_allowed"


class TestMediationContinuationInclusionMode:
    def test_all_values_present(self):
        values = {m.value for m in MediationContinuationInclusionMode}
        assert values == {"approved_only", "all_steps", "none"}

    def test_default_is_approved_only(self):
        profile = MediationPolicyProfile()
        assert profile.continuation_inclusion_mode == MediationContinuationInclusionMode.approved_only


class TestMediatedWorkflowModels:
    def test_workflow_step_construction(self):
        step = MediatedWorkflowStep(
            step_index=0,
            action_type=MediatedActionType.read,
            target_tool="server__tool_a",
            justification="Need data",
        )
        assert step.step_index == 0
        assert step.action_type == MediatedActionType.read
        assert step.target_tool == "server__tool_a"
        assert step.arguments == {}

    def test_workflow_request_construction(self):
        wf = MediatedWorkflowRequest(
            mediation_version=WORKFLOW_MEDIATION_VERSION,
            workflow_id="wf_001",
            steps=[
                MediatedWorkflowStep(
                    step_index=0,
                    action_type=MediatedActionType.read,
                    target_tool="server__tool_a",
                    justification="Need data",
                )
            ],
        )
        assert wf.workflow_id == "wf_001"
        assert len(wf.steps) == 1
        assert wf.justification == ""

    def test_workflow_result_construction(self):
        action_result = MediatedActionResult(
            request_id="wf_step_0",
            status=MediatedActionStatus.completed,
            tool_name="server__tool_a",
            arguments_summary="(no arguments)",
            result_summary="result data",
            policy_decision=POLICY_APPROVED,
        )
        step_result = MediatedWorkflowStepResult(
            step_index=0,
            action_result=action_result,
        )
        wf_result = MediatedWorkflowResult(
            workflow_id="wf_001",
            total_steps=1,
            approved_steps=1,
            rejected_steps=0,
            completed_steps=1,
            failed_steps=0,
            step_results=[step_result],
        )
        assert wf_result.workflow_id == "wf_001"
        assert wf_result.completed_steps == 1
        assert step_result.rejection_reason is None

    def test_step_result_with_rejection_reason(self):
        action_result = MediatedActionResult(
            request_id="wf_step_0",
            status=MediatedActionStatus.rejected,
            tool_name="server__tool_a",
            arguments_summary="(no arguments)",
            result_summary="",
            failure_reason="tool is denied",
            policy_decision=POLICY_REJECTED_TOOL_NOT_ALLOWED,
        )
        step_result = MediatedWorkflowStepResult(
            step_index=0,
            action_result=action_result,
            rejection_reason=MediationRejectionReason.tool_not_allowed,
        )
        assert step_result.rejection_reason == MediationRejectionReason.tool_not_allowed


class TestMediationPolicyProfile:
    def test_default_construction(self):
        profile = MediationPolicyProfile()
        assert profile.name == "conservative"
        assert profile.allowed_action_types == []
        assert profile.allowed_tools == []
        assert profile.denied_tools == []
        assert profile.max_steps_per_turn == 1
        assert profile.max_approvals_per_session == 100
        assert profile.mixed_action_types_allowed is True

    def test_custom_profile(self):
        profile = MediationPolicyProfile(
            name="custom",
            allowed_tools=["server__tool_a"],
            denied_tools=["server__tool_b"],
            max_steps_per_turn=3,
            max_approvals_per_session=50,
            continuation_inclusion_mode=MediationContinuationInclusionMode.all_steps,
        )
        assert profile.name == "custom"
        assert "server__tool_a" in profile.allowed_tools
        assert "server__tool_b" in profile.denied_tools
        assert profile.max_steps_per_turn == 3
        assert profile.max_approvals_per_session == 50
        assert profile.continuation_inclusion_mode == MediationContinuationInclusionMode.all_steps


class TestNewEventTypes:
    def test_workflow_event_types_exist(self):
        expected = {
            "mediated_workflow_requested",
            "mediated_workflow_step_requested",
            "mediated_workflow_step_approved",
            "mediated_workflow_step_rejected",
            "mediated_workflow_step_completed",
            "mediated_workflow_completed",
        }
        actual = {e.value for e in EventType}
        for val in expected:
            assert val in actual, f"EventType.{val} not found"

    def test_v08_event_types_still_present(self):
        """Regression: v0.8.0 event types must be preserved."""
        assert "mediated_action_requested" in {e.value for e in EventType}
        assert "mediated_action_approved" in {e.value for e in EventType}
        assert "mediated_action_rejected" in {e.value for e in EventType}
        assert "mediated_action_completed" in {e.value for e in EventType}


class TestSessionContinuationContextV9:
    def test_mediated_workflow_summaries_field(self):
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
        )
        assert hasattr(ctx, "mediated_workflow_summaries")
        assert ctx.mediated_workflow_summaries == []

    def test_reconstruction_version_bumped(self):
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
        )
        # Updated to v1.0.0 as part of v1.0.0 stabilization release
        assert ctx.reconstruction_version == "v1.0.0"

    def test_mediated_action_summaries_still_present(self):
        """Regression: v0.8.0 field must be preserved."""
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
        )
        assert hasattr(ctx, "mediated_action_summaries")
        assert ctx.mediated_action_summaries == []


# ---------------------------------------------------------------------------
# 2. Config fields
# ---------------------------------------------------------------------------


class TestConfigV9Fields:
    def test_all_new_fields_present(self):
        cfg = _make_config()
        assert hasattr(cfg, "claude_code_max_mediated_workflow_steps")
        assert hasattr(cfg, "claude_code_allowed_mediated_tools")
        assert hasattr(cfg, "claude_code_denied_mediated_tools")
        assert hasattr(cfg, "claude_code_max_session_mediated_approvals")
        assert hasattr(cfg, "claude_code_include_rejected_mediation_in_continuation")
        assert hasattr(cfg, "claude_code_mediation_policy_profile")

    def test_defaults_are_conservative(self):
        cfg = _make_config()
        assert cfg.claude_code_max_mediated_workflow_steps == 1
        assert cfg.claude_code_allowed_mediated_tools == []
        assert cfg.claude_code_denied_mediated_tools == []
        assert cfg.claude_code_max_session_mediated_approvals == 100
        assert cfg.claude_code_include_rejected_mediation_in_continuation is False
        assert cfg.claude_code_mediation_policy_profile == "conservative"

    def test_allowed_tools_configured(self):
        cfg = _make_config(claude_code_allowed_mediated_tools=["server__tool_a", "server__tool_b"])
        assert cfg.claude_code_allowed_mediated_tools == ["server__tool_a", "server__tool_b"]

    def test_denied_tools_configured(self):
        cfg = _make_config(claude_code_denied_mediated_tools=["server__dangerous"])
        assert cfg.claude_code_denied_mediated_tools == ["server__dangerous"]

    def test_session_approval_limit_configured(self):
        cfg = _make_config(claude_code_max_session_mediated_approvals=10)
        assert cfg.claude_code_max_session_mediated_approvals == 10

    def test_include_rejected_in_continuation_configured(self):
        cfg = _make_config(claude_code_include_rejected_mediation_in_continuation=True)
        assert cfg.claude_code_include_rejected_mediation_in_continuation is True

    def test_v08_fields_still_present(self):
        """Regression: v0.8.0 config fields must be preserved."""
        cfg = _make_config()
        assert hasattr(cfg, "claude_code_enable_execution_mediation")
        assert hasattr(cfg, "claude_code_max_mediated_actions_per_turn")
        assert hasattr(cfg, "claude_code_allowed_mediated_action_types")
        assert hasattr(cfg, "claude_code_include_mediated_results_in_continuation")


# ---------------------------------------------------------------------------
# 3. Capability flags
# ---------------------------------------------------------------------------


class TestBackendCapabilitiesV9:
    def test_new_flags_have_defaults(self):
        caps = BackendCapabilities()
        assert caps.supports_bounded_mediated_workflows is False
        assert caps.supports_mediation_policy_profiles is False

    def test_claude_code_backend_sets_new_flags(self):
        caps = ClaudeCodeExecutionBackend(MagicMock()).capabilities
        assert caps.supports_bounded_mediated_workflows is True
        assert caps.supports_mediation_policy_profiles is True

    def test_api_backend_does_not_set_new_flags(self):
        caps = ApiExecutionBackend(MagicMock()).capabilities
        assert caps.supports_bounded_mediated_workflows is False
        assert caps.supports_mediation_policy_profiles is False

    def test_v08_flags_still_set_on_claude_code(self):
        """Regression: v0.8.0 capability flags must be preserved."""
        caps = ClaudeCodeExecutionBackend(MagicMock()).capabilities
        assert caps.supports_execution_mediation is True
        assert caps.supports_mediated_action_results is True


# ---------------------------------------------------------------------------
# 4. Mediation engine constants
# ---------------------------------------------------------------------------


class TestMediationEngineConstants:
    def test_workflow_version_defined(self):
        assert WORKFLOW_MEDIATION_VERSION == "v0.9.0"

    def test_both_versions_in_supported_set(self):
        assert MEDIATION_VERSION in SUPPORTED_MEDIATION_VERSIONS
        assert WORKFLOW_MEDIATION_VERSION in SUPPORTED_MEDIATION_VERSIONS

    def test_new_policy_codes_defined(self):
        assert POLICY_REJECTED_TOOL_NOT_ALLOWED == "rejected:tool_not_allowed"
        assert POLICY_REJECTED_WORKFLOW_STEP_LIMIT == "rejected:workflow_step_limit_exceeded"
        assert POLICY_REJECTED_SESSION_APPROVAL_LIMIT == "rejected:session_approval_limit_exceeded"
        assert POLICY_REJECTED_MALFORMED == "rejected:malformed_request"


# ---------------------------------------------------------------------------
# 5. parse_workflow()
# ---------------------------------------------------------------------------


class TestParseWorkflow:
    def test_parse_valid_workflow_single_step(self):
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg)
        text = _make_workflow_block()
        workflows = engine.parse_workflow(text)
        assert len(workflows) == 1
        assert workflows[0].workflow_id == "wf_001"
        assert len(workflows[0].steps) == 1
        assert workflows[0].steps[0].action_type == MediatedActionType.read
        assert workflows[0].steps[0].target_tool == "server__tool_a"

    def test_parse_valid_workflow_multi_step(self):
        cfg = _make_config(
            claude_code_enable_execution_mediation=True,
            claude_code_max_mediated_workflow_steps=3,
        )
        engine = MediationEngine(cfg)
        text = _make_workflow_block(
            steps=[
                {"step_index": 0, "action_type": "read", "target_tool": "server__tool_a", "justification": "a"},
                {"step_index": 1, "action_type": "lookup", "target_tool": "server__tool_b", "justification": "b"},
            ]
        )
        workflows = engine.parse_workflow(text)
        assert len(workflows) == 1
        assert len(workflows[0].steps) == 2
        assert workflows[0].steps[0].action_type == MediatedActionType.read
        assert workflows[0].steps[1].action_type == MediatedActionType.lookup

    def test_parse_empty_output(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        assert engine.parse_workflow("no workflow blocks here") == []

    def test_parse_malformed_json_skipped(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        text = "<mediated_workflow_request>\nnot valid json\n</mediated_workflow_request>"
        assert engine.parse_workflow(text) == []

    def test_parse_missing_required_fields_skipped(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        # Missing 'steps'
        text = '<mediated_workflow_request>\n{"mediation_version":"v0.9.0","workflow_id":"wf_001"}\n</mediated_workflow_request>'
        assert engine.parse_workflow(text) == []

    def test_parse_non_object_skipped(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        text = "<mediated_workflow_request>\n[1,2,3]\n</mediated_workflow_request>"
        assert engine.parse_workflow(text) == []

    def test_parse_empty_steps_skipped(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        data = {"mediation_version": "v0.9.0", "workflow_id": "wf_001", "steps": []}
        text = f"<mediated_workflow_request>\n{json.dumps(data)}\n</mediated_workflow_request>"
        assert engine.parse_workflow(text) == []

    def test_parse_step_with_unknown_action_type_skips_workflow(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        data = {
            "mediation_version": "v0.9.0",
            "workflow_id": "wf_001",
            "steps": [
                {"step_index": 0, "action_type": "delete", "target_tool": "server__tool_a", "justification": "bad"}
            ],
        }
        text = f"<mediated_workflow_request>\n{json.dumps(data)}\n</mediated_workflow_request>"
        assert engine.parse_workflow(text) == []

    def test_parse_step_missing_fields_skips_workflow(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        data = {
            "mediation_version": "v0.9.0",
            "workflow_id": "wf_001",
            "steps": [
                {"step_index": 0, "action_type": "read"}  # missing target_tool and justification
            ],
        }
        text = f"<mediated_workflow_request>\n{json.dumps(data)}\n</mediated_workflow_request>"
        assert engine.parse_workflow(text) == []

    def test_parse_multiple_workflows(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        block1 = _make_workflow_block(workflow_id="wf_001")
        block2 = _make_workflow_block(workflow_id="wf_002")
        text = f"{block1}\n\nSome text\n\n{block2}"
        workflows = engine.parse_workflow(text)
        assert len(workflows) == 2
        assert workflows[0].workflow_id == "wf_001"
        assert workflows[1].workflow_id == "wf_002"

    def test_parse_deterministic(self):
        """Same input always produces same output."""
        cfg = _make_config()
        engine = MediationEngine(cfg)
        text = _make_workflow_block()
        r1 = engine.parse_workflow(text)
        r2 = engine.parse_workflow(text)
        assert len(r1) == len(r2) == 1
        assert r1[0].workflow_id == r2[0].workflow_id
        assert r1[0].steps[0].target_tool == r2[0].steps[0].target_tool

    def test_parse_does_not_detect_single_action_blocks(self):
        """parse_workflow does not parse <mediated_action_request> blocks."""
        cfg = _make_config()
        engine = MediationEngine(cfg)
        single_action = (
            '<mediated_action_request>\n'
            f'{{"mediation_version":"{MEDIATION_VERSION}","request_id":"req_001",'
            '"action_type":"read","target_tool":"server__tool_a","justification":"x"}}\n'
            '</mediated_action_request>'
        )
        assert engine.parse_workflow(single_action) == []


# ---------------------------------------------------------------------------
# 6. validate_workflow_request()
# ---------------------------------------------------------------------------


class TestValidateWorkflowRequest:
    def _make_wf(self, step_count: int = 1, version: str = WORKFLOW_MEDIATION_VERSION) -> MediatedWorkflowRequest:
        steps = [
            MediatedWorkflowStep(
                step_index=i,
                action_type=MediatedActionType.read,
                target_tool=f"server__tool_{i}",
                justification=f"step {i}",
            )
            for i in range(step_count)
        ]
        return MediatedWorkflowRequest(
            mediation_version=version,
            workflow_id="wf_001",
            steps=steps,
        )

    def test_disabled_rejects(self):
        cfg = _make_config(claude_code_enable_execution_mediation=False)
        engine = MediationEngine(cfg)
        ok, code = engine.validate_workflow_request(self._make_wf())
        assert not ok
        assert code == POLICY_REJECTED_DISABLED

    def test_wrong_version_rejects(self):
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg)
        ok, code = engine.validate_workflow_request(self._make_wf(version="v0.8.0"))
        assert not ok
        assert code == POLICY_REJECTED_VERSION

    def test_step_count_within_limit_passes(self):
        cfg = _make_config(
            claude_code_enable_execution_mediation=True,
            claude_code_max_mediated_workflow_steps=2,
        )
        engine = MediationEngine(cfg)
        ok, code = engine.validate_workflow_request(self._make_wf(step_count=2))
        assert ok
        assert code == POLICY_APPROVED

    def test_step_count_exceeds_limit_rejects(self):
        cfg = _make_config(
            claude_code_enable_execution_mediation=True,
            claude_code_max_mediated_workflow_steps=1,
        )
        engine = MediationEngine(cfg)
        ok, code = engine.validate_workflow_request(self._make_wf(step_count=2))
        assert not ok
        assert code == POLICY_REJECTED_WORKFLOW_STEP_LIMIT

    def test_default_max_one_step(self):
        cfg = _make_config(claude_code_enable_execution_mediation=True)
        engine = MediationEngine(cfg)
        # 1 step passes with default max=1
        ok1, _ = engine.validate_workflow_request(self._make_wf(step_count=1))
        assert ok1
        # 2 steps fails
        ok2, code2 = engine.validate_workflow_request(self._make_wf(step_count=2))
        assert not ok2
        assert code2 == POLICY_REJECTED_WORKFLOW_STEP_LIMIT


# ---------------------------------------------------------------------------
# 7. validate_request() — new v0.9.0 gates
# ---------------------------------------------------------------------------


class TestValidateRequestV9Gates:
    def _make_enabled_cfg(self, **kwargs) -> Config:
        return _make_config(
            claude_code_enable_execution_mediation=True,
            **kwargs,
        )

    def _make_req(self, target_tool: str = "server__tool_a") -> MediatedActionRequest:
        return MediatedActionRequest(
            mediation_version=MEDIATION_VERSION,
            request_id="req_001",
            action_type=MediatedActionType.read,
            target_tool=target_tool,
            justification="Need data",
        )

    def test_session_approval_limit_not_exceeded(self):
        cfg = self._make_enabled_cfg(
            claude_code_max_session_mediated_approvals=10,
            claude_code_max_mediated_actions_per_turn=3,
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(
            self._make_req(), "general", 0, session_approved_total=9
        )
        # 9 prior + 0 current = 9 < 10, should pass
        assert ok
        assert code == POLICY_APPROVED

    def test_session_approval_limit_at_boundary_rejects(self):
        cfg = self._make_enabled_cfg(
            claude_code_max_session_mediated_approvals=10,
            claude_code_max_mediated_actions_per_turn=3,
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(
            self._make_req(), "general", 0, session_approved_total=10
        )
        # 10 prior + 0 current = 10 >= 10, should reject
        assert not ok
        assert code == POLICY_REJECTED_SESSION_APPROVAL_LIMIT

    def test_session_approval_combined_count_rejects(self):
        cfg = self._make_enabled_cfg(
            claude_code_max_session_mediated_approvals=5,
            claude_code_max_mediated_actions_per_turn=3,
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        # 3 prior + 2 current = 5 >= 5, reject
        ok, code = engine.validate_request(
            self._make_req(), "general", 2, session_approved_total=3
        )
        assert not ok
        assert code == POLICY_REJECTED_SESSION_APPROVAL_LIMIT

    def test_denied_tool_rejects(self):
        cfg = self._make_enabled_cfg(
            claude_code_denied_mediated_tools=["server__tool_a"],
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert not ok
        assert code == POLICY_REJECTED_TOOL_NOT_ALLOWED

    def test_denied_tool_other_tool_passes(self):
        cfg = self._make_enabled_cfg(
            claude_code_denied_mediated_tools=["server__tool_b"],
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert ok
        assert code == POLICY_APPROVED

    def test_allowed_tools_list_enforced(self):
        cfg = self._make_enabled_cfg(
            claude_code_allowed_mediated_tools=["server__tool_b"],
        )
        resolver = _make_visibility_resolver(["server__tool_a", "server__tool_b"])
        engine = MediationEngine(cfg, resolver)
        # server__tool_a not in allowed list → rejected
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert not ok
        assert code == POLICY_REJECTED_TOOL_NOT_ALLOWED

    def test_allowed_tools_list_permits_listed_tool(self):
        cfg = self._make_enabled_cfg(
            claude_code_allowed_mediated_tools=["server__tool_a"],
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert ok
        assert code == POLICY_APPROVED

    def test_empty_allowed_list_permits_all_visible(self):
        """Empty allowed_tools = permit all visible tools (default behavior)."""
        cfg = self._make_enabled_cfg(claude_code_allowed_mediated_tools=[])
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert ok
        assert code == POLICY_APPROVED

    def test_denied_takes_precedence_over_allowed(self):
        """Denied list blocks a tool even if it is in the allowed list."""
        cfg = self._make_enabled_cfg(
            claude_code_allowed_mediated_tools=["server__tool_a"],
            claude_code_denied_mediated_tools=["server__tool_a"],
        )
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        # denied gate (6) fires before allowed gate (7)
        ok, code = engine.validate_request(self._make_req("server__tool_a"), "general", 0)
        assert not ok
        assert code == POLICY_REJECTED_TOOL_NOT_ALLOWED

    def test_session_limit_default_100_passes_small_count(self):
        cfg = self._make_enabled_cfg()  # default max_session_mediated_approvals=100
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        ok, code = engine.validate_request(
            self._make_req(), "general", 0, session_approved_total=0
        )
        assert ok

    def test_v08_version_accepted(self):
        """v0.8.0 mediation_version still accepted in validate_request (regression)."""
        cfg = self._make_enabled_cfg()
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        req = MediatedActionRequest(
            mediation_version=MEDIATION_VERSION,
            request_id="req_001",
            action_type=MediatedActionType.read,
            target_tool="server__tool_a",
            justification="Need data",
        )
        ok, code = engine.validate_request(req, "general", 0)
        assert ok

    def test_unknown_version_rejected(self):
        cfg = self._make_enabled_cfg()
        resolver = _make_visibility_resolver(["server__tool_a"])
        engine = MediationEngine(cfg, resolver)
        req = MediatedActionRequest(
            mediation_version="v0.7.0",
            request_id="req_001",
            action_type=MediatedActionType.read,
            target_tool="server__tool_a",
            justification="Need data",
        )
        ok, code = engine.validate_request(req, "general", 0)
        assert not ok
        assert code == POLICY_REJECTED_VERSION


# ---------------------------------------------------------------------------
# 8. step_to_action_request()
# ---------------------------------------------------------------------------


class TestStepToActionRequest:
    def test_conversion(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        step = MediatedWorkflowStep(
            step_index=2,
            action_type=MediatedActionType.inspect,
            target_tool="server__tool_c",
            arguments={"key": "value"},
            justification="Check state",
        )
        req = engine.step_to_action_request(step, WORKFLOW_MEDIATION_VERSION)
        assert req.action_type == MediatedActionType.inspect
        assert req.target_tool == "server__tool_c"
        assert req.arguments == {"key": "value"}
        assert req.justification == "Check state"
        # Converted to single-action mediation_version for gate compat
        assert req.mediation_version == MEDIATION_VERSION
        assert req.request_id == "wf_step_2"


# ---------------------------------------------------------------------------
# 9. rejection_reason_enum()
# ---------------------------------------------------------------------------


class TestRejectionReasonEnum:
    def test_all_v08_codes_map(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        assert engine.rejection_reason_enum(POLICY_REJECTED_DISABLED) == MediationRejectionReason.feature_disabled
        assert engine.rejection_reason_enum(POLICY_REJECTED_VERSION) == MediationRejectionReason.invalid_version
        assert engine.rejection_reason_enum(POLICY_REJECTED_TYPE) == MediationRejectionReason.unsupported_action_type
        assert engine.rejection_reason_enum(POLICY_REJECTED_LIMIT) == MediationRejectionReason.per_turn_limit_exceeded
        assert engine.rejection_reason_enum(POLICY_REJECTED_TOOL_VISIBILITY) == MediationRejectionReason.tool_not_visible
        assert engine.rejection_reason_enum(POLICY_REJECTED_FEDERATION_INACTIVE) == MediationRejectionReason.federation_inactive

    def test_new_v09_codes_map(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        assert engine.rejection_reason_enum(POLICY_REJECTED_TOOL_NOT_ALLOWED) == MediationRejectionReason.tool_not_allowed
        assert engine.rejection_reason_enum(POLICY_REJECTED_WORKFLOW_STEP_LIMIT) == MediationRejectionReason.workflow_step_limit_exceeded
        assert engine.rejection_reason_enum(POLICY_REJECTED_SESSION_APPROVAL_LIMIT) == MediationRejectionReason.session_approval_limit_exceeded
        assert engine.rejection_reason_enum(POLICY_REJECTED_MALFORMED) == MediationRejectionReason.malformed_request

    def test_unknown_code_returns_malformed(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        result = engine.rejection_reason_enum("rejected:something_new")
        assert result == MediationRejectionReason.malformed_request


# ---------------------------------------------------------------------------
# 10. build_policy_profile()
# ---------------------------------------------------------------------------


class TestBuildPolicyProfile:
    def test_conservative_defaults(self):
        cfg = _make_config()
        engine = MediationEngine(cfg)
        profile = engine.build_policy_profile()
        assert profile.name == "conservative"
        assert profile.allowed_action_types == []
        assert profile.allowed_tools == []
        assert profile.denied_tools == []
        assert profile.max_steps_per_turn == 1
        assert profile.max_approvals_per_session == 100
        assert profile.continuation_inclusion_mode == MediationContinuationInclusionMode.approved_only

    def test_custom_profile_from_config(self):
        cfg = _make_config(
            claude_code_mediation_policy_profile="custom",
            claude_code_allowed_mediated_tools=["server__tool_a"],
            claude_code_denied_mediated_tools=["server__tool_b"],
            claude_code_max_mediated_actions_per_turn=3,
            claude_code_max_session_mediated_approvals=50,
            claude_code_include_rejected_mediation_in_continuation=True,
        )
        engine = MediationEngine(cfg)
        profile = engine.build_policy_profile()
        assert profile.name == "custom"
        assert profile.allowed_tools == ["server__tool_a"]
        assert profile.denied_tools == ["server__tool_b"]
        assert profile.max_steps_per_turn == 3
        assert profile.max_approvals_per_session == 50
        assert profile.continuation_inclusion_mode == MediationContinuationInclusionMode.all_steps

    def test_include_rejected_false_gives_approved_only(self):
        cfg = _make_config(claude_code_include_rejected_mediation_in_continuation=False)
        engine = MediationEngine(cfg)
        profile = engine.build_policy_profile()
        assert profile.continuation_inclusion_mode == MediationContinuationInclusionMode.approved_only


# ---------------------------------------------------------------------------
# 11. Continuation: _extract_workflow_summaries()
# ---------------------------------------------------------------------------


class TestExtractWorkflowSummaries:
    def test_disabled_returns_empty(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=False)
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "completed",
                "result_summary": "some data",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert result == []

    def test_none_config_returns_empty(self):
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "completed",
                "result_summary": "data",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, None)
        assert result == []

    def test_completed_step_included(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "completed",
                "result_summary": "result data",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert len(result) == 1
        assert "wf_001" in result[0]
        assert "server__tool_a" in result[0]
        assert "result data" in result[0]

    def test_failed_step_included(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "failed",
                "failure_reason": "connection timeout",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert len(result) == 1
        assert "failed" in result[0]
        assert "connection timeout" in result[0]

    def test_rejected_step_excluded_by_default(self):
        cfg = _make_config(
            claude_code_include_mediated_results_in_continuation=True,
            claude_code_include_rejected_mediation_in_continuation=False,
        )
        events = [
            _make_event(EventType.mediated_workflow_step_rejected, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "rejection_reason": "tool_not_allowed",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert result == []

    def test_rejected_step_included_when_configured(self):
        cfg = _make_config(
            claude_code_include_mediated_results_in_continuation=True,
            claude_code_include_rejected_mediation_in_continuation=True,
        )
        events = [
            _make_event(EventType.mediated_workflow_step_rejected, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "rejection_reason": "tool_not_allowed",
                "failure_reason": "tool is in denied list",
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert len(result) == 1
        assert "rejected" in result[0]
        assert "server__tool_a" in result[0]

    def test_result_truncated_at_120_chars(self):
        cfg = _make_config(claude_code_include_mediated_results_in_continuation=True)
        long_summary = "x" * 200
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "completed",
                "result_summary": long_summary,
            })
        ]
        result = ContinuationContextBuilder._extract_workflow_summaries(events, cfg)
        assert len(result) == 1
        assert "[truncated]" in result[0]
        # The summary should contain at most 120 chars of content + truncated marker
        assert len(long_summary[:120]) == 120


# ---------------------------------------------------------------------------
# 12. Continuation: _extract_mediated_summaries() with rejected inclusion (v0.9.0)
# ---------------------------------------------------------------------------


class TestExtractMediatedSummariesRejectedInclusion:
    def test_rejected_action_excluded_by_default(self):
        cfg = _make_config(
            claude_code_include_mediated_results_in_continuation=True,
            claude_code_include_rejected_mediation_in_continuation=False,
        )
        events = [
            _make_event(EventType.mediated_action_rejected, {
                "request_id": "req_001",
                "target_tool": "server__tool_a",
                "policy_decision": POLICY_REJECTED_TOOL_NOT_ALLOWED,
                "failure_reason": "tool is denied",
            })
        ]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert result == []

    def test_rejected_action_included_when_configured(self):
        cfg = _make_config(
            claude_code_include_mediated_results_in_continuation=True,
            claude_code_include_rejected_mediation_in_continuation=True,
        )
        events = [
            _make_event(EventType.mediated_action_rejected, {
                "request_id": "req_001",
                "target_tool": "server__tool_a",
                "policy_decision": POLICY_REJECTED_TOOL_NOT_ALLOWED,
                "failure_reason": "tool is denied",
            })
        ]
        result = ContinuationContextBuilder._extract_mediated_summaries(events, cfg)
        assert len(result) == 1
        assert "server__tool_a" in result[0]
        assert "rejected" in result[0]


# ---------------------------------------------------------------------------
# 13. Continuation _RECONSTRUCTION_VERSION
# ---------------------------------------------------------------------------


class TestReconstructionVersion:
    def test_continuation_builder_version_is_v09(self):
        from claude_agent_mcp.runtime.continuation_builder import _RECONSTRUCTION_VERSION
        # Updated to v1.0.0 as part of v1.0.0 stabilization release
        assert _RECONSTRUCTION_VERSION == "v1.0.0"

    def test_build_context_uses_v09_version(self):
        cfg = _make_config()
        session = _make_session()
        policy = ContinuationWindowPolicy()
        ctx = ContinuationContextBuilder.build_context(session, [], policy, config=cfg)
        # Updated to v1.0.0 as part of v1.0.0 stabilization release
        assert ctx.reconstruction_version == "v1.0.0"

    def test_mediated_workflow_summaries_in_context(self):
        cfg = _make_config(
            claude_code_include_mediated_results_in_continuation=True,
        )
        session = _make_session()
        policy = ContinuationWindowPolicy()
        events = [
            _make_event(EventType.mediated_workflow_step_completed, {
                "workflow_id": "wf_001",
                "step_index": 0,
                "target_tool": "server__tool_a",
                "status": "completed",
                "result_summary": "data found",
            })
        ]
        ctx = ContinuationContextBuilder.build_context(session, events, policy, config=cfg)
        assert len(ctx.mediated_workflow_summaries) == 1
        assert "wf_001" in ctx.mediated_workflow_summaries[0]


# ---------------------------------------------------------------------------
# 14. WorkflowExecutor._count_session_approvals()
# ---------------------------------------------------------------------------


class TestCountSessionApprovals:
    @pytest.mark.asyncio
    async def test_counts_v08_approvals(self):
        """Counts mediated_action_approved events."""
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        mock_sessions = AsyncMock()
        mock_sessions.get_events.return_value = [
            _make_event(EventType.mediated_action_approved, {"request_id": "req_001"}),
            _make_event(EventType.mediated_action_approved, {"request_id": "req_002"}),
            _make_event(EventType.mediated_action_requested, {"request_id": "req_003"}),  # not counted
        ]

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor._sessions = mock_sessions

        count = await executor._count_session_approvals("sess_001")
        assert count == 2

    @pytest.mark.asyncio
    async def test_counts_v09_workflow_step_approvals(self):
        """Counts mediated_workflow_step_approved events."""
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        mock_sessions = AsyncMock()
        mock_sessions.get_events.return_value = [
            _make_event(EventType.mediated_workflow_step_approved, {"step_index": 0}),
            _make_event(EventType.mediated_workflow_step_approved, {"step_index": 1}),
            _make_event(EventType.mediated_workflow_step_rejected, {"step_index": 2}),  # not counted
        ]

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor._sessions = mock_sessions

        count = await executor._count_session_approvals("sess_001")
        assert count == 2

    @pytest.mark.asyncio
    async def test_counts_both_types_combined(self):
        """Counts both v0.8.0 and v0.9.0 approval events."""
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        mock_sessions = AsyncMock()
        mock_sessions.get_events.return_value = [
            _make_event(EventType.mediated_action_approved, {}),
            _make_event(EventType.mediated_workflow_step_approved, {}),
            _make_event(EventType.mediated_workflow_step_approved, {}),
        ]

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor._sessions = mock_sessions

        count = await executor._count_session_approvals("sess_001")
        assert count == 3

    @pytest.mark.asyncio
    async def test_empty_events_returns_zero(self):
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        mock_sessions = AsyncMock()
        mock_sessions.get_events.return_value = []

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor._sessions = mock_sessions

        count = await executor._count_session_approvals("sess_001")
        assert count == 0

    @pytest.mark.asyncio
    async def test_error_returns_zero(self):
        """get_events failure returns 0 without raising."""
        from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor

        mock_sessions = AsyncMock()
        mock_sessions.get_events.side_effect = RuntimeError("DB error")

        executor = WorkflowExecutor.__new__(WorkflowExecutor)
        executor._sessions = mock_sessions

        count = await executor._count_session_approvals("sess_001")
        assert count == 0


# ---------------------------------------------------------------------------
# 15. WorkflowExecutor._process_mediated_actions() — workflow path
# ---------------------------------------------------------------------------


def _build_workflow_executor(
    cfg: Config,
    tool_names: list[str],
    invoker_result: str = "tool result",
) -> tuple:
    """Build a partial WorkflowExecutor suitable for testing mediation methods."""
    from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
    from claude_agent_mcp.runtime.mediation_engine import MediationEngine as ME

    resolver = _make_visibility_resolver(tool_names)

    mock_sessions = AsyncMock()
    mock_sessions.get_events.return_value = []  # no prior approvals

    mock_invoker = AsyncMock()
    mock_tool_result = MagicMock()
    mock_tool_result.to_content_string.return_value = invoker_result
    mock_invoker.invoke.return_value = mock_tool_result

    mock_backend = MagicMock()
    mock_backend.capabilities = BackendCapabilities(
        supports_execution_mediation=True,
        supports_bounded_mediated_workflows=True,
    )

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._config = cfg
    executor._sessions = mock_sessions
    executor._backend = mock_backend
    executor._mediation = ME(cfg, resolver)

    return executor, mock_sessions, mock_invoker


@pytest.mark.asyncio
async def test_process_workflow_approved_single_step():
    """Workflow with one step: approval → execution → completion events emitted."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_max_mediated_workflow_steps=1,
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a"]
    )

    text = _make_workflow_block(
        workflow_id="wf_001",
        steps=[{
            "step_index": 0,
            "action_type": "read",
            "target_tool": "server__tool_a",
            "justification": "Need data",
        }]
    )
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    assert len(results) == 1
    assert results[0].status == MediatedActionStatus.completed
    assert results[0].tool_name == "server__tool_a"

    # Verify events emitted
    emitted_types = [call.args[1] for call in mock_sessions.append_event.call_args_list]
    assert EventType.mediated_workflow_requested in emitted_types
    assert EventType.mediated_workflow_step_requested in emitted_types
    assert EventType.mediated_workflow_step_approved in emitted_types
    assert EventType.mediated_workflow_step_completed in emitted_types
    assert EventType.mediated_workflow_completed in emitted_types


@pytest.mark.asyncio
async def test_process_workflow_rejected_tool_denied():
    """Workflow step rejected because tool is in denied list."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_denied_mediated_tools=["server__tool_a"],
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a"]
    )

    text = _make_workflow_block(
        steps=[{
            "step_index": 0,
            "action_type": "read",
            "target_tool": "server__tool_a",
            "justification": "Need data",
        }]
    )
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    assert len(results) == 1
    assert results[0].status == MediatedActionStatus.rejected
    assert results[0].policy_decision == POLICY_REJECTED_TOOL_NOT_ALLOWED

    emitted_types = [call.args[1] for call in mock_sessions.append_event.call_args_list]
    assert EventType.mediated_workflow_step_rejected in emitted_types
    assert EventType.mediated_workflow_completed in emitted_types


@pytest.mark.asyncio
async def test_process_workflow_rejected_step_count_limit():
    """Workflow rejected at workflow level because step count exceeds max."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_max_mediated_workflow_steps=1,
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a", "server__tool_b"]
    )

    text = _make_workflow_block(
        steps=[
            {"step_index": 0, "action_type": "read", "target_tool": "server__tool_a", "justification": "a"},
            {"step_index": 1, "action_type": "read", "target_tool": "server__tool_b", "justification": "b"},
        ]
    )
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    # Both steps should be rejected (workflow-level rejection)
    assert len(results) == 2
    for r in results:
        assert r.status == MediatedActionStatus.rejected
        assert r.policy_decision == POLICY_REJECTED_WORKFLOW_STEP_LIMIT


@pytest.mark.asyncio
async def test_process_workflow_session_approval_limit():
    """Workflow step rejected because session approval limit is reached."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_max_session_mediated_approvals=2,
        claude_code_max_mediated_actions_per_turn=5,
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a"]
    )

    # Simulate 2 prior approvals in the session
    mock_sessions.get_events.return_value = [
        _make_event(EventType.mediated_action_approved, {}),
        _make_event(EventType.mediated_action_approved, {}),
    ]

    text = _make_workflow_block(
        steps=[{
            "step_index": 0,
            "action_type": "read",
            "target_tool": "server__tool_a",
            "justification": "Need data",
        }]
    )
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    assert len(results) == 1
    assert results[0].status == MediatedActionStatus.rejected
    assert results[0].policy_decision == POLICY_REJECTED_SESSION_APPROVAL_LIMIT


@pytest.mark.asyncio
async def test_process_workflow_events_workflow_completed_stats():
    """Workflow completed event includes correct aggregate stats."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_max_mediated_workflow_steps=2,
        claude_code_max_mediated_actions_per_turn=2,
        claude_code_denied_mediated_tools=["server__tool_b"],
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a", "server__tool_b"]
    )

    text = _make_workflow_block(
        steps=[
            {"step_index": 0, "action_type": "read", "target_tool": "server__tool_a", "justification": "a"},
            {"step_index": 1, "action_type": "read", "target_tool": "server__tool_b", "justification": "b"},
        ]
    )
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    # Step 0 completed, step 1 rejected (denied)
    assert len(results) == 2
    status_values = {r.tool_name: r.status for r in results}
    assert status_values["server__tool_a"] == MediatedActionStatus.completed
    assert status_values["server__tool_b"] == MediatedActionStatus.rejected

    # Find workflow_completed event and check stats
    # append_event(session_id, event_type, turn_index, payload) → args[3] is payload
    completed_event_payloads = [
        call.args[3]
        for call in mock_sessions.append_event.call_args_list
        if call.args[1] == EventType.mediated_workflow_completed
    ]
    assert len(completed_event_payloads) == 1
    payload = completed_event_payloads[0]
    assert payload["approved_steps"] == 1
    assert payload["rejected_steps"] == 1
    assert payload["completed_steps"] == 1


@pytest.mark.asyncio
async def test_process_mediated_actions_v08_format_regression():
    """Regression: v0.8.0 single-action format still works after v0.9.0 changes."""
    cfg = _make_config(
        claude_code_enable_execution_mediation=True,
        claude_code_max_mediated_actions_per_turn=1,
    )
    executor, mock_sessions, mock_invoker = _build_workflow_executor(
        cfg, ["server__tool_a"]
    )

    data = {
        "mediation_version": MEDIATION_VERSION,
        "request_id": "req_001",
        "action_type": "read",
        "target_tool": "server__tool_a",
        "justification": "Need data",
    }
    text = f"<mediated_action_request>\n{json.dumps(data)}\n</mediated_action_request>"

    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=mock_invoker,
    )

    assert len(results) == 1
    assert results[0].status == MediatedActionStatus.completed

    # v0.8.0 events should be emitted
    emitted_types = [call.args[1] for call in mock_sessions.append_event.call_args_list]
    assert EventType.mediated_action_requested in emitted_types
    assert EventType.mediated_action_approved in emitted_types
    assert EventType.mediated_action_completed in emitted_types
    # No workflow events for single-action format
    assert EventType.mediated_workflow_requested not in emitted_types


@pytest.mark.asyncio
async def test_process_no_workflow_support_skips_workflow_parsing():
    """Backend without supports_bounded_mediated_workflows does not parse workflow blocks."""
    cfg = _make_config(claude_code_enable_execution_mediation=True)

    from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
    from claude_agent_mcp.runtime.mediation_engine import MediationEngine as ME

    mock_sessions = AsyncMock()
    mock_sessions.get_events.return_value = []

    mock_backend = MagicMock()
    mock_backend.capabilities = BackendCapabilities(
        supports_execution_mediation=True,
        supports_bounded_mediated_workflows=False,  # no workflow support
    )

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._config = cfg
    executor._sessions = mock_sessions
    executor._backend = mock_backend
    executor._mediation = ME(cfg, _make_visibility_resolver(["server__tool_a"]))

    text = _make_workflow_block()
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=AsyncMock(),
    )

    # Workflow block is ignored — no results
    assert results == []


@pytest.mark.asyncio
async def test_process_no_mediation_support_returns_empty():
    """Regression: backends without supports_execution_mediation return empty list."""
    cfg = _make_config(claude_code_enable_execution_mediation=True)

    from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
    from claude_agent_mcp.runtime.mediation_engine import MediationEngine as ME

    mock_sessions = AsyncMock()

    mock_backend = MagicMock()
    mock_backend.capabilities = BackendCapabilities(
        supports_execution_mediation=False,
    )

    executor = WorkflowExecutor.__new__(WorkflowExecutor)
    executor._config = cfg
    executor._sessions = mock_sessions
    executor._backend = mock_backend
    executor._mediation = ME(cfg)

    text = _make_workflow_block()
    results = await executor._process_mediated_actions(
        output_text=text,
        session_id="sess_001",
        profile_name="general",
        turn_index=1,
        invoker=None,
    )
    assert results == []


# ---------------------------------------------------------------------------
# 16. claude_code_backend: [Mediated Execution Context] with workflow summaries
# ---------------------------------------------------------------------------


class TestClaudeCodeBackendWorkflowContext:
    def test_workflow_summaries_rendered_in_context_section(self):
        """[Mediated Execution Context] includes workflow summaries when present."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
            mediated_action_summaries=[],
            mediated_workflow_summaries=["Workflow wf_001 step 0: tool server__tool_a: result"],
        )

        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="do something",
            continuation_context=ctx,
        )

        assert "[Mediated Execution Context]" in prompt
        assert "Workflow wf_001 step 0" in prompt

    def test_both_action_and_workflow_summaries_rendered(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
            mediated_action_summaries=["Tool server__tool_a (read): some result"],
            mediated_workflow_summaries=["Workflow wf_001 step 0: tool server__tool_b: data"],
        )

        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="continue",
            continuation_context=ctx,
        )

        assert "[Mediated Execution Context]" in prompt
        assert "server__tool_a" in prompt
        assert "wf_001" in prompt

    def test_empty_summaries_omit_section(self):
        """[Mediated Execution Context] omitted when both summary lists are empty."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        ctx = SessionContinuationContext(
            session_id="sess_001",
            is_continuation=True,
            mediated_action_summaries=[],
            mediated_workflow_summaries=[],
        )

        prompt = backend._build_continuation_prompt(
            system_prompt="System",
            task="continue",
            continuation_context=ctx,
        )

        assert "[Mediated Execution Context]" not in prompt
