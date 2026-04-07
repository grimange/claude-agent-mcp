"""Downstream MCP connection management.

Discovery occurs at startup — not per-request.
Each discovery opens a transient subprocess connection, discovers tools, then closes.
Invocations similarly open fresh connections to keep lifecycle management simple.

This is intentionally simple for v0.3. Persistent connection pooling may be
added in a future release.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_mcp.errors import DownstreamDiscoveryError, DownstreamInvocationError
from claude_agent_mcp.federation.models import DiscoveredTool, DownstreamServerConfig

logger = logging.getLogger(__name__)


class DownstreamConnectionManager:
    """Opens transient connections to downstream servers for discovery and invocation."""

    async def discover_all(
        self,
        servers: list[DownstreamServerConfig],
    ) -> list[DiscoveredTool]:
        """Discover tools from all supplied enabled servers.

        Individual server failures are logged and skipped — startup is not aborted.
        Returns a flat list of all successfully discovered tools.
        """
        all_tools: list[DiscoveredTool] = []

        for server in servers:
            try:
                tools = await asyncio.wait_for(
                    self._discover_server(server),
                    timeout=server.discovery_timeout_seconds,
                )
                all_tools.extend(tools)
                logger.info(
                    "Discovered %d tool(s) from downstream server %r",
                    len(tools),
                    server.name,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "Discovery timed out for downstream server %r (%.1fs) — skipping",
                    server.name,
                    server.discovery_timeout_seconds,
                )
            except DownstreamDiscoveryError as exc:
                logger.warning(
                    "Discovery failed for downstream server %r: %s — skipping",
                    server.name,
                    exc.message,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Unexpected discovery error for downstream server %r: %s — skipping",
                    server.name,
                    exc,
                )

        return all_tools

    async def _discover_server(
        self, server: DownstreamServerConfig
    ) -> list[DiscoveredTool]:
        if server.transport == "stdio":
            return await self._discover_stdio(server)
        raise DownstreamDiscoveryError(
            f"Unsupported transport {server.transport!r} for server {server.name!r}"
        )

    async def _discover_stdio(
        self, server: DownstreamServerConfig
    ) -> list[DiscoveredTool]:
        """Connect to a stdio downstream server and list its tools."""
        try:
            from mcp import ClientSession
            from mcp.client.stdio import StdioServerParameters, stdio_client
        except ImportError as exc:
            raise DownstreamDiscoveryError(
                f"MCP client library not available for stdio discovery: {exc}"
            ) from exc

        params = StdioServerParameters(
            command=server.command,
            args=server.args,
            env=server.env or None,
        )

        try:
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    response = await session.list_tools()
                    raw_tools = getattr(response, "tools", []) or []
                    return [self._normalize_tool(server, t) for t in raw_tools]
        except DownstreamDiscoveryError:
            raise
        except Exception as exc:
            raise DownstreamDiscoveryError(
                f"Failed to connect to downstream server {server.name!r}: {exc}"
            ) from exc

    @staticmethod
    def _normalize_tool(server: DownstreamServerConfig, tool: Any) -> DiscoveredTool:
        """Convert a raw MCP tool object into a DiscoveredTool with a normalized name."""
        downstream_name = getattr(tool, "name", None) or str(tool)
        normalized_name = f"{server.name}__{downstream_name}"

        # Extract input schema — handle both Pydantic models and plain dicts
        input_schema: dict[str, Any] = {}
        schema_raw = (
            getattr(tool, "inputSchema", None)
            or getattr(tool, "input_schema", None)
        )
        if schema_raw is not None:
            if hasattr(schema_raw, "model_dump"):
                input_schema = schema_raw.model_dump(exclude_none=True)
            elif isinstance(schema_raw, dict):
                input_schema = schema_raw

        return DiscoveredTool(
            downstream_server_name=server.name,
            downstream_tool_name=downstream_name,
            normalized_name=normalized_name,
            description=getattr(tool, "description", "") or "",
            input_schema=input_schema,
            allowed=False,  # set by catalog after allowlist filtering
            profiles_allowed=[],  # set by catalog
        )


async def invoke_downstream_stdio(
    server: DownstreamServerConfig,
    downstream_tool_name: str,
    tool_input: dict[str, Any],
) -> Any:
    """Invoke a named tool on a downstream stdio server and return raw result content.

    Opens a fresh connection per call. Raises DownstreamInvocationError on failure.
    """
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
    except ImportError as exc:
        raise DownstreamInvocationError(
            f"MCP client library not available for invocation: {exc}"
        ) from exc

    params = StdioServerParameters(
        command=server.command,
        args=server.args,
        env=server.env or None,
    )

    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(downstream_tool_name, tool_input)
                return result
    except DownstreamInvocationError:
        raise
    except Exception as exc:
        raise DownstreamInvocationError(
            f"Invocation of {downstream_tool_name!r} on server {server.name!r} failed: {exc}"
        ) from exc
