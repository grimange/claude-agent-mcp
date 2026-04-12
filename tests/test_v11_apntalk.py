"""Tests for v1.1.0 APNTalk restricted verification mode.

Covers:
- RuntimeMode enum values
- RuntimeRestrictionContract model fields and resolved APNTalk values
- Mode-aware MCP tool registration (actual registered surface)
- Forbidden tool absence in APNTalk mode
- Exact runtime proof fields in snapshot
- Fail-closed behavior: backend mismatch, transport mismatch, missing dirs
- Standard-mode regression: tool surface and snapshot fields unchanged
- Config mode field loading and validation
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_mcp.config import Config, VALID_MODES
from claude_agent_mcp.runtime.status_inspector import RuntimeStatusInspector, VERSION as INSPECTOR_VERSION
from claude_agent_mcp.server import (
    VERSION as SERVER_VERSION,
    TOOL_DEFINITIONS,
    _APNTALK_ADMITTED_TOOLS,
    _apntalk_startup_check,
    _build_apntalk_contract,
    build_server,
)
from claude_agent_mcp.types import (
    RuntimeMode,
    RuntimeRestrictionContract,
    RuntimeStatusSnapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**overrides) -> Config:
    """Build Config with clean env, optionally overriding specific vars."""
    clean_env = {k: v for k, v in os.environ.items() if "CLAUDE_AGENT" not in k}
    with patch.dict(os.environ, {**clean_env, **overrides}, clear=True):
        return Config()


def _make_apntalk_config(**overrides) -> Config:
    """Build a valid APNTalk config."""
    base = {
        "CLAUDE_AGENT_MCP_MODE": "apntalk_verification",
        "CLAUDE_AGENT_MCP_EXECUTION_BACKEND": "claude_code",
        "CLAUDE_AGENT_MCP_TRANSPORT": "stdio",
        "CLAUDE_AGENT_MCP_ALLOWED_DIRS": "/tmp/apntalk_test",
    }
    base.update(overrides)
    return _make_config(**base)


def _make_mock_server_deps():
    """Return lightweight mocks for build_server dependencies."""
    session_store = MagicMock()
    artifact_store = MagicMock()
    executor = MagicMock()
    return session_store, artifact_store, executor


# ---------------------------------------------------------------------------
# RuntimeMode enum
# ---------------------------------------------------------------------------


def test_runtime_mode_standard_value():
    assert RuntimeMode.standard == "standard"


def test_runtime_mode_apntalk_value():
    assert RuntimeMode.apntalk_verification == "apntalk_verification"


def test_runtime_mode_is_str_enum():
    for m in RuntimeMode:
        assert isinstance(m, str)


def test_runtime_mode_has_exactly_two_values():
    values = {m.value for m in RuntimeMode}
    assert values == {"standard", "apntalk_verification"}


# ---------------------------------------------------------------------------
# RuntimeRestrictionContract — APNTalk resolved values
# ---------------------------------------------------------------------------


def test_build_apntalk_contract_mode():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.mode == "apntalk_verification"


def test_build_apntalk_contract_policy_mode():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.policy_mode == "verification_only"


def test_build_apntalk_contract_authority_mode():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.authority_mode == "advisory_only"


def test_build_apntalk_contract_tool_surface_mode():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.tool_surface_mode == "restricted"


def test_build_apntalk_contract_active_profile():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.active_profile == "apntalk_verification"


def test_build_apntalk_contract_required_backend():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.required_backend == "claude_code"


def test_build_apntalk_contract_required_transport():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.required_transport == "stdio"


def test_build_apntalk_contract_allowed_tools_exact():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert set(contract.allowed_tools) == {"agent_get_runtime_status", "agent_verify_task"}


def test_build_apntalk_contract_restriction_contract_id():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.restriction_contract_id == "apntalk_verification_v1"


def test_build_apntalk_contract_restriction_contract_version():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.restriction_contract_version == 1


def test_build_apntalk_contract_fail_closed():
    contract = _build_apntalk_contract(["/tmp/test"])
    assert contract.fail_closed is True


def test_build_apntalk_contract_allowed_dirs_preserved():
    dirs = ["/home/user/project", "/tmp/evidence"]
    contract = _build_apntalk_contract(dirs)
    assert contract.allowed_directories == dirs


# ---------------------------------------------------------------------------
# 11.1 / 11.2 Tool-surface restriction — actual registered MCP tools
# ---------------------------------------------------------------------------


def _get_registered_tool_names(server) -> frozenset[str]:
    """Return the names of MCP tools actually registered on the server.

    Invokes the ListToolsRequest handler directly so we test the real
    server-level registration, not just config resolution.
    """
    import asyncio
    from mcp.types import ListToolsRequest

    handler = server.request_handlers[ListToolsRequest]
    result = asyncio.run(handler(ListToolsRequest(method="tools/list")))
    return frozenset(t.name for t in result.root.tools)


def test_apntalk_mode_server_publishes_only_admitted_tools():
    """Actual registered MCP tool list must equal the admitted pair exactly."""
    contract = _build_apntalk_contract(["/tmp/test"])
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=contract)

    registered_names = _get_registered_tool_names(server)
    assert registered_names == frozenset({"agent_get_runtime_status", "agent_verify_task"}), (
        f"Expected admitted pair, got: {registered_names}"
    )


def test_apntalk_mode_agent_run_task_absent():
    contract = _build_apntalk_contract(["/tmp/test"])
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=contract)
    assert "agent_run_task" not in _get_registered_tool_names(server)


def test_apntalk_mode_agent_continue_session_absent():
    contract = _build_apntalk_contract(["/tmp/test"])
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=contract)
    assert "agent_continue_session" not in _get_registered_tool_names(server)


def test_apntalk_mode_agent_get_session_absent():
    contract = _build_apntalk_contract(["/tmp/test"])
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=contract)
    assert "agent_get_session" not in _get_registered_tool_names(server)


def test_apntalk_mode_agent_list_sessions_absent():
    contract = _build_apntalk_contract(["/tmp/test"])
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=contract)
    assert "agent_list_sessions" not in _get_registered_tool_names(server)


def test_standard_mode_publishes_all_six_tools():
    """Standard mode must preserve the full v1.0.0 tool surface."""
    ss, art, exec_ = _make_mock_server_deps()
    server = build_server(ss, art, exec_, restriction_contract=None)
    registered_names = _get_registered_tool_names(server)
    assert registered_names == frozenset({
        "agent_run_task",
        "agent_continue_session",
        "agent_get_session",
        "agent_list_sessions",
        "agent_verify_task",
        "agent_get_runtime_status",
    })


# ---------------------------------------------------------------------------
# 11.3 Runtime proof exactness
# ---------------------------------------------------------------------------


def test_snapshot_restriction_proof_backend():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.backend == "claude_code"


def test_snapshot_restriction_proof_transport():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.transport == "stdio"


def test_snapshot_restriction_proof_active_profile():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.active_profile == "apntalk_verification"


def test_snapshot_restriction_proof_authority_mode():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.authority_mode == "advisory_only"


def test_snapshot_restriction_proof_tool_surface_mode():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.tool_surface_mode == "restricted"


def test_snapshot_restriction_proof_exposed_tools_exact():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    exposed = ["agent_get_runtime_status", "agent_verify_task"]
    snapshot = inspector.build_snapshot(
        restriction_contract=contract,
        exposed_tool_names=exposed,
    )
    assert set(snapshot.exposed_tools) == {"agent_get_runtime_status", "agent_verify_task"}


def test_snapshot_restriction_proof_allowed_directories():
    dirs = ["/tmp/apntalk_test"]
    config = _make_apntalk_config(CLAUDE_AGENT_MCP_ALLOWED_DIRS=dirs[0])
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.allowed_directories == dirs


def test_snapshot_restriction_contract_id():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.restriction_contract_id == "apntalk_verification_v1"


def test_snapshot_restriction_contract_version():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.restriction_contract_version == 1


def test_snapshot_restriction_fail_closed_enabled():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.fail_closed_enabled is True


def test_snapshot_restriction_compliance_true_when_satisfied():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    exposed = sorted(contract.allowed_tools)
    snapshot = inspector.build_snapshot(
        restriction_contract=contract,
        exposed_tool_names=exposed,
    )
    assert snapshot.restriction_compliance is True
    assert snapshot.non_compliance_reasons is None


def test_snapshot_restriction_compliance_false_on_extra_tools():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    # Simulate extra tool being registered (should not happen in practice)
    exposed = sorted(contract.allowed_tools) + ["agent_run_task"]
    snapshot = inspector.build_snapshot(
        restriction_contract=contract,
        exposed_tool_names=exposed,
    )
    assert snapshot.restriction_compliance is False
    assert snapshot.non_compliance_reasons is not None
    assert any("agent_run_task" in r for r in snapshot.non_compliance_reasons)


def test_snapshot_server_version_present_in_apntalk_mode():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.server_version == SERVER_VERSION


def test_snapshot_mode_field_is_apntalk_verification():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(restriction_contract=contract)
    assert snapshot.mode == "apntalk_verification"


# ---------------------------------------------------------------------------
# 11.4 Fail-closed behavior
# ---------------------------------------------------------------------------


def test_apntalk_startup_check_passes_with_valid_config():
    config = _make_apntalk_config()
    contract = _build_apntalk_contract(config.allowed_dirs)
    reasons = _apntalk_startup_check(config, contract)
    assert reasons == [], f"Unexpected non-compliance: {reasons}"


def test_apntalk_startup_check_fails_on_api_backend():
    config = _make_apntalk_config(CLAUDE_AGENT_MCP_EXECUTION_BACKEND="api")
    contract = _build_apntalk_contract(config.allowed_dirs)
    # Bypass validate() which would catch this first
    reasons = _apntalk_startup_check(config, contract)
    assert any("backend" in r for r in reasons), f"Expected backend reason, got: {reasons}"


def test_apntalk_startup_check_fails_on_streamable_http():
    # Build config with overridden transport (skip validate() errors)
    config = _make_config(
        CLAUDE_AGENT_MCP_MODE="apntalk_verification",
        CLAUDE_AGENT_MCP_EXECUTION_BACKEND="claude_code",
        CLAUDE_AGENT_MCP_ALLOWED_DIRS="/tmp/test",
    )
    config.transport = "streamable-http"  # Override post-construction
    contract = _build_apntalk_contract(config.allowed_dirs)
    reasons = _apntalk_startup_check(config, contract)
    assert any("transport" in r for r in reasons), f"Expected transport reason, got: {reasons}"


def test_apntalk_startup_check_fails_on_empty_allowed_dirs():
    config = _make_apntalk_config()
    contract = _build_apntalk_contract([])  # No allowed dirs
    reasons = _apntalk_startup_check(config, contract)
    assert any("allowed_directories" in r or "empty" in r for r in reasons), (
        f"Expected empty dirs reason, got: {reasons}"
    )


def test_config_validate_fails_if_apntalk_mode_with_api_backend():
    with pytest.raises(SystemExit) as exc_info:
        config = _make_config(
            CLAUDE_AGENT_MCP_MODE="apntalk_verification",
            CLAUDE_AGENT_MCP_EXECUTION_BACKEND="api",
            CLAUDE_AGENT_MCP_TRANSPORT="stdio",
        )
        config.validate()
    assert "apntalk_verification" in str(exc_info.value)
    assert "claude_code" in str(exc_info.value)


def test_config_validate_fails_if_apntalk_mode_with_streamable_http():
    with pytest.raises(SystemExit) as exc_info:
        config = _make_config(
            CLAUDE_AGENT_MCP_MODE="apntalk_verification",
            CLAUDE_AGENT_MCP_EXECUTION_BACKEND="claude_code",
            CLAUDE_AGENT_MCP_TRANSPORT="streamable-http",
            CLAUDE_AGENT_MCP_HOST="127.0.0.1",
            CLAUDE_AGENT_MCP_PORT="8000",
        )
        config.validate()
    assert "apntalk_verification" in str(exc_info.value)
    assert "stdio" in str(exc_info.value)


def test_config_validate_passes_for_valid_apntalk_config():
    config = _make_apntalk_config()
    # Should not raise
    config.validate()


def test_config_validate_fails_on_invalid_mode():
    with pytest.raises(SystemExit) as exc_info:
        config = _make_config(CLAUDE_AGENT_MCP_MODE="nonexistent_mode")
        config.validate()
    assert "nonexistent_mode" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 11.5 Standard-mode regression safety
# ---------------------------------------------------------------------------


def test_standard_mode_tool_definitions_unchanged():
    """TOOL_DEFINITIONS must still contain all six tools."""
    names = {t.name for t in TOOL_DEFINITIONS}
    assert names == {
        "agent_run_task",
        "agent_continue_session",
        "agent_get_session",
        "agent_list_sessions",
        "agent_verify_task",
        "agent_get_runtime_status",
    }


def test_standard_mode_snapshot_restriction_fields_are_none():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()  # no restriction_contract
    assert snapshot.mode == "standard"
    assert snapshot.policy_mode is None
    assert snapshot.authority_mode is None
    assert snapshot.tool_surface_mode is None
    assert snapshot.active_profile is None
    assert snapshot.exposed_tools is None
    assert snapshot.allowed_directories is None
    assert snapshot.restriction_contract_id is None
    assert snapshot.restriction_contract_version is None
    assert snapshot.fail_closed_enabled is None
    assert snapshot.restriction_compliance is None
    assert snapshot.non_compliance_reasons is None
    assert snapshot.server_version is None


def test_standard_mode_snapshot_core_fields_unchanged():
    config = _make_config()
    inspector = RuntimeStatusInspector(config)
    snapshot = inspector.build_snapshot()
    assert snapshot.version == INSPECTOR_VERSION
    assert snapshot.backend == "api"
    assert snapshot.transport == "stdio"
    assert isinstance(snapshot.capability_flags, dict)
    assert isinstance(snapshot.continuation_settings, dict)
    assert isinstance(snapshot.mediation_settings, dict)
    assert isinstance(snapshot.workflow_settings, dict)
    assert isinstance(snapshot.preserved_limitations, list)
    assert snapshot.resolved_at


def test_standard_mode_config_mode_default():
    config = _make_config()
    assert config.mode == "standard"


# ---------------------------------------------------------------------------
# Config mode field
# ---------------------------------------------------------------------------


def test_config_mode_loaded_from_env():
    config = _make_config(
        CLAUDE_AGENT_MCP_MODE="apntalk_verification",
        CLAUDE_AGENT_MCP_EXECUTION_BACKEND="claude_code",
        CLAUDE_AGENT_MCP_TRANSPORT="stdio",
    )
    assert config.mode == "apntalk_verification"


def test_config_mode_default_is_standard():
    config = _make_config()
    assert config.mode == "standard"


def test_valid_modes_set_contains_both():
    assert "standard" in VALID_MODES
    assert "apntalk_verification" in VALID_MODES


# ---------------------------------------------------------------------------
# Server VERSION
# ---------------------------------------------------------------------------


def test_server_version_is_1_1_0():
    assert SERVER_VERSION == "1.1.0"


def test_inspector_version_is_1_1_0():
    assert INSPECTOR_VERSION == "1.1.0"


# ---------------------------------------------------------------------------
# _APNTALK_ADMITTED_TOOLS constant
# ---------------------------------------------------------------------------


def test_apntalk_admitted_tools_exact():
    assert _APNTALK_ADMITTED_TOOLS == frozenset({
        "agent_get_runtime_status",
        "agent_verify_task",
    })


# ---------------------------------------------------------------------------
# RuntimeStatusSnapshot serialization with restriction fields
# ---------------------------------------------------------------------------


def test_snapshot_serializes_restriction_proof_fields():
    config = _make_apntalk_config()
    inspector = RuntimeStatusInspector(config)
    contract = _build_apntalk_contract(config.allowed_dirs)
    snapshot = inspector.build_snapshot(
        restriction_contract=contract,
        exposed_tool_names=sorted(contract.allowed_tools),
    )
    d = snapshot.model_dump()
    assert d["mode"] == "apntalk_verification"
    assert d["restriction_contract_id"] == "apntalk_verification_v1"
    assert d["restriction_contract_version"] == 1
    assert d["fail_closed_enabled"] is True
    assert d["restriction_compliance"] is True
    assert set(d["exposed_tools"]) == {"agent_get_runtime_status", "agent_verify_task"}
    assert d["server_version"] == SERVER_VERSION
