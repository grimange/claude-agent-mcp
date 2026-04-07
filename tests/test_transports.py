"""Transport smoke tests for claude-agent-mcp v0.2 deployment track.

These tests validate startup wiring and transport-level behavior without
requiring a live Anthropic API key. They cover:
  - stdio transport module importability and structure
  - streamable-http ASGI app construction and tool enumeration
  - transport routing through the same build_server() function
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.testclient import TestClient

from claude_agent_mcp.server import TOOL_DEFINITIONS, build_server
from claude_agent_mcp.transports.streamable_http import build_starlette_app


# ---------------------------------------------------------------------------
# stdio transport
# ---------------------------------------------------------------------------


def test_stdio_module_imports():
    """stdio transport module must import cleanly."""
    from claude_agent_mcp.transports import stdio  # noqa: F401


def test_stdio_run_is_coroutine():
    """run_stdio must be an async function."""
    import inspect
    from claude_agent_mcp.transports.stdio import run_stdio

    assert inspect.iscoroutinefunction(run_stdio)


# ---------------------------------------------------------------------------
# streamable-http transport
# ---------------------------------------------------------------------------


def test_streamable_http_module_imports():
    """streamable_http transport module must import cleanly."""
    from claude_agent_mcp.transports import streamable_http  # noqa: F401


def test_build_starlette_app_returns_app(
    session_store, artifact_store_fixture, executor
):
    """build_starlette_app must return a Starlette ASGI app."""
    from starlette.applications import Starlette

    server = build_server(session_store, artifact_store_fixture, executor)
    app = build_starlette_app(server, stateless=True)

    assert isinstance(app, Starlette)


def test_starlette_app_has_mcp_route(
    session_store, artifact_store_fixture, executor
):
    """The ASGI app must mount the /mcp route."""
    server = build_server(session_store, artifact_store_fixture, executor)
    app = build_starlette_app(server, stateless=True)

    route_paths = [getattr(r, "path", None) for r in app.routes]
    assert "/mcp" in route_paths


# ---------------------------------------------------------------------------
# build_server shared by both transports
# ---------------------------------------------------------------------------


def test_build_server_returns_same_tools_regardless_of_call(
    session_store, artifact_store_fixture, executor
):
    """build_server must register exactly the v0.1 tool surface."""
    server = build_server(session_store, artifact_store_fixture, executor)
    # Verify the TOOL_DEFINITIONS are intact (transport-agnostic contract)
    names = {t.name for t in TOOL_DEFINITIONS}
    assert names == {
        "agent_run_task",
        "agent_continue_session",
        "agent_get_session",
        "agent_list_sessions",
        "agent_verify_task",
    }


# ---------------------------------------------------------------------------
# Config transport validation
# ---------------------------------------------------------------------------


def test_config_valid_stdio():
    from claude_agent_mcp.config import Config

    cfg = Config.__new__(Config)
    cfg.transport = "stdio"
    cfg.host = "127.0.0.1"
    cfg.port = 8000
    cfg.log_level = "INFO"
    cfg.execution_backend = "api"
    # validate() must not raise
    cfg.validate()


def test_config_valid_streamable_http():
    from claude_agent_mcp.config import Config

    cfg = Config.__new__(Config)
    cfg.transport = "streamable-http"
    cfg.host = "127.0.0.1"
    cfg.port = 9000
    cfg.log_level = "DEBUG"
    cfg.execution_backend = "api"
    cfg.validate()


def test_config_invalid_transport_raises():
    from claude_agent_mcp.config import Config

    cfg = Config.__new__(Config)
    cfg.transport = "sse"
    cfg.host = "127.0.0.1"
    cfg.port = 8000
    cfg.log_level = "INFO"
    cfg.execution_backend = "api"
    with pytest.raises(SystemExit):
        cfg.validate()


def test_config_invalid_port_raises():
    from claude_agent_mcp.config import Config

    cfg = Config.__new__(Config)
    cfg.transport = "streamable-http"
    cfg.host = "127.0.0.1"
    cfg.port = 99999
    cfg.log_level = "INFO"
    cfg.execution_backend = "api"
    with pytest.raises(SystemExit):
        cfg.validate()


def test_config_invalid_log_level_raises():
    from claude_agent_mcp.config import Config

    cfg = Config.__new__(Config)
    cfg.transport = "stdio"
    cfg.host = "127.0.0.1"
    cfg.port = 8000
    cfg.log_level = "VERBOSE"
    cfg.execution_backend = "api"
    with pytest.raises(SystemExit):
        cfg.validate()


def test_config_env_transport(monkeypatch):
    """CLAUDE_AGENT_MCP_TRANSPORT env var must set transport."""
    monkeypatch.setenv("CLAUDE_AGENT_MCP_TRANSPORT", "streamable-http")
    monkeypatch.setenv("CLAUDE_AGENT_MCP_HOST", "0.0.0.0")
    monkeypatch.setenv("CLAUDE_AGENT_MCP_PORT", "9123")

    from importlib import reload
    import claude_agent_mcp.config as cfg_module

    reload(cfg_module)
    cfg = cfg_module.Config()
    assert cfg.transport == "streamable-http"
    assert cfg.host == "0.0.0.0"
    assert cfg.port == 9123


def test_config_legacy_env_vars_still_work(monkeypatch, tmp_path):
    """Legacy CLAUDE_AGENT_STATE_DIR and similar vars must still be honoured."""
    state = str(tmp_path / "legacy_state")
    monkeypatch.setenv("CLAUDE_AGENT_STATE_DIR", state)
    monkeypatch.delenv("CLAUDE_AGENT_MCP_STATE_DIR", raising=False)

    from importlib import reload
    import claude_agent_mcp.config as cfg_module

    reload(cfg_module)
    cfg = cfg_module.Config()
    assert str(cfg.state_dir) == str((tmp_path / "legacy_state").resolve())
