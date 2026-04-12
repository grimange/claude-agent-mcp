"""Tests for v1.0.0 stabilization, operator UX, and production hardening.

Covers:
- OperatorProfilePreset enum values and semantics (types)
- WarningCode enum values (types)
- RuntimeStatusSnapshot model (types)
- Operator profile preset defaults (_OPERATOR_PRESET_DEFAULTS) in config
- Config preset-awareness: safe_default, continuity_optimized, mediation_enabled, workflow_limited
- Preset precedence: individual env vars override preset defaults
- RuntimeStatusInspector.build_snapshot() — all fields present, defaults correct
- RuntimeStatusInspector.set_federation_active() — reflected in snapshot
- AuditPresenter.continuation_summary() — correct counts from event log
- AuditPresenter.mediation_summary() — single-action and workflow counts
- AuditPresenter.session_totals() — aggregates all subsystems
- AuditPresenter.workflow_summary() — provider calls, artifacts, errors
- AuditPresenter normalized warning format helpers
- server.py VERSION constant is "1.0.0"
- server.py TOOL_DEFINITIONS includes agent_get_runtime_status
- Backward compatibility: v0.1 tool names still present
- Reconstruction version updated to v1.0.0 in continuation_builder
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from claude_agent_mcp.config import Config, _OPERATOR_PRESET_DEFAULTS
from claude_agent_mcp.runtime.audit_presenter import AuditPresenter
from claude_agent_mcp.runtime.continuation_builder import _RECONSTRUCTION_VERSION
from claude_agent_mcp.runtime.status_inspector import (
    PRESERVED_LIMITATIONS,
    RuntimeStatusInspector,
    VERSION as INSPECTOR_VERSION,
)
from claude_agent_mcp.server import TOOL_DEFINITIONS, VERSION as SERVER_VERSION
from claude_agent_mcp.types import (
    EventType,
    OperatorProfilePreset,
    RuntimeStatusSnapshot,
    SessionContinuationContext,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    WarningCode,
    WorkflowName,
    ProfileName,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(
    event_type: EventType,
    payload: dict[str, Any] | None = None,
    turn_index: int = 0,
) -> SessionEventRecord:
    return SessionEventRecord(
        session_id="sess_test",
        event_type=event_type,
        turn_index=turn_index,
        payload=payload or {},
        created_at=datetime.now(tz=timezone.utc),
    )


def _make_session(
    session_id: str = "sess_test",
    status: SessionStatus = SessionStatus.completed,
    turn_count: int = 3,
    request_count: int = 2,
    artifact_count: int = 1,
) -> SessionRecord:
    now = datetime.now(tz=timezone.utc)
    return SessionRecord(
        session_id=session_id,
        workflow=WorkflowName.run_task,
        profile=ProfileName.general,
        provider="claude_code",
        status=status,
        created_at=now,
        updated_at=now,
        last_activity_at=now,
        request_count=request_count,
        turn_count=turn_count,
        artifact_count=artifact_count,
    )


def _make_config(**overrides) -> Config:
    """Build a Config with clean env, optionally setting specific env vars."""
    clean_env = {k: v for k, v in os.environ.items() if "CLAUDE_AGENT" not in k}
    with patch.dict(os.environ, {**clean_env, **overrides}, clear=True):
        from functools import lru_cache
        import claude_agent_mcp.config as cfg_module
        # Bypass the lru_cache by constructing directly
        return Config()


# ---------------------------------------------------------------------------
# OperatorProfilePreset enum
# ---------------------------------------------------------------------------


def test_operator_profile_preset_values():
    assert OperatorProfilePreset.safe_default == "safe_default"
    assert OperatorProfilePreset.continuity_optimized == "continuity_optimized"
    assert OperatorProfilePreset.mediation_enabled == "mediation_enabled"
    assert OperatorProfilePreset.workflow_limited == "workflow_limited"


def test_operator_profile_preset_is_str_enum():
    for preset in OperatorProfilePreset:
        assert isinstance(preset, str)


# ---------------------------------------------------------------------------
# WarningCode enum
# ---------------------------------------------------------------------------


def test_warning_code_values():
    expected = {
        "tool_downgrade",
        "tool_forwarding_incompatible",
        "history_truncated",
        "stop_reason_limited",
        "empty_response",
        "mediation_rejected",
        "federation_inactive_for_mediation",
        "continuation_context_truncated",
    }
    actual = {wc.value for wc in WarningCode}
    assert actual == expected


def test_warning_code_is_str_enum():
    for code in WarningCode:
        assert isinstance(code, str)


# ---------------------------------------------------------------------------
# RuntimeStatusSnapshot model
# ---------------------------------------------------------------------------


def test_runtime_status_snapshot_fields():
    snapshot = RuntimeStatusSnapshot(
        version="1.0.0",
        operator_profile_preset=None,
        backend="api",
        transport="stdio",
        model="claude-sonnet-4-6",
        federation_enabled=False,
        federation_active=False,
        capability_flags={},
        continuation_settings={},
        mediation_settings={},
        workflow_settings={},
        preserved_limitations=[],
        resolved_at="2026-04-08T00:00:00Z",
    )
    assert snapshot.version == "1.0.0"
    assert snapshot.backend == "api"
    assert snapshot.operator_profile_preset is None


def test_runtime_status_snapshot_serializes():
    snapshot = RuntimeStatusSnapshot(
        version="1.0.0",
        operator_profile_preset="safe_default",
        backend="claude_code",
        transport="stdio",
        model="claude-sonnet-4-6",
        federation_enabled=False,
        federation_active=False,
        capability_flags={"x": True},
        continuation_settings={"max_turns": 5},
        mediation_settings={"enabled": False},
        workflow_settings={"max_steps": 1},
        preserved_limitations=["No native tool_use"],
        resolved_at="2026-04-08T00:00:00Z",
    )
    d = snapshot.model_dump()
    assert d["version"] == "1.0.0"
    assert d["operator_profile_preset"] == "safe_default"
    assert d["capability_flags"]["x"] is True


# ---------------------------------------------------------------------------
# Operator preset defaults dictionary
# ---------------------------------------------------------------------------


def test_preset_defaults_all_presets_exist():
    for preset in OperatorProfilePreset:
        assert preset.value in _OPERATOR_PRESET_DEFAULTS, (
            f"Preset '{preset.value}' missing from _OPERATOR_PRESET_DEFAULTS"
        )


def test_safe_default_has_mediation_disabled():
    defaults = _OPERATOR_PRESET_DEFAULTS["safe_default"]
    assert defaults["enable_execution_mediation"] == "false"


def test_mediation_enabled_preset_turns_on_mediation():
    defaults = _OPERATOR_PRESET_DEFAULTS["mediation_enabled"]
    assert defaults["enable_execution_mediation"] == "true"


def test_workflow_limited_preset_has_bounded_steps():
    defaults = _OPERATOR_PRESET_DEFAULTS["workflow_limited"]
    assert defaults["enable_execution_mediation"] == "true"
    assert int(defaults["max_mediated_workflow_steps"]) >= 2


def test_continuity_optimized_has_longer_windows():
    cont_defaults = _OPERATOR_PRESET_DEFAULTS["continuity_optimized"]
    safe_defaults = _OPERATOR_PRESET_DEFAULTS["safe_default"]
    assert int(cont_defaults["max_continuation_turns"]) > int(
        safe_defaults["max_continuation_turns"]
    )


# ---------------------------------------------------------------------------
# Config preset-awareness
# ---------------------------------------------------------------------------


def test_config_no_preset_uses_hardcoded_defaults():
    config = _make_config()
    assert config.operator_profile_preset is None
    assert config.claude_code_enable_execution_mediation is False
    assert config.claude_code_max_continuation_turns == 5


def test_config_safe_default_preset_applied():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="safe_default")
    assert config.operator_profile_preset == "safe_default"
    assert config.claude_code_enable_execution_mediation is False
    assert config.claude_code_max_continuation_turns == 5
    assert config.claude_code_max_session_mediated_approvals == 10


def test_config_continuity_optimized_preset_applied():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="continuity_optimized")
    assert config.operator_profile_preset == "continuity_optimized"
    assert config.claude_code_enable_execution_mediation is False
    assert config.claude_code_max_continuation_turns == 10
    assert config.claude_code_max_continuation_warnings == 5


def test_config_mediation_enabled_preset_applied():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="mediation_enabled")
    assert config.operator_profile_preset == "mediation_enabled"
    assert config.claude_code_enable_execution_mediation is True
    assert config.claude_code_max_mediated_actions_per_turn == 3
    assert config.claude_code_include_mediated_results_in_continuation is True


def test_config_workflow_limited_preset_applied():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="workflow_limited")
    assert config.operator_profile_preset == "workflow_limited"
    assert config.claude_code_enable_execution_mediation is True
    assert config.claude_code_max_mediated_workflow_steps >= 2
    assert config.claude_code_max_session_mediated_approvals <= 50


def test_config_env_var_overrides_preset():
    """Individual env vars must take precedence over preset defaults."""
    config = _make_config(
        CLAUDE_AGENT_MCP_OPERATOR_PROFILE="mediation_enabled",
        CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION="false",
        CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS="7",
    )
    # Even though mediation_enabled preset turns mediation on, the explicit env var wins
    assert config.claude_code_enable_execution_mediation is False
    # Continuation turns also overridden
    assert config.claude_code_max_continuation_turns == 7


def test_config_unknown_preset_uses_hardcoded_defaults():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="nonexistent_preset")
    # Unknown preset: field should be set (not None) but no preset defaults applied
    assert config.operator_profile_preset == "nonexistent_preset"
    # Falls back to hardcoded defaults
    assert config.claude_code_max_continuation_turns == 5


# ---------------------------------------------------------------------------
# RuntimeStatusInspector
# ---------------------------------------------------------------------------


def test_status_inspector_build_snapshot_basic():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()

    assert snapshot.version == "1.1.0"
    assert snapshot.backend == "api"
    assert snapshot.transport == "stdio"
    assert snapshot.operator_profile_preset is None
    assert snapshot.federation_enabled is False
    assert snapshot.federation_active is False
    assert isinstance(snapshot.capability_flags, dict)
    assert isinstance(snapshot.continuation_settings, dict)
    assert isinstance(snapshot.mediation_settings, dict)
    assert isinstance(snapshot.workflow_settings, dict)
    assert isinstance(snapshot.preserved_limitations, list)
    assert len(snapshot.preserved_limitations) > 0
    assert snapshot.resolved_at  # non-empty ISO timestamp


def test_status_inspector_federation_active_reflects_set():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    inspector.set_federation_active(True)
    snapshot = inspector.build_snapshot()
    assert snapshot.federation_active is True


def test_status_inspector_reflects_preset():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="mediation_enabled")
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()
    assert snapshot.operator_profile_preset == "mediation_enabled"
    assert snapshot.mediation_settings["enabled"] is True


def test_status_inspector_capability_flags_include_mediation():
    config = _make_config(CLAUDE_AGENT_MCP_OPERATOR_PROFILE="mediation_enabled")
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()
    assert "execution_mediation_enabled" in snapshot.capability_flags
    assert snapshot.capability_flags["execution_mediation_enabled"] is True


def test_status_inspector_continuation_settings_keys():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()
    cont = snapshot.continuation_settings
    assert "max_continuation_turns" in cont
    assert "max_continuation_warnings" in cont
    assert "max_continuation_forwarding_events" in cont
    assert "include_verification_context" in cont
    assert "include_tool_downgrade_context" in cont


def test_status_inspector_workflow_settings_keys():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()
    wf = snapshot.workflow_settings
    assert "max_workflow_steps" in wf
    assert "allowed_tools" in wf
    assert "denied_tools" in wf
    assert "max_session_approvals" in wf
    assert "policy_profile" in wf


def test_status_inspector_preserved_limitations_non_empty():
    assert len(PRESERVED_LIMITATIONS) >= 3


def test_status_inspector_version_is_1_1_0():
    assert INSPECTOR_VERSION == "1.1.0"


def test_status_inspector_with_backend_capabilities():
    from claude_agent_mcp.backends.base import BackendCapabilities
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    caps = BackendCapabilities(
        supports_downstream_tools=True,
        supports_structured_tool_use=True,
        supports_native_multiturn=True,
        supports_rich_stop_reason=True,
        supports_structured_messages=True,
        supports_workspace_assumptions=False,
        supports_limited_downstream_tools=False,
        supports_structured_continuation_context=False,
        supports_continuation_window_policy=False,
        supports_execution_mediation=False,
        supports_mediated_action_results=False,
    )
    snapshot = inspector.build_snapshot(backend_capabilities=caps)
    flags = snapshot.capability_flags
    assert "backend_supports_downstream_tools" in flags
    assert flags["backend_supports_downstream_tools"] is True
    assert flags["backend_supports_native_multiturn"] is True


# ---------------------------------------------------------------------------
# AuditPresenter — continuation_summary
# ---------------------------------------------------------------------------


def test_continuation_summary_empty_events():
    result = AuditPresenter.continuation_summary([])
    assert result["total_continuation_calls"] == 0
    assert result["truncations_occurred"] == 0
    assert result["last_reconstruction_version"] is None


def test_continuation_summary_counts():
    events = [
        _make_event(
            EventType.session_continuation_context_built,
            {"reconstruction_version": "v1.0.0", "render_stats": {"turns_included": 3}},
        ),
        _make_event(EventType.session_continuation_context_truncated, {}),
        _make_event(
            EventType.session_continuation_prompt_rendered,
            {"reconstruction_version": "v1.0.0"},
        ),
        _make_event(
            EventType.session_continuation_context_built,
            {"reconstruction_version": "v1.0.0"},
        ),
    ]
    result = AuditPresenter.continuation_summary(events)
    assert result["total_continuation_calls"] == 2
    assert result["truncations_occurred"] == 1


def test_continuation_summary_last_version_from_rendered():
    events = [
        _make_event(
            EventType.session_continuation_context_built,
            {"reconstruction_version": "v0.9.0"},
        ),
        _make_event(
            EventType.session_continuation_prompt_rendered,
            {"reconstruction_version": "v1.0.0"},
        ),
    ]
    result = AuditPresenter.continuation_summary(events)
    assert result["last_reconstruction_version"] == "v1.0.0"


# ---------------------------------------------------------------------------
# AuditPresenter — mediation_summary
# ---------------------------------------------------------------------------


def test_mediation_summary_empty_events():
    result = AuditPresenter.mediation_summary([])
    sa = result["single_action"]
    wf = result["workflow"]
    assert sa["requested"] == 0
    assert sa["approved"] == 0
    assert sa["rejected"] == 0
    assert wf["requested"] == 0


def test_mediation_summary_single_action_counts():
    events = [
        _make_event(EventType.mediated_action_requested),
        _make_event(EventType.mediated_action_approved),
        _make_event(EventType.mediated_action_completed, {"status": "completed"}),
        _make_event(EventType.mediated_action_requested),
        _make_event(
            EventType.mediated_action_rejected,
            {"rejection_reason": "federation_inactive"},
        ),
    ]
    result = AuditPresenter.mediation_summary(events)
    sa = result["single_action"]
    assert sa["requested"] == 2
    assert sa["approved"] == 1
    assert sa["rejected"] == 1
    assert sa["completed"] == 1
    assert sa["rejection_reasons"]["federation_inactive"] == 1


def test_mediation_summary_workflow_counts():
    events = [
        _make_event(EventType.mediated_workflow_requested),
        _make_event(
            EventType.mediated_workflow_completed,
            {"approved_steps": 2, "rejected_steps": 1, "completed_steps": 2},
        ),
    ]
    result = AuditPresenter.mediation_summary(events)
    wf = result["workflow"]
    assert wf["requested"] == 1
    assert wf["completed"] == 1
    assert wf["total_steps_approved"] == 2
    assert wf["total_steps_rejected"] == 1


# ---------------------------------------------------------------------------
# AuditPresenter — workflow_summary
# ---------------------------------------------------------------------------


def test_workflow_summary_empty():
    result = AuditPresenter.workflow_summary([])
    assert result["provider_calls"] == 0
    assert result["artifact_emissions"] == 0
    assert result["error_events"] == 0
    assert result["policy_decisions"] == []


def test_workflow_summary_counts():
    events = [
        _make_event(EventType.provider_request_start),
        _make_event(EventType.provider_request_start),
        _make_event(EventType.artifact_emission),
        _make_event(EventType.error_event),
        _make_event(EventType.policy_decision, {"decision": "allowed"}),
    ]
    result = AuditPresenter.workflow_summary(events)
    assert result["provider_calls"] == 2
    assert result["artifact_emissions"] == 1
    assert result["error_events"] == 1
    assert result["policy_decisions"] == ["allowed"]


# ---------------------------------------------------------------------------
# AuditPresenter — session_totals
# ---------------------------------------------------------------------------


def test_session_totals_basic():
    session = _make_session(turn_count=5, request_count=3, artifact_count=2)
    events = [
        _make_event(EventType.session_continuation_context_built, {"reconstruction_version": "v1.0.0"}),
        _make_event(EventType.mediated_action_approved),
        _make_event(EventType.mediated_action_rejected, {"rejection_reason": "feature_disabled"}),
    ]
    result = AuditPresenter.session_totals(session, events)
    assert result["session_id"] == "sess_test"
    assert result["turn_count"] == 5
    assert result["request_count"] == 3
    assert result["artifact_count"] == 2
    assert result["continuation_calls"] == 1
    assert result["mediated_actions_approved"] == 1
    assert result["mediated_actions_rejected"] == 1


def test_session_totals_empty_events():
    session = _make_session()
    result = AuditPresenter.session_totals(session, [])
    assert result["continuation_calls"] == 0
    assert result["mediated_actions_approved"] == 0
    assert result["error_events"] == 0


# ---------------------------------------------------------------------------
# AuditPresenter — normalized warning formatters
# ---------------------------------------------------------------------------


def test_format_tool_downgrade_warning():
    msg = AuditPresenter.format_tool_downgrade_warning(3, "claude_code")
    assert "[tool_downgrade]" in msg
    assert "3" in msg
    assert "claude_code" in msg


def test_format_tool_forwarding_incompatible_warning():
    msg = AuditPresenter.format_tool_forwarding_incompatible_warning("my_tool", "complex schema")
    assert "[tool_forwarding_incompatible]" in msg
    assert "my_tool" in msg
    assert "complex schema" in msg


def test_format_history_truncated_warning():
    msg = AuditPresenter.format_history_truncated_warning(kept=5, omitted=3, max_exchanges=5)
    assert "[history_truncated]" in msg
    assert "5" in msg
    assert "3" in msg


def test_format_stop_reason_limited_warning():
    msg = AuditPresenter.format_stop_reason_limited_warning()
    assert "[stop_reason_limited]" in msg
    assert "backend_defaulted" in msg


def test_format_empty_response_warning():
    msg = AuditPresenter.format_empty_response_warning()
    assert "[empty_response]" in msg


def test_format_mediation_rejected_warning():
    msg = AuditPresenter.format_mediation_rejected_warning(
        request_id="req_001",
        tool_name="my_tool",
        rejection_reason="per_turn_limit_exceeded",
        policy_decision="rejected:per_turn_action_limit_exceeded",
    )
    assert "[mediation_rejected]" in msg
    assert "req_001" in msg
    assert "my_tool" in msg
    assert "per_turn_limit_exceeded" in msg


def test_format_federation_inactive_warning():
    msg = AuditPresenter.format_federation_inactive_warning("req_001", "my_tool")
    assert "[federation_inactive]" in msg
    assert "req_001" in msg
    assert "federation" in msg


# ---------------------------------------------------------------------------
# Server version and tools — backward compatibility
# ---------------------------------------------------------------------------


def test_server_version_is_1_1_1():
    assert SERVER_VERSION == "1.1.1"


def test_v01_tool_contracts_preserved():
    """All five v0.1 MCP tool contracts must remain present and unchanged."""
    names = {t.name for t in TOOL_DEFINITIONS}
    v01_tools = {
        "agent_run_task",
        "agent_continue_session",
        "agent_get_session",
        "agent_list_sessions",
        "agent_verify_task",
    }
    assert v01_tools.issubset(names), (
        f"Missing v0.1 tools: {v01_tools - names}"
    )


def test_agent_get_runtime_status_is_additive():
    """agent_get_runtime_status is present and additive — does not affect v0.1 tools."""
    names = {t.name for t in TOOL_DEFINITIONS}
    assert "agent_get_runtime_status" in names


def test_run_task_required_fields_unchanged():
    run_task = next(t for t in TOOL_DEFINITIONS if t.name == "agent_run_task")
    assert "task" in run_task.inputSchema.get("required", [])


def test_continue_session_required_fields_unchanged():
    cont = next(t for t in TOOL_DEFINITIONS if t.name == "agent_continue_session")
    required = cont.inputSchema.get("required", [])
    assert "session_id" in required
    assert "message" in required


# ---------------------------------------------------------------------------
# Continuation builder reconstruction version
# ---------------------------------------------------------------------------


def test_reconstruction_version_updated_to_v1_0_0():
    assert _RECONSTRUCTION_VERSION == "v1.0.0"


def test_session_continuation_context_default_version():
    ctx = SessionContinuationContext(session_id="sess_x", is_continuation=True)
    assert ctx.reconstruction_version == "v1.0.0"
