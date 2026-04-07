"""Tests for v0.4 execution backend abstraction.

Covers:
- BackendRegistry selection and rejection of unknown names
- Config validation for execution_backend field
- ApiExecutionBackend startup validation
- ApiExecutionBackend execute() routing
- ClaudeCodeExecutionBackend startup validation
- Cross-backend execute() produces NormalizedProviderResult
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_mcp.backends import VALID_BACKENDS, build_backend
from claude_agent_mcp.backends.api_backend import ApiExecutionBackend
from claude_agent_mcp.backends.base import ExecutionBackend
from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend
from claude_agent_mcp.backends.registry import BackendRegistry
from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import (
    ClaudeCodeUnavailableError,
    ExecutionBackendAuthError,
    ExecutionBackendConfigError,
)
from claude_agent_mcp.types import NormalizedProviderResult


# ---------------------------------------------------------------------------
# Helpers
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
    return cfg


# ---------------------------------------------------------------------------
# VALID_BACKENDS constant
# ---------------------------------------------------------------------------


def test_valid_backends_contains_api():
    assert "api" in VALID_BACKENDS


def test_valid_backends_contains_claude_code():
    assert "claude_code" in VALID_BACKENDS


# ---------------------------------------------------------------------------
# BackendRegistry
# ---------------------------------------------------------------------------


def test_registry_get_registered_backend():
    cfg = _make_config()
    registry = BackendRegistry()
    backend = ApiExecutionBackend(cfg)
    registry.register(backend)
    assert registry.get("api") is backend


def test_registry_unknown_backend_raises():
    registry = BackendRegistry()
    with pytest.raises(ExecutionBackendConfigError, match="Unknown execution backend"):
        registry.get("unknown_backend")


def test_registry_names_returns_registered():
    cfg = _make_config()
    registry = BackendRegistry()
    registry.register(ApiExecutionBackend(cfg))
    assert "api" in registry.names()


# ---------------------------------------------------------------------------
# Config: execution_backend validation
# ---------------------------------------------------------------------------


def test_config_valid_api_backend():
    cfg = _make_config(execution_backend="api")
    # validate() must not raise
    cfg.validate()


def test_config_valid_claude_code_backend():
    cfg = _make_config(execution_backend="claude_code")
    cfg.validate()


def test_config_invalid_backend_raises():
    cfg = _make_config(execution_backend="grpc")
    with pytest.raises(SystemExit, match="CLAUDE_AGENT_MCP_EXECUTION_BACKEND"):
        cfg.validate()


# ---------------------------------------------------------------------------
# ApiExecutionBackend
# ---------------------------------------------------------------------------


class TestApiExecutionBackend:
    def test_name(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        assert backend.name == "api"

    def test_validate_startup_passes_with_key(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        backend = ApiExecutionBackend(cfg)
        backend.validate_startup(cfg)  # must not raise

    def test_validate_startup_fails_without_key(self):
        cfg = _make_config(anthropic_api_key="")
        backend = ApiExecutionBackend(cfg)
        with pytest.raises(ExecutionBackendAuthError, match="ANTHROPIC_API_KEY"):
            backend.validate_startup(cfg)

    def test_is_available_with_key(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        backend = ApiExecutionBackend(cfg)
        assert backend.is_available(cfg) is True

    def test_is_available_without_key(self):
        cfg = _make_config(anthropic_api_key="")
        backend = ApiExecutionBackend(cfg)
        assert backend.is_available(cfg) is False

    @pytest.mark.asyncio
    async def test_execute_without_tools_calls_run(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        expected = NormalizedProviderResult(
            output_text="Done", turn_count=1, stop_reason="end_turn"
        )
        backend._adapter.run = AsyncMock(return_value=expected)

        result = await backend.execute(
            system_prompt="sys",
            task="do it",
            max_turns=5,
        )

        backend._adapter.run.assert_called_once_with(
            system_prompt="sys",
            task="do it",
            max_turns=5,
            conversation_history=None,
        )
        assert result is expected

    @pytest.mark.asyncio
    async def test_execute_with_tools_calls_run_with_tools(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        expected = NormalizedProviderResult(
            output_text="Done with tools", turn_count=2, stop_reason="end_turn"
        )
        backend._adapter.run_with_tools = AsyncMock(return_value=expected)

        async def _tool_executor(name, inp):
            return "tool_result"

        tools = [{"name": "my_tool", "description": "test", "input_schema": {}}]
        result = await backend.execute(
            system_prompt="sys",
            task="do it",
            max_turns=5,
            tools=tools,
            tool_executor=_tool_executor,
        )

        backend._adapter.run_with_tools.assert_called_once()
        assert result is expected

    @pytest.mark.asyncio
    async def test_execute_with_empty_tools_calls_run(self):
        """Empty tools list should take the non-tool-use path."""
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        expected = NormalizedProviderResult(
            output_text="Done", turn_count=1, stop_reason="end_turn"
        )
        backend._adapter.run = AsyncMock(return_value=expected)

        result = await backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            tools=[],  # empty list — no tool-use loop
        )

        backend._adapter.run.assert_called_once()
        assert result is expected

    @pytest.mark.asyncio
    async def test_execute_continuation_passes_history(self):
        cfg = _make_config()
        backend = ApiExecutionBackend(cfg)
        expected = NormalizedProviderResult(
            output_text="Continued", turn_count=1, stop_reason="end_turn"
        )
        backend._adapter.run = AsyncMock(return_value=expected)

        history = [{"role": "user", "content": "first"}]
        result = await backend.execute(
            system_prompt="sys",
            task="second",
            max_turns=5,
            conversation_history=history,
        )

        backend._adapter.run.assert_called_once_with(
            system_prompt="sys",
            task="second",
            max_turns=5,
            conversation_history=history,
        )
        assert result is expected


# ---------------------------------------------------------------------------
# ClaudeCodeExecutionBackend
# ---------------------------------------------------------------------------


class TestClaudeCodeExecutionBackend:
    def test_name(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend.name == "claude_code"

    def test_validate_startup_fails_when_cli_not_found(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        with patch.object(backend, "_find_cli", return_value=None):
            with pytest.raises(ClaudeCodeUnavailableError, match="claude CLI not found"):
                backend.validate_startup(cfg)

    def test_validate_startup_fails_when_cli_returns_nonzero(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "error output"
        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            with pytest.raises(ClaudeCodeUnavailableError, match="version check failed"):
                backend.validate_startup(cfg)

    def test_validate_startup_passes_when_cli_available(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stderr = ""
        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            backend.validate_startup(cfg)  # must not raise

    def test_is_available_returns_false_when_cli_absent(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        with patch.object(backend, "_find_cli", return_value=None):
            assert backend.is_available(cfg) is False

    def test_is_available_returns_true_when_cli_present(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("subprocess.run", return_value=mock_result):
            assert backend.is_available(cfg) is True

    def test_find_cli_uses_configured_path(self, tmp_path):
        cli_path = tmp_path / "claude"
        cli_path.touch(mode=0o755)
        cfg = _make_config(claude_code_cli_path=str(cli_path))
        backend = ClaudeCodeExecutionBackend(cfg)
        assert backend._find_cli() == str(cli_path)

    def test_find_cli_configured_path_missing_falls_back_to_which(self, tmp_path):
        cfg = _make_config(claude_code_cli_path="/nonexistent/claude")
        backend = ClaudeCodeExecutionBackend(cfg)
        with patch("shutil.which", return_value="/usr/local/bin/claude"):
            result = backend._find_cli()
        assert result == "/usr/local/bin/claude"

    @pytest.mark.asyncio
    async def test_execute_returns_normalized_result(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"Task done successfully", b""))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys",
                task="do it",
                max_turns=5,
            )

        assert isinstance(result, NormalizedProviderResult)
        assert result.output_text == "Task done successfully"
        assert result.turn_count == 1
        assert result.stop_reason == "end_turn"

    @pytest.mark.asyncio
    async def test_execute_warns_on_tools(self):
        """Tools are not forwarded; a warning is returned instead."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

        async def _tool_exec(name, inp):
            return "result"

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                tools=[{"name": "t"}],
                tool_executor=_tool_exec,
            )

        assert any("federation" in w or "tools" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_execute_raises_on_nonzero_exit(self):
        from claude_agent_mcp.errors import ClaudeCodeInvocationError

        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)

        mock_proc = AsyncMock()
        mock_proc.returncode = 1
        mock_proc.communicate = AsyncMock(return_value=(b"", b"auth error"))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            with pytest.raises(ClaudeCodeInvocationError, match="code 1"):
                await backend.execute(
                    system_prompt="sys",
                    task="task",
                    max_turns=5,
                )

    def test_build_prompt_includes_system_and_task(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        prompt = backend._build_prompt("system text", "user task", None)
        assert "system text" in prompt
        assert "user task" in prompt

    def test_build_prompt_includes_history(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        history = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first response"},
        ]
        prompt = backend._build_prompt("sys", "second message", history)
        assert "first message" in prompt
        assert "first response" in prompt
        assert "second message" in prompt


# ---------------------------------------------------------------------------
# build_backend factory
# ---------------------------------------------------------------------------


def test_build_backend_api_succeeds_with_key():
    cfg = _make_config(execution_backend="api", anthropic_api_key="sk-test")
    backend = build_backend(cfg)
    assert backend.name == "api"


def test_build_backend_api_fails_without_key():
    cfg = _make_config(execution_backend="api", anthropic_api_key="")
    with pytest.raises(ExecutionBackendAuthError):
        build_backend(cfg)


def test_build_backend_claude_code_fails_without_cli():
    cfg = _make_config(execution_backend="claude_code")
    with patch("shutil.which", return_value=None):
        with pytest.raises(ClaudeCodeUnavailableError):
            build_backend(cfg)


def test_build_backend_rejects_unknown_backend():
    cfg = _make_config(execution_backend="grpc")
    with pytest.raises(ExecutionBackendConfigError, match="Unknown execution backend"):
        build_backend(cfg)


def test_build_backend_returns_execution_backend_instance():
    cfg = _make_config(execution_backend="api", anthropic_api_key="sk-test")
    backend = build_backend(cfg)
    assert isinstance(backend, ExecutionBackend)
