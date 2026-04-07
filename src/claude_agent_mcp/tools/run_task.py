"""MCP tool handler for agent_run_task."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import ValidationError
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import ProfileName, RunTaskRequest

logger = logging.getLogger(__name__)


async def handle_run_task(
    executor: WorkflowExecutor, arguments: dict[str, Any]
) -> dict[str, Any]:
    """Validate input and delegate to the workflow executor."""
    try:
        req = RunTaskRequest(**arguments)
    except Exception as exc:
        err = ValidationError(f"Invalid agent_run_task request: {exc}")
        return {
            "ok": False,
            "session_id": "",
            "status": "failed",
            "workflow": "run_task",
            "profile": arguments.get("system_profile", "general"),
            "summary": err.message,
            "result": {},
            "artifacts": [],
            "warnings": [],
            "errors": [err.to_dict()],
        }

    response = await executor.run_task(req)
    return response.model_dump(mode="json")
