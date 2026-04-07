"""stdio transport bootstrap for claude-agent-mcp.

Routes all MCP communication through stdin/stdout, which is the default
and required transport for MCP host (e.g. Claude Desktop) integration.
"""

from __future__ import annotations

import mcp.server.stdio
from mcp.server.models import InitializationOptions

from claude_agent_mcp.logging import get_logger

logger = get_logger(__name__)

VERSION = "0.2.0"


async def run_stdio(server, session_store) -> None:
    """Run the MCP server over stdio transport.

    Args:
        server: The built MCP Server instance (from build_server()).
        session_store: The open SessionStore — will be closed in finally block
                       by the caller (server.py).
    """
    logger.info("Starting stdio transport")
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name="claude-agent-mcp",
                server_version=VERSION,
                capabilities=server.get_capabilities(
                    notification_options=None,
                    experimental_capabilities={},
                ),
            ),
        )
