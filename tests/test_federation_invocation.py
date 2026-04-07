"""Tests for federation invocation layer and session audit logging.

Uses mocked downstream connections and session store to stay deterministic.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_agent_mcp.errors import (
    DownstreamSchemaValidationError,
    DownstreamToolNotVisibleError,
)
from claude_agent_mcp.federation.catalog import ToolCatalog
from claude_agent_mcp.federation.invoker import DownstreamToolInvoker, build_invoker
from claude_agent_mcp.federation.models import (
    DiscoveredTool,
    DownstreamServerConfig,
    DownstreamToolCallResult,
)
from claude_agent_mcp.federation.visibility import ToolVisibilityResolver
from claude_agent_mcp.types import EventType, ProfileName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server(
    name: str = "fs_tools",
    allowed_tools: list[str] | None = None,
    profiles_allowed: list[str] | None = None,
) -> DownstreamServerConfig:
    return DownstreamServerConfig(
        name=name,
        transport="stdio",
        command="python",
        allowed_tools=allowed_tools or ["read_file"],
        profiles_allowed=profiles_allowed or ["general"],
    )


def _tool(
    server: str = "fs_tools",
    name: str = "read_file",
    schema: dict | None = None,
) -> DiscoveredTool:
    schema = schema or {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    }
    return DiscoveredTool(
        downstream_server_name=server,
        downstream_tool_name=name,
        normalized_name=f"{server}__{name}",
        description=f"Tool {name}",
        input_schema=schema,
        allowed=True,
        profiles_allowed=["general"],
    )


def _mock_session_store() -> MagicMock:
    store = MagicMock()
    store.append_event = AsyncMock()
    return store


def _make_invoker(
    visible_tools: list[DiscoveredTool],
    server_configs: list[DownstreamServerConfig],
) -> DownstreamToolInvoker:
    return DownstreamToolInvoker(
        visible_tools=visible_tools,
        server_configs=server_configs,
        session_store=_mock_session_store(),
    )


# ---------------------------------------------------------------------------
# DownstreamToolInvoker
# ---------------------------------------------------------------------------


class TestDownstreamToolInvoker:
    @pytest.mark.asyncio
    async def test_invisible_tool_raises(self):
        invoker = _make_invoker(visible_tools=[], server_configs=[_server()])
        with pytest.raises(DownstreamToolNotVisibleError):
            await invoker.invoke("fs_tools__read_file", {"path": "/tmp/f"}, "sess_1", 0)

    @pytest.mark.asyncio
    async def test_missing_required_arg_raises_schema_error(self):
        tool = _tool(schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        })
        invoker = _make_invoker(visible_tools=[tool], server_configs=[_server()])
        with pytest.raises(DownstreamSchemaValidationError, match="path"):
            await invoker.invoke("fs_tools__read_file", {}, "sess_1", 0)  # missing 'path'

    @pytest.mark.asyncio
    async def test_successful_invocation_returns_result(self):
        tool = _tool()
        invoker = _make_invoker(visible_tools=[tool], server_configs=[_server()])

        # Mock the stdio invocation
        with patch(
            "claude_agent_mcp.federation.invoker.invoke_downstream_stdio",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_result = MagicMock()
            mock_result.content = [MagicMock(text="file contents here")]
            mock_invoke.return_value = mock_result

            result = await invoker.invoke("fs_tools__read_file", {"path": "/tmp/f"}, "sess_1", 0)

        assert result.success is True
        assert "file contents here" in result.to_content_string()
        assert result.tool_name == "fs_tools__read_file"

    @pytest.mark.asyncio
    async def test_downstream_error_returns_failed_result(self):
        from claude_agent_mcp.errors import DownstreamInvocationError

        tool = _tool()
        invoker = _make_invoker(visible_tools=[tool], server_configs=[_server()])

        with patch(
            "claude_agent_mcp.federation.invoker.invoke_downstream_stdio",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_invoke.side_effect = DownstreamInvocationError("connection refused")

            result = await invoker.invoke("fs_tools__read_file", {"path": "/tmp/f"}, "sess_1", 0)

        assert result.success is False
        assert result.error_message is not None
        assert "connection refused" in result.error_message

    @pytest.mark.asyncio
    async def test_session_events_are_recorded(self):
        tool = _tool()
        store = _mock_session_store()
        invoker = DownstreamToolInvoker(
            visible_tools=[tool],
            server_configs=[_server()],
            session_store=store,
        )

        with patch(
            "claude_agent_mcp.federation.invoker.invoke_downstream_stdio",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_result = MagicMock()
            mock_result.content = [MagicMock(text="ok")]
            mock_invoke.return_value = mock_result

            await invoker.invoke("fs_tools__read_file", {"path": "/tmp/f"}, "sess_audit", 2)

        # Verify two events were appended: invocation + result
        assert store.append_event.call_count == 2
        calls = store.append_event.call_args_list
        event_types = [c.args[1] for c in calls]
        assert EventType.downstream_tool_invocation in event_types
        assert EventType.downstream_tool_result in event_types

    @pytest.mark.asyncio
    async def test_invocation_event_records_input_keys_not_values(self):
        """Invocation events should record only input keys to avoid over-logging secrets."""
        tool = _tool()
        store = _mock_session_store()
        invoker = DownstreamToolInvoker(
            visible_tools=[tool],
            server_configs=[_server()],
            session_store=store,
        )

        with patch(
            "claude_agent_mcp.federation.invoker.invoke_downstream_stdio",
            new_callable=AsyncMock,
        ) as mock_invoke:
            mock_result = MagicMock()
            mock_result.content = [MagicMock(text="ok")]
            mock_invoke.return_value = mock_result

            await invoker.invoke(
                "fs_tools__read_file",
                {"path": "/secret/path", "token": "supersecret"},
                "sess_1",
                0,
            )

        invocation_call = store.append_event.call_args_list[0]
        payload = invocation_call.args[3]
        assert "input_keys" in payload
        assert "path" in payload["input_keys"]
        assert "token" in payload["input_keys"]
        # Values must NOT be in the payload
        assert "/secret/path" not in str(payload)
        assert "supersecret" not in str(payload)


# ---------------------------------------------------------------------------
# build_invoker convenience factory
# ---------------------------------------------------------------------------


class TestBuildInvoker:
    def test_invoker_has_correct_visible_tools(self):
        server = _server(allowed_tools=["read_file"], profiles_allowed=["general"])
        tool = _tool(server="fs_tools", name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        resolver = ToolVisibilityResolver(catalog)
        store = _mock_session_store()

        invoker = build_invoker(
            profile=ProfileName.general,
            visibility_resolver=resolver,
            server_configs=[server],
            session_store=store,
        )
        # The visible tool must be registered
        assert "fs_tools__read_file" in invoker._visible

    def test_verification_profile_gets_no_tools_by_default(self):
        server = _server(allowed_tools=["read_file"], profiles_allowed=["general"])
        tool = _tool()
        catalog = ToolCatalog.build([tool], [server])
        resolver = ToolVisibilityResolver(catalog)
        store = _mock_session_store()

        invoker = build_invoker(
            profile=ProfileName.verification,
            visibility_resolver=resolver,
            server_configs=[server],
            session_store=store,
        )
        assert invoker._visible == {}


# ---------------------------------------------------------------------------
# DownstreamToolCallResult serialization
# ---------------------------------------------------------------------------


class TestDownstreamToolCallResult:
    def test_success_string_content(self):
        r = DownstreamToolCallResult(tool_name="t", success=True, content="hello")
        assert r.to_content_string() == "hello"

    def test_success_dict_content_serialized(self):
        r = DownstreamToolCallResult(tool_name="t", success=True, content={"key": "val"})
        assert '"key"' in r.to_content_string()

    def test_error_content_string(self):
        r = DownstreamToolCallResult(
            tool_name="t", success=False, error_message="connection refused"
        )
        assert "connection refused" in r.to_content_string()

    def test_none_content_gives_empty_string(self):
        r = DownstreamToolCallResult(tool_name="t", success=True, content=None)
        assert r.to_content_string() == ""
