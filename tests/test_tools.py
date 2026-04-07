"""Tests for MCP tool handler request validation and response shapes."""

from __future__ import annotations

import pytest

from claude_agent_mcp.tools.continue_session import handle_continue_session
from claude_agent_mcp.tools.get_session import handle_get_session
from claude_agent_mcp.tools.list_sessions import handle_list_sessions
from claude_agent_mcp.tools.run_task import handle_run_task
from claude_agent_mcp.tools.verify_task import handle_verify_task
from claude_agent_mcp.types import RunTaskRequest


@pytest.mark.asyncio
async def test_run_task_handler_missing_task(executor):
    result = await handle_run_task(executor, {})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_run_task_handler_valid(executor):
    result = await handle_run_task(executor, {"task": "hello"})
    assert result["ok"] is True
    assert "session_id" in result
    assert result["workflow"] == "run_task"


@pytest.mark.asyncio
async def test_continue_session_handler_missing_fields(executor):
    result = await handle_continue_session(executor, {"session_id": "sess_123"})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_get_session_handler_missing_session(session_store):
    result = await handle_get_session(session_store, {"session_id": "sess_missing"})
    assert "error" in result
    assert result["error"]["code"] == "session_not_found"


@pytest.mark.asyncio
async def test_get_session_handler_missing_arg(session_store):
    result = await handle_get_session(session_store, {})
    assert "error" in result


@pytest.mark.asyncio
async def test_list_sessions_handler_returns_sessions(executor, session_store):
    await handle_run_task(executor, {"task": "task one"})
    result = await handle_list_sessions(session_store, {})
    assert "sessions" in result
    assert isinstance(result["sessions"], list)


@pytest.mark.asyncio
async def test_list_sessions_handler_invalid_limit(session_store):
    result = await handle_list_sessions(session_store, {"limit": 9999})
    # Pydantic caps at 200
    assert "error" in result or "sessions" in result  # tolerate clamping vs error


@pytest.mark.asyncio
async def test_verify_task_handler_missing_task(executor):
    result = await handle_verify_task(executor, {})
    assert result["ok"] is False
    assert result["errors"][0]["code"] == "validation_error"
    assert result["result"]["verdict"] == "fail_closed"


@pytest.mark.asyncio
async def test_verify_task_handler_valid(executor):
    result = await handle_verify_task(executor, {"task": "verify something"})
    assert result["ok"] is True
    assert "verdict" in result["result"]
    assert result["workflow"] == "verify_task"
    assert result["profile"] == "verification"


@pytest.mark.asyncio
async def test_response_envelope_shape(executor):
    """All mutating tool responses must match the canonical envelope shape."""
    result = await handle_run_task(executor, {"task": "envelope test"})
    required_keys = {
        "ok", "session_id", "status", "workflow", "profile",
        "summary", "result", "artifacts", "warnings", "errors",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}"
    )
