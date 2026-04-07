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
        # v0.5: CLI does not provide rich stop reasons; backend reports backend_defaulted
        assert result.stop_reason == "backend_defaulted"

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


# ---------------------------------------------------------------------------
# v0.5 — BackendCapabilities model
# ---------------------------------------------------------------------------


class TestBackendCapabilities:
    def test_api_backend_capabilities_flags(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        backend = ApiExecutionBackend(cfg)
        caps = backend.capabilities
        assert caps.supports_downstream_tools is True
        assert caps.supports_structured_tool_use is True
        assert caps.supports_native_multiturn is True
        assert caps.supports_rich_stop_reason is True
        assert caps.supports_structured_messages is True
        assert caps.supports_workspace_assumptions is False

    def test_claude_code_backend_capabilities_flags(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        caps = backend.capabilities
        assert caps.supports_downstream_tools is False
        assert caps.supports_structured_tool_use is False
        assert caps.supports_native_multiturn is False
        assert caps.supports_rich_stop_reason is False
        assert caps.supports_structured_messages is False
        assert caps.supports_workspace_assumptions is True

    def test_capabilities_are_frozen(self):
        """BackendCapabilities must be immutable (frozen dataclass)."""
        cfg = _make_config()
        caps = ClaudeCodeExecutionBackend(cfg).capabilities
        with pytest.raises(Exception):
            caps.supports_downstream_tools = True  # type: ignore[misc]

    def test_capabilities_differ_between_backends(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        api_caps = ApiExecutionBackend(cfg).capabilities
        cc_caps = ClaudeCodeExecutionBackend(cfg).capabilities
        assert api_caps != cc_caps


# ---------------------------------------------------------------------------
# v0.5 — Structured prompt reconstruction
# ---------------------------------------------------------------------------


class TestClaudeCodePromptBuilder:
    def setup_method(self):
        self.backend = ClaudeCodeExecutionBackend(_make_config())

    def test_prompt_includes_all_sections(self):
        prompt, _ = self.backend._build_structured_prompt(
            system_prompt="system instructions",
            task="do the thing",
            conversation_history=None,
            session_summary="prior summary",
        )
        assert "[System]" in prompt
        assert "system instructions" in prompt
        assert "[Session Context]" in prompt
        assert "prior summary" in prompt
        assert "[Current Request]" in prompt
        assert "do the thing" in prompt
        assert "[Instructions]" in prompt

    def test_prompt_omits_session_context_when_no_summary(self):
        prompt, _ = self.backend._build_structured_prompt(
            system_prompt="sys",
            task="task",
            conversation_history=None,
            session_summary=None,
        )
        assert "[Session Context]" not in prompt

    def test_prompt_includes_conversation_history_with_role_labels(self):
        history = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "first response"},
        ]
        prompt, _ = self.backend._build_structured_prompt(
            system_prompt="sys",
            task="second message",
            conversation_history=history,
            session_summary=None,
        )
        assert "[Conversation History]" in prompt
        assert "[User]" in prompt
        assert "[Assistant]" in prompt
        assert "first message" in prompt
        assert "first response" in prompt
        assert "second message" in prompt

    def test_prompt_structure_is_deterministic(self):
        """Same inputs always produce the same prompt."""
        history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        p1, _ = self.backend._build_structured_prompt("sys", "task", history, "summary")
        p2, _ = self.backend._build_structured_prompt("sys", "task", history, "summary")
        assert p1 == p2

    def test_history_not_truncated_within_limit(self):
        """History at or below limit is not truncated."""
        from claude_agent_mcp.backends.claude_code_backend import HISTORY_MAX_EXCHANGES
        history = []
        for i in range(HISTORY_MAX_EXCHANGES):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        _, truncated = self.backend._build_structured_prompt(
            "sys", "task", history, None
        )
        assert not truncated

    def test_history_truncated_beyond_limit(self):
        """History exceeding the limit is truncated and was_truncated=True."""
        from claude_agent_mcp.backends.claude_code_backend import HISTORY_MAX_EXCHANGES
        history = []
        for i in range(HISTORY_MAX_EXCHANGES + 5):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        _, truncated = self.backend._build_structured_prompt(
            "sys", "task", history, None
        )
        assert truncated

    def test_truncated_history_keeps_most_recent(self):
        """After truncation, the most recent messages are preserved."""
        from claude_agent_mcp.backends.claude_code_backend import HISTORY_MAX_EXCHANGES
        history = []
        for i in range(HISTORY_MAX_EXCHANGES + 3):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})
        prompt, _ = self.backend._build_structured_prompt("sys", "task", history, None)
        last_idx = HISTORY_MAX_EXCHANGES + 2
        assert f"q{last_idx}" in prompt
        assert f"a{last_idx}" in prompt

    def test_long_content_is_capped(self):
        """Individual message content exceeding CONTENT_MAX_CHARS is truncated."""
        from claude_agent_mcp.backends.claude_code_backend import CONTENT_MAX_CHARS
        long_content = "x" * (CONTENT_MAX_CHARS + 100)
        history = [{"role": "user", "content": long_content}]
        prompt, _ = self.backend._build_structured_prompt("sys", "task", history, None)
        assert "[truncated]" in prompt

    def test_build_prompt_legacy_alias_still_works(self):
        """_build_prompt legacy alias is preserved for backwards compatibility."""
        prompt = self.backend._build_prompt("sys", "task", None)
        assert "task" in prompt
        assert "sys" in prompt


# ---------------------------------------------------------------------------
# v0.5 — Normalization and warnings
# ---------------------------------------------------------------------------


class TestClaudeCodeNormalizationV5:
    @pytest.mark.asyncio
    async def test_stop_reason_is_backend_defaulted(self):
        """stop_reason must be 'backend_defaulted' (not 'end_turn') in v0.5."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys", task="task", max_turns=5
            )

        assert result.stop_reason == "backend_defaulted"

    @pytest.mark.asyncio
    async def test_stop_reason_warning_is_present(self):
        """A warning about stop_reason precision must always be present."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys", task="task", max_turns=5
            )

        assert any("stop_reason" in w for w in result.warnings)

    @pytest.mark.asyncio
    async def test_unsupported_tools_warning_references_api_backend(self):
        """Tools warning must tell operators to switch to 'api' backend."""
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

        assert any("api" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_empty_response_produces_warning(self):
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys", task="task", max_turns=5
            )

        assert result.output_text == ""
        assert any("empty" in w.lower() for w in result.warnings)

    @pytest.mark.asyncio
    async def test_session_summary_is_passed_to_prompt(self):
        """session_summary is embedded in the CLI prompt."""
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        captured_cmd: list = []

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))

        async def fake_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return mock_proc

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            await backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                session_summary="prior work summary",
            )

        full_cmd = " ".join(str(a) for a in captured_cmd)
        assert "prior work summary" in full_cmd

    @pytest.mark.asyncio
    async def test_truncation_warning_emitted_when_history_long(self):
        """Truncated history produces a warning in the result."""
        from claude_agent_mcp.backends.claude_code_backend import HISTORY_MAX_EXCHANGES
        cfg = _make_config()
        backend = ClaudeCodeExecutionBackend(cfg)
        history = []
        for i in range(HISTORY_MAX_EXCHANGES + 3):
            history.append({"role": "user", "content": f"q{i}"})
            history.append({"role": "assistant", "content": f"a{i}"})

        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))

        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                conversation_history=history,
            )

        assert any("truncat" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# v0.5 — API backend: session_summary parameter accepted without error
# ---------------------------------------------------------------------------


class TestApiBackendV5Compatibility:
    @pytest.mark.asyncio
    async def test_execute_accepts_session_summary_kwarg(self):
        """API backend execute() must accept session_summary without error."""
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
            session_summary="some prior context",
        )

        assert result is expected


# ---------------------------------------------------------------------------
# v0.6 — Cross-backend contract tests
# ---------------------------------------------------------------------------


class TestCrossBackendContractV6:
    """Verify that both backends satisfy the shared v0.6 contract."""

    def test_both_backends_have_capabilities_property(self):
        cfg_api = _make_config(anthropic_api_key="sk-test")
        cfg_cc = _make_config()
        api_caps = ApiExecutionBackend(cfg_api).capabilities
        cc_caps = ClaudeCodeExecutionBackend(cfg_cc).capabilities
        # Both must return a BackendCapabilities object
        from claude_agent_mcp.backends.base import BackendCapabilities
        assert isinstance(api_caps, BackendCapabilities)
        assert isinstance(cc_caps, BackendCapabilities)

    def test_api_backend_capabilities_is_frozen(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        caps = ApiExecutionBackend(cfg).capabilities
        with pytest.raises(Exception):
            caps.supports_downstream_tools = False  # type: ignore[misc]

    def test_claude_code_backend_capabilities_is_frozen(self):
        cfg = _make_config()
        caps = ClaudeCodeExecutionBackend(cfg).capabilities
        with pytest.raises(Exception):
            caps.supports_downstream_tools = True  # type: ignore[misc]

    def test_claude_code_backend_has_limited_downstream_tools_flag(self):
        cfg = _make_config()
        caps = ClaudeCodeExecutionBackend(cfg).capabilities
        assert caps.supports_limited_downstream_tools is True

    def test_api_backend_does_not_have_limited_downstream_tools_flag(self):
        """API backend has full tool support — limited flag should be False."""
        cfg = _make_config(anthropic_api_key="sk-test")
        caps = ApiExecutionBackend(cfg).capabilities
        assert caps.supports_limited_downstream_tools is False

    @pytest.mark.asyncio
    async def test_both_backends_accept_is_continuation_kwarg(self):
        """Both backends must accept is_continuation without TypeError."""
        # API backend
        cfg_api = _make_config(anthropic_api_key="sk-test")
        api_backend = ApiExecutionBackend(cfg_api)
        expected = NormalizedProviderResult(
            output_text="Done", turn_count=1, stop_reason="end_turn"
        )
        api_backend._adapter.run = AsyncMock(return_value=expected)
        result = await api_backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            is_continuation=True,
        )
        assert result is expected

        # Claude Code backend
        cfg_cc = _make_config()
        cc_backend = ClaudeCodeExecutionBackend(cfg_cc)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))
        with patch.object(cc_backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            cc_result = await cc_backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                is_continuation=True,
            )
        assert isinstance(cc_result, NormalizedProviderResult)

    @pytest.mark.asyncio
    async def test_both_backends_accept_session_summary_kwarg(self):
        """Both backends must accept session_summary without TypeError."""
        # API backend
        cfg_api = _make_config(anthropic_api_key="sk-test")
        api_backend = ApiExecutionBackend(cfg_api)
        expected = NormalizedProviderResult(
            output_text="Done", turn_count=1, stop_reason="end_turn"
        )
        api_backend._adapter.run = AsyncMock(return_value=expected)
        result = await api_backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            session_summary="prior context",
        )
        assert result is expected

        # Claude Code backend
        cfg_cc = _make_config()
        cc_backend = ClaudeCodeExecutionBackend(cfg_cc)
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate = AsyncMock(return_value=(b"done", b""))
        with patch.object(cc_backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            cc_result = await cc_backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                session_summary="prior context",
            )
        assert isinstance(cc_result, NormalizedProviderResult)

    def test_both_backends_have_name_property(self):
        cfg = _make_config(anthropic_api_key="sk-test")
        assert ApiExecutionBackend(cfg).name == "api"
        assert ClaudeCodeExecutionBackend(cfg).name == "claude_code"

    def test_both_backends_are_execution_backend_instances(self):
        from claude_agent_mcp.backends.base import ExecutionBackend
        cfg = _make_config(anthropic_api_key="sk-test")
        assert isinstance(ApiExecutionBackend(cfg), ExecutionBackend)
        assert isinstance(ClaudeCodeExecutionBackend(cfg), ExecutionBackend)
