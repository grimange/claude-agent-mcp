"""Tests for server-level wiring (tool registration, error handling)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.server import TOOL_DEFINITIONS


def test_tool_definitions_count():
    """v1.0.0 exposes the five v0.1 tools plus the additive status inspection tool."""
    names = {t.name for t in TOOL_DEFINITIONS}
    assert names == {
        "agent_run_task",
        "agent_continue_session",
        "agent_get_session",
        "agent_list_sessions",
        "agent_verify_task",
        "agent_get_runtime_status",
    }


def test_tool_definitions_have_schemas():
    for tool in TOOL_DEFINITIONS:
        assert tool.inputSchema is not None
        assert "properties" in tool.inputSchema or "type" in tool.inputSchema


def test_run_task_requires_task_field():
    run_task = next(t for t in TOOL_DEFINITIONS if t.name == "agent_run_task")
    assert "task" in run_task.inputSchema.get("required", [])


def test_continue_session_required_fields():
    cont = next(t for t in TOOL_DEFINITIONS if t.name == "agent_continue_session")
    required = cont.inputSchema.get("required", [])
    assert "session_id" in required
    assert "message" in required


def test_verify_task_requires_task_field():
    verify = next(t for t in TOOL_DEFINITIONS if t.name == "agent_verify_task")
    assert "task" in verify.inputSchema.get("required", [])
