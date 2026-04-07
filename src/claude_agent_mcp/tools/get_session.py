"""MCP tool handler for agent_get_session."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import AgentMCPError, ValidationError
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.types import GetSessionRequest

logger = logging.getLogger(__name__)


async def handle_get_session(
    session_store: SessionStore, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        req = GetSessionRequest(**arguments)
    except Exception as exc:
        err = ValidationError(f"Invalid agent_get_session request: {exc}")
        return {"error": err.to_dict()}

    try:
        detail = await session_store.get_session_detail(req.session_id)
        return detail.model_dump(mode="json")
    except AgentMCPError as exc:
        return {"error": exc.to_dict()}
