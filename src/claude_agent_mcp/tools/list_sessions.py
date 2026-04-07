"""MCP tool handler for agent_list_sessions."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import AgentMCPError, ValidationError
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.types import ListSessionsRequest, ListSessionsResponse, SessionStatus

logger = logging.getLogger(__name__)


async def handle_list_sessions(
    session_store: SessionStore, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        req = ListSessionsRequest(**arguments)
    except Exception as exc:
        err = ValidationError(f"Invalid agent_list_sessions request: {exc}")
        return {"error": err.to_dict()}

    try:
        sessions = await session_store.list_sessions(
            limit=req.limit,
            status=req.status,
        )
        resp = ListSessionsResponse(sessions=sessions)
        return resp.model_dump(mode="json")
    except AgentMCPError as exc:
        return {"error": exc.to_dict()}
