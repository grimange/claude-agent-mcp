"""MCP tool handler for agent_verify_task."""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.errors import ValidationError
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import VerifyTaskRequest

logger = logging.getLogger(__name__)


async def handle_verify_task(
    executor: WorkflowExecutor, arguments: dict[str, Any]
) -> dict[str, Any]:
    try:
        req = VerifyTaskRequest(**arguments)
    except Exception as exc:
        err = ValidationError(f"Invalid agent_verify_task request: {exc}")
        return {
            "ok": False,
            "session_id": "",
            "status": "failed",
            "workflow": "verify_task",
            "profile": "verification",
            "summary": err.message,
            "result": {
                "verdict": "fail_closed",
                "findings": [],
                "contradictions": [],
                "missing_evidence": [],
                "restrictions": [],
            },
            "artifacts": [],
            "warnings": [],
            "errors": [err.to_dict()],
        }

    response = await executor.verify_task(req)
    return response.model_dump(mode="json")
