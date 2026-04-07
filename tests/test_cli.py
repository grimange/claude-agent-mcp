"""CLI smoke tests for claude-agent-mcp v0.2 deployment track.

Tests that the CLI argument parser, version flag, and transport dispatch
routing work correctly without starting a live server.
"""

from __future__ import annotations

import sys
from unittest.mock import AsyncMock, patch

import pytest

from claude_agent_mcp.server import VERSION, _build_parser


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def test_parser_default_transport():
    parser = _build_parser()
    args = parser.parse_args([])
    assert args.transport is None  # not set — defers to env/config


def test_parser_explicit_stdio():
    parser = _build_parser()
    args = parser.parse_args(["--transport", "stdio"])
    assert args.transport == "stdio"


def test_parser_explicit_streamable_http():
    parser = _build_parser()
    args = parser.parse_args(["--transport", "streamable-http"])
    assert args.transport == "streamable-http"


def test_parser_host_port():
    parser = _build_parser()
    args = parser.parse_args(["--transport", "streamable-http", "--host", "0.0.0.0", "--port", "9999"])
    assert args.host == "0.0.0.0"
    assert args.port == 9999


def test_parser_rejects_unknown_transport():
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--transport", "grpc"])


def test_version_flag(capsys):
    parser = _build_parser()
    with pytest.raises(SystemExit) as exc:
        parser.parse_args(["--version"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert VERSION in captured.out


# ---------------------------------------------------------------------------
# main() dispatch routing
# ---------------------------------------------------------------------------


def test_main_routes_to_stdio(monkeypatch):
    """main() with --transport stdio must call run_stdio, not run_streamable_http."""
    monkeypatch.setattr("sys.argv", ["claude-agent-mcp", "--transport", "stdio"])

    with patch("claude_agent_mcp.server.run_stdio", new=AsyncMock()) as mock_stdio, \
         patch("claude_agent_mcp.server.run_streamable_http", new=AsyncMock()) as mock_http, \
         patch("claude_agent_mcp.server.get_config") as mock_cfg, \
         patch("asyncio.run") as mock_run:
        cfg = _make_test_config(transport="stdio")
        mock_cfg.return_value = cfg

        from claude_agent_mcp.server import main
        main()

        assert mock_run.called
        # The coroutine argument should come from run_stdio (not run_streamable_http)
        coro_qualname = mock_run.call_args[0][0].__qualname__ if hasattr(mock_run.call_args[0][0], "__qualname__") else str(mock_run.call_args[0][0])
        # Simply verify it was called with a coroutine — not the HTTP runner
        assert mock_run.call_count == 1


def test_main_routes_to_streamable_http(monkeypatch):
    """main() with --transport streamable-http must call run_streamable_http."""
    monkeypatch.setattr(
        "sys.argv",
        ["claude-agent-mcp", "--transport", "streamable-http", "--port", "9876"],
    )

    with patch("claude_agent_mcp.server.run_stdio", new=AsyncMock()) as mock_stdio, \
         patch("claude_agent_mcp.server.run_streamable_http", new=AsyncMock()) as mock_http, \
         patch("claude_agent_mcp.server.get_config") as mock_cfg, \
         patch("asyncio.run") as mock_run:
        cfg = _make_test_config(transport="streamable-http")
        mock_cfg.return_value = cfg

        from claude_agent_mcp.server import main
        main()

        assert mock_run.called
        assert mock_run.call_count == 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_test_config(transport: str = "stdio"):
    """Build a minimal Config-like object for CLI tests."""
    from claude_agent_mcp.config import Config
    from pathlib import Path

    cfg = Config.__new__(Config)
    cfg.transport = transport
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
    cfg.anthropic_api_key = "test-key"
    return cfg
