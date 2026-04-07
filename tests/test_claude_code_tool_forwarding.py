"""Tests for v0.6 Claude Code limited tool forwarding and continuation prompt.

Covers:
- Tool compatibility screening (ToolScreenResult levels)
- Screen-tools splitting into compatible/incompatible sets
- Tool descriptions section formatting
- Tool forwarding behavior (disabled by default, enabled opt-in)
- Per-tool warnings for dropped tools
- Continuation vs. initial prompt framing
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from claude_agent_mcp.backends.claude_code_backend import (
    TOOL_MAX_TOP_LEVEL_PROPS,
    ClaudeCodeExecutionBackend,
    ToolCompatibilityLevel,
    ToolScreenResult,
)
from claude_agent_mcp.config import Config
from claude_agent_mcp.types import NormalizedProviderResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_config(**kwargs) -> Config:
    cfg = Config.__new__(Config)
    cfg.anthropic_api_key = ""
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
    cfg.execution_backend = "claude_code"
    cfg.claude_code_cli_path = kwargs.get("claude_code_cli_path", "")
    cfg.claude_code_timeout_seconds = kwargs.get("claude_code_timeout_seconds", 300)
    cfg.claude_code_enable_limited_tool_forwarding = kwargs.get(
        "claude_code_enable_limited_tool_forwarding", False
    )
    return cfg


def _simple_tool(name: str = "my_tool", description: str = "Does a thing") -> dict:
    return {
        "name": name,
        "description": description,
        "input_schema": {
            "type": "object",
            "properties": {
                "param_a": {"type": "string", "description": "First param"},
            },
            "required": ["param_a"],
        },
    }


# ---------------------------------------------------------------------------
# Tool screening — individual tool
# ---------------------------------------------------------------------------


def test_screen_tool_simple_schema_is_compatible():
    tool = _simple_tool()
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.compatible
    assert result.tool_name == "my_tool"


def test_screen_tool_with_ref_is_schema_unsupported():
    tool = {
        "name": "ref_tool",
        "description": "Tool with $ref",
        "input_schema": {
            "type": "object",
            "properties": {
                "item": {"$ref": "#/definitions/SomeType"},
            },
        },
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.schema_unsupported
    assert result.tool_name == "ref_tool"
    assert "$ref" in result.reason


def test_screen_tool_with_allof_is_schema_unsupported():
    tool = {
        "name": "allof_tool",
        "description": "Tool with allOf",
        "input_schema": {
            "allOf": [{"type": "object"}],
        },
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.schema_unsupported
    assert "allOf" in result.reason


def test_screen_tool_missing_description_is_filtered():
    tool = {
        "name": "no_desc_tool",
        "input_schema": {"type": "object", "properties": {}},
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.missing_description
    assert result.tool_name == "no_desc_tool"


def test_screen_tool_empty_description_is_filtered():
    tool = {
        "name": "empty_desc_tool",
        "description": "   ",
        "input_schema": {"type": "object", "properties": {}},
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.missing_description


def test_screen_tool_too_many_properties_is_complex():
    # Create a tool with TOOL_MAX_TOP_LEVEL_PROPS + 1 properties
    props = {f"param_{i}": {"type": "string"} for i in range(TOOL_MAX_TOP_LEVEL_PROPS + 1)}
    tool = {
        "name": "big_tool",
        "description": "Has many params",
        "input_schema": {
            "type": "object",
            "properties": props,
        },
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.complex_schema
    assert result.tool_name == "big_tool"


def test_screen_tool_at_max_properties_is_compatible():
    """A tool with exactly TOOL_MAX_TOP_LEVEL_PROPS properties is still compatible."""
    props = {f"param_{i}": {"type": "string"} for i in range(TOOL_MAX_TOP_LEVEL_PROPS)}
    tool = {
        "name": "exact_max_tool",
        "description": "At the limit",
        "input_schema": {
            "type": "object",
            "properties": props,
        },
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.compatible


def test_screen_tool_anyof_is_schema_unsupported():
    tool = {
        "name": "anyof_tool",
        "description": "Uses anyOf",
        "input_schema": {"anyOf": [{"type": "string"}, {"type": "number"}]},
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.schema_unsupported


def test_screen_tool_oneof_is_schema_unsupported():
    tool = {
        "name": "oneof_tool",
        "description": "Uses oneOf",
        "input_schema": {"oneOf": [{"type": "string"}]},
    }
    result = ClaudeCodeExecutionBackend.screen_tool(tool)
    assert result.level == ToolCompatibilityLevel.schema_unsupported


# ---------------------------------------------------------------------------
# Tool screening — batch
# ---------------------------------------------------------------------------


def test_screen_tools_splits_compatible_and_incompatible():
    tools = [
        _simple_tool("good_tool"),
        {"name": "bad_tool", "description": "Uses $ref", "input_schema": {"$ref": "#/X"}},
        {"name": "no_desc"},
    ]
    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools(tools)

    assert len(compatible) == 1
    assert compatible[0]["name"] == "good_tool"

    assert len(screened_out) == 2
    screened_names = {r.tool_name for r in screened_out}
    assert "bad_tool" in screened_names
    assert "no_desc" in screened_names


def test_screen_tools_all_compatible():
    tools = [_simple_tool("t1"), _simple_tool("t2")]
    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools(tools)
    assert len(compatible) == 2
    assert len(screened_out) == 0


def test_screen_tools_all_incompatible():
    tools = [
        {"name": "t1"},  # no description
        {"name": "t2", "description": "Uses $ref", "input_schema": {"$ref": "#/X"}},
    ]
    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools(tools)
    assert len(compatible) == 0
    assert len(screened_out) == 2


def test_screen_tools_empty_list():
    compatible, screened_out = ClaudeCodeExecutionBackend.screen_tools([])
    assert compatible == []
    assert screened_out == []


# ---------------------------------------------------------------------------
# Tool descriptions section formatting
# ---------------------------------------------------------------------------


def test_build_tool_descriptions_section_includes_tool_name():
    backend = ClaudeCodeExecutionBackend(_make_config())
    tools = [_simple_tool("my_tool", "Does a specific thing")]
    section = backend._build_tool_descriptions_section(tools)
    assert "my_tool" in section
    assert "[Available Tools]" in section


def test_build_tool_descriptions_section_includes_description():
    backend = ClaudeCodeExecutionBackend(_make_config())
    tools = [_simple_tool("my_tool", "Does a specific thing")]
    section = backend._build_tool_descriptions_section(tools)
    assert "Does a specific thing" in section


def test_build_tool_descriptions_section_includes_parameters():
    backend = ClaudeCodeExecutionBackend(_make_config())
    tool = {
        "name": "search_tool",
        "description": "Search for documents",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        },
    }
    section = backend._build_tool_descriptions_section([tool])
    assert "query" in section
    assert "limit" in section
    assert "required" in section
    assert "optional" in section


def test_build_tool_descriptions_section_states_not_invocable():
    backend = ClaudeCodeExecutionBackend(_make_config())
    section = backend._build_tool_descriptions_section([_simple_tool()])
    assert "not directly invocable" in section or "context only" in section


def test_build_tool_descriptions_section_multiple_tools():
    backend = ClaudeCodeExecutionBackend(_make_config())
    tools = [_simple_tool("tool_a", "Desc A"), _simple_tool("tool_b", "Desc B")]
    section = backend._build_tool_descriptions_section(tools)
    assert "tool_a" in section
    assert "tool_b" in section
    assert "Desc A" in section
    assert "Desc B" in section


# ---------------------------------------------------------------------------
# Tool forwarding behavior — disabled by default
# ---------------------------------------------------------------------------


def test_tool_forwarding_disabled_by_default_emits_single_warning():
    """When limited tool forwarding is disabled (default), a single consolidated warning is emitted."""
    cfg = _make_config(claude_code_enable_limited_tool_forwarding=False)
    backend = ClaudeCodeExecutionBackend(cfg)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    import asyncio

    async def run():
        with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
             patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            return await backend.execute(
                system_prompt="sys",
                task="task",
                max_turns=5,
                tools=[_simple_tool("t1"), _simple_tool("t2")],
            )

    result = asyncio.get_event_loop().run_until_complete(run())

    # Should have exactly one tools-related warning (consolidated), not per-tool
    tools_warnings = [w for w in result.warnings if "federation" in w.lower() or "not forwarded" in w.lower() or "not supported" in w.lower()]
    assert len(tools_warnings) == 1
    assert "api" in tools_warnings[0].lower()


@pytest.mark.asyncio
async def test_tool_forwarding_enabled_passes_compatible_tools_to_prompt():
    """When enabled, compatible tools appear in the prompt as text descriptions."""
    cfg = _make_config(claude_code_enable_limited_tool_forwarding=True)
    backend = ClaudeCodeExecutionBackend(cfg)

    captured_cmd: list = []

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    async def fake_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return mock_proc

    tool = _simple_tool("search_docs", "Search documentation")

    with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            tools=[tool],
        )

    full_cmd = " ".join(str(a) for a in captured_cmd)
    assert "search_docs" in full_cmd
    assert "[Available Tools]" in full_cmd


@pytest.mark.asyncio
async def test_tool_forwarding_enabled_emits_per_tool_warnings_for_dropped():
    """When enabled, each dropped tool gets its own warning."""
    cfg = _make_config(claude_code_enable_limited_tool_forwarding=True)
    backend = ClaudeCodeExecutionBackend(cfg)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    incompatible_tool = {
        "name": "complex_tool",
        "description": "Uses $ref",
        "input_schema": {"$ref": "#/definitions/X"},
    }
    compatible_tool = _simple_tool("simple_tool")

    with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            tools=[incompatible_tool, compatible_tool],
        )

    # Should have a per-tool warning for the dropped tool
    dropped_warnings = [w for w in result.warnings if "complex_tool" in w and "not forwarded" in w]
    assert len(dropped_warnings) == 1

    # Should NOT have a consolidated "all tools dropped" warning
    consolidated_warnings = [w for w in result.warnings if "federation" in w.lower() and "not supported" in w.lower()]
    assert len(consolidated_warnings) == 0


@pytest.mark.asyncio
async def test_tool_forwarding_enabled_all_incompatible_still_warns_per_tool():
    """When all tools are incompatible, each still gets its own warning."""
    cfg = _make_config(claude_code_enable_limited_tool_forwarding=True)
    backend = ClaudeCodeExecutionBackend(cfg)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    tools = [
        {"name": "t1", "description": "Uses $ref", "input_schema": {"$ref": "#/X"}},
        {"name": "t2"},  # no description
    ]

    with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
            tools=tools,
        )

    per_tool_warnings = [w for w in result.warnings if "not forwarded" in w]
    assert len(per_tool_warnings) == 2


@pytest.mark.asyncio
async def test_tool_forwarding_enabled_no_tools_passed_no_tool_warning():
    """When no tools are passed, no tool-forwarding warnings are emitted."""
    cfg = _make_config(claude_code_enable_limited_tool_forwarding=True)
    backend = ClaudeCodeExecutionBackend(cfg)

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec", return_value=mock_proc):
        result = await backend.execute(
            system_prompt="sys",
            task="task",
            max_turns=5,
        )

    tool_warnings = [w for w in result.warnings if "not forwarded" in w or "federation" in w.lower()]
    assert len(tool_warnings) == 0


# ---------------------------------------------------------------------------
# Continuation prompt framing (v0.6)
# ---------------------------------------------------------------------------


def test_continuation_prompt_uses_continuation_framing():
    """When is_continuation=True, the prompt uses [Continuation Session] framing."""
    backend = ClaudeCodeExecutionBackend(_make_config())
    prompt, _ = backend._build_structured_prompt(
        system_prompt="sys",
        task="continue task",
        conversation_history=None,
        session_summary="prior work",
        is_continuation=True,
    )
    assert "[Continuation Session]" in prompt
    assert "prior work" in prompt
    # Should NOT use the initial framing header
    assert "[Session Context]" not in prompt


def test_initial_prompt_uses_initial_framing():
    """When is_continuation=False (default), the prompt uses [Session Context] framing."""
    backend = ClaudeCodeExecutionBackend(_make_config())
    prompt, _ = backend._build_structured_prompt(
        system_prompt="sys",
        task="initial task",
        conversation_history=None,
        session_summary="prior work",
        is_continuation=False,
    )
    assert "[Session Context]" in prompt
    assert "prior work" in prompt
    assert "[Continuation Session]" not in prompt


def test_continuation_instructions_emphasize_resuming():
    """Continuation prompt [Instructions] section emphasizes resuming the session."""
    backend = ClaudeCodeExecutionBackend(_make_config())
    prompt, _ = backend._build_structured_prompt(
        system_prompt="sys",
        task="continue",
        conversation_history=None,
        session_summary=None,
        is_continuation=True,
    )
    assert "continuing" in prompt.lower() or "resuming" in prompt.lower() or "resume" in prompt.lower()


def test_initial_instructions_do_not_say_resuming():
    """Initial prompt [Instructions] section does not use continuation language."""
    backend = ClaudeCodeExecutionBackend(_make_config())
    prompt, _ = backend._build_structured_prompt(
        system_prompt="sys",
        task="initial task",
        conversation_history=None,
        session_summary=None,
        is_continuation=False,
    )
    # The initial instructions should reference "current request" not "continuing"
    assert "current request" in prompt.lower()


def test_no_session_summary_omits_context_section_in_continuation():
    """When there is no session summary, no context section appears even in continuation mode."""
    backend = ClaudeCodeExecutionBackend(_make_config())
    prompt, _ = backend._build_structured_prompt(
        system_prompt="sys",
        task="task",
        conversation_history=None,
        session_summary=None,
        is_continuation=True,
    )
    assert "[Continuation Session]" not in prompt
    assert "[Session Context]" not in prompt


@pytest.mark.asyncio
async def test_execute_is_continuation_true_uses_continuation_framing():
    """Passing is_continuation=True to execute() produces continuation framing in the CLI prompt."""
    cfg = _make_config()
    backend = ClaudeCodeExecutionBackend(cfg)

    captured_cmd: list = []

    mock_proc = AsyncMock()
    mock_proc.returncode = 0
    mock_proc.communicate = AsyncMock(return_value=(b"output", b""))

    async def fake_exec(*args, **kwargs):
        captured_cmd.extend(args)
        return mock_proc

    with patch.object(backend, "_find_cli", return_value="/usr/bin/claude"), \
         patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
        await backend.execute(
            system_prompt="sys",
            task="continue this",
            max_turns=5,
            session_summary="prior work summary",
            is_continuation=True,
        )

    full_cmd = " ".join(str(a) for a in captured_cmd)
    assert "[Continuation Session]" in full_cmd
    assert "[Session Context]" not in full_cmd
