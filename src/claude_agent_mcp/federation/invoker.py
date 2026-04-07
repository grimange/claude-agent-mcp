"""Downstream tool invoker — bounded invocation layer for federation.

All downstream tool calls must flow through this layer. Direct calls from
provider output to external servers are not permitted.

Responsibilities:
- Validate the tool is in the visible set for the current session.
- Validate arguments against the tool's input schema where practical.
- Execute the downstream MCP call via the connection layer.
- Normalize success and error results into DownstreamToolCallResult.
- Record invocation and result events in the session transcript.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import (
    DownstreamInvocationError,
    DownstreamSchemaValidationError,
    DownstreamToolNotVisibleError,
)
from claude_agent_mcp.federation.connections import invoke_downstream_stdio
from claude_agent_mcp.federation.models import (
    DiscoveredTool,
    DownstreamServerConfig,
    DownstreamToolCallResult,
)
from claude_agent_mcp.federation.visibility import ToolVisibilityResolver
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.types import EventType, ProfileName

logger = logging.getLogger(__name__)


class DownstreamToolInvoker:
    """Bounded invocation layer for downstream MCP tools.

    The invoker is constructed per-session with the visible tool set and
    server configs already filtered for the active profile. It validates
    every call against that set and routes through the connection layer.
    """

    def __init__(
        self,
        visible_tools: list[DiscoveredTool],
        server_configs: list[DownstreamServerConfig],
        session_store: SessionStore,
    ) -> None:
        self._visible: dict[str, DiscoveredTool] = {
            t.normalized_name: t for t in visible_tools
        }
        # server_name -> config for routing invocations
        self._server_map: dict[str, DownstreamServerConfig] = {
            sc.name: sc for sc in server_configs
        }
        self._session_store = session_store

    async def invoke(
        self,
        normalized_name: str,
        tool_input: dict[str, Any],
        session_id: str,
        turn_index: int,
    ) -> DownstreamToolCallResult:
        """Invoke a downstream tool by normalized name.

        Validates visibility, executes via the connection layer, normalizes
        the result, and records invocation events in the session.

        Raises DownstreamToolNotVisibleError if the tool is not in the visible set.
        """
        tool = self._visible.get(normalized_name)
        if tool is None:
            raise DownstreamToolNotVisibleError(
                f"Tool {normalized_name!r} is not visible for this session",
                tool_name=normalized_name,
            )

        server_cfg = self._server_map.get(tool.downstream_server_name)
        if server_cfg is None:
            raise DownstreamInvocationError(
                f"No server config found for {tool.downstream_server_name!r}",
                tool_name=normalized_name,
            )

        # Basic schema validation if the tool has a schema
        if tool.input_schema:
            self._validate_args(tool, tool_input)

        # Record invocation start event
        await self._session_store.append_event(
            session_id,
            EventType.downstream_tool_invocation,
            turn_index,
            {
                "normalized_name": normalized_name,
                "server": tool.downstream_server_name,
                "downstream_name": tool.downstream_tool_name,
                # Only record input keys, not values, to avoid over-logging
                "input_keys": sorted(tool_input.keys()),
            },
        )

        logger.debug(
            "Invoking downstream tool %r on server %r (session %s, turn %d)",
            normalized_name,
            tool.downstream_server_name,
            session_id,
            turn_index,
        )

        call_result = await self._execute(server_cfg, tool, tool_input)

        # Record result event
        await self._session_store.append_event(
            session_id,
            EventType.downstream_tool_result,
            turn_index,
            {
                "normalized_name": normalized_name,
                "success": call_result.success,
                "error_message": call_result.error_message,
            },
        )

        return call_result

    async def _execute(
        self,
        server_cfg: DownstreamServerConfig,
        tool: DiscoveredTool,
        tool_input: dict[str, Any],
    ) -> DownstreamToolCallResult:
        """Execute the downstream call and normalize the result."""
        if server_cfg.transport == "stdio":
            return await self._execute_stdio(server_cfg, tool, tool_input)
        return DownstreamToolCallResult(
            tool_name=tool.normalized_name,
            success=False,
            error_message=f"Unsupported transport {server_cfg.transport!r}",
        )

    @staticmethod
    async def _execute_stdio(
        server_cfg: DownstreamServerConfig,
        tool: DiscoveredTool,
        tool_input: dict[str, Any],
    ) -> DownstreamToolCallResult:
        try:
            raw_result = await invoke_downstream_stdio(
                server_cfg,
                tool.downstream_tool_name,
                tool_input,
            )
            # Normalize MCP CallToolResult to a string-serializable content
            content = _extract_content(raw_result)
            return DownstreamToolCallResult(
                tool_name=tool.normalized_name,
                success=True,
                content=content,
            )
        except DownstreamInvocationError as exc:
            logger.warning(
                "Downstream tool %r invocation error: %s",
                tool.normalized_name,
                exc.message,
            )
            return DownstreamToolCallResult(
                tool_name=tool.normalized_name,
                success=False,
                error_message=exc.message,
            )
        except Exception as exc:
            logger.warning(
                "Unexpected error invoking downstream tool %r: %s",
                tool.normalized_name,
                exc,
            )
            return DownstreamToolCallResult(
                tool_name=tool.normalized_name,
                success=False,
                error_message=str(exc),
            )

    @staticmethod
    def _validate_args(tool: DiscoveredTool, tool_input: dict[str, Any]) -> None:
        """Basic required-field validation against the tool's input schema.

        This is a lightweight check — full JSON Schema validation is not
        implemented in v0.3.
        """
        schema = tool.input_schema
        required_fields: list[str] = schema.get("required", [])
        missing = [f for f in required_fields if f not in tool_input]
        if missing:
            raise DownstreamSchemaValidationError(
                f"Tool {tool.normalized_name!r} is missing required arguments: {missing}",
                tool_name=tool.normalized_name,
                missing_fields=missing,
            )


def _extract_content(raw_result: Any) -> Any:
    """Extract serializable content from a raw MCP CallToolResult."""
    # MCP CallToolResult has a .content list of content blocks
    content_blocks = getattr(raw_result, "content", None)
    if content_blocks is None:
        return str(raw_result) if raw_result is not None else ""

    parts: list[str] = []
    for block in content_blocks:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(str(text))
        elif hasattr(block, "model_dump"):
            import json
            parts.append(json.dumps(block.model_dump(), default=str))
        else:
            parts.append(str(block))

    return "\n".join(parts) if parts else ""


def build_invoker(
    profile: ProfileName,
    visibility_resolver: ToolVisibilityResolver,
    server_configs: list[DownstreamServerConfig],
    session_store: SessionStore,
) -> DownstreamToolInvoker:
    """Convenience factory: build an invoker for the given profile and session."""
    visible = visibility_resolver.resolve(profile)
    return DownstreamToolInvoker(
        visible_tools=visible,
        server_configs=server_configs,
        session_store=session_store,
    )
