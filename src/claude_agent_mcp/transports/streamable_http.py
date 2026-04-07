"""Streamable HTTP transport bootstrap for claude-agent-mcp.

Exposes the MCP server over HTTP using the MCP Streamable HTTP protocol
(POST /mcp with streaming SSE responses). Backed by Starlette + uvicorn.

This transport is intended for local operator deployment beyond stdio.
It is NOT multi-tenant and NOT hardened for public internet exposure.
Bind to 127.0.0.1 (default) unless you understand the trust implications.

Authentication is NOT implemented. If you expose this transport on a
non-loopback interface, you are responsible for access control.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Mount, Route

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from claude_agent_mcp.logging import get_logger

logger = get_logger(__name__)

VERSION = "0.2.0"


def build_starlette_app(server, *, stateless: bool = False) -> Starlette:
    """Build a Starlette ASGI app that wraps the MCP server.

    Args:
        server: The built MCP Server instance (from build_server()).
        stateless: If True, each HTTP request gets a fresh transport with no
                   session tracking. Suitable for simple one-shot clients.
                   Default (False) maintains per-HTTP-client MCP sessions.

    Returns:
        A Starlette ASGI application ready for uvicorn.
    """
    session_manager = StreamableHTTPSessionManager(
        app=server,
        stateless=stateless,
        json_response=False,
        # session_idle_timeout only applies to stateful (non-stateless) mode
        session_idle_timeout=None if stateless else 1800,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        logger.info("Streamable HTTP session manager starting")
        async with session_manager.run():
            yield
        logger.info("Streamable HTTP session manager stopped")

    async def handle_mcp(request: Request) -> Response:
        return await session_manager.handle_request(
            request.scope, request.receive, request._send
        )

    app = Starlette(
        lifespan=lifespan,
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ],
    )
    return app


async def run_streamable_http(server, *, host: str, port: int) -> None:
    """Run the MCP server over Streamable HTTP transport.

    Args:
        server: The built MCP Server instance (from build_server()).
        host: Bind host (default: 127.0.0.1).
        port: Bind port (default: 8000).
    """
    logger.info("Starting streamable-http transport on %s:%d", host, port)

    app = build_starlette_app(server, stateless=False)

    uv_config = uvicorn.Config(
        app=app,
        host=host,
        port=port,
        log_level="warning",  # uvicorn access logs suppressed; use our logger
        lifespan="on",
    )
    uv_server = uvicorn.Server(uv_config)
    await uv_server.serve()
