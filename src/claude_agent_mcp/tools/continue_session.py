"""MCP tool handler for agent_continue_session."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import ValidationError
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import ContinueSessionRequest

logger = logging.getLogger(__name__)


async def handle_continue_session(
    executor: WorkflowExecutor, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        req = ContinueSessionRequest(**arguments)
    except Exception as exc:
        err = ValidationError(f"Invalid agent_continue_session request: {exc}")
        return {
            "ok": False,
            "session_id": arguments.get("session_id", ""),
            "status": "failed",
            "workflow": "continue_session",
            "profile": "general",
            "summary": err.message,
            "result": {},
            "artifacts": [],
            "warnings": [],
            "errors": [err.to_dict()],
        }

    response = await executor.continue_session(req)
    return response.model_dump(mode="json")
