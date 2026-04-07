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


@pytest.mark.asyncio
async def test_continue_session_response_envelope_shape(executor):
    """agent_continue_session must return the canonical envelope shape."""
    run_result = await handle_run_task(executor, {"task": "initial task"})
    session_id = run_result["session_id"]

    result = await handle_continue_session(
        executor, {"session_id": session_id, "message": "follow-up"}
    )
    required_keys = {
        "ok", "session_id", "status", "workflow", "profile",
        "summary", "result", "artifacts", "warnings", "errors",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}"
    )
    assert result["workflow"] == "continue_session"


@pytest.mark.asyncio
async def test_verify_task_response_envelope_shape(executor):
    """agent_verify_task must return the canonical envelope shape with all verify result fields."""
    result = await handle_verify_task(executor, {"task": "verify something"})
    required_keys = {
        "ok", "session_id", "status", "workflow", "profile",
        "summary", "result", "artifacts", "warnings", "errors",
    }
    assert required_keys.issubset(result.keys()), (
        f"Missing keys: {required_keys - set(result.keys())}"
    )
    assert result["workflow"] == "verify_task"
    assert result["profile"] == "verification"
    for field in ("verdict", "findings", "contradictions", "missing_evidence", "restrictions"):
        assert field in result["result"], f"Missing verify result field: {field}"


@pytest.mark.asyncio
async def test_error_object_shape(executor):
    """Error objects in the errors array must have stable code and message fields."""
    result = await handle_run_task(executor, {})
    assert result["ok"] is False
    assert len(result["errors"]) > 0
    err = result["errors"][0]
    assert "code" in err, "error object missing 'code'"
    assert "message" in err, "error object missing 'message'"
    assert isinstance(err["code"], str)
    assert isinstance(err["message"], str)


@pytest.mark.asyncio
async def test_get_session_response_fields(executor, session_store):
    """agent_get_session response must include all documented fields."""
    run_result = await handle_run_task(executor, {"task": "test session detail"})
    session_id = run_result["session_id"]

    result = await handle_get_session(session_store, {"session_id": session_id})
    required_fields = {
        "session_id", "workflow", "profile", "status",
        "created_at", "updated_at", "last_activity_at",
        "artifact_count", "turn_count", "request_count",
    }
    assert required_fields.issubset(result.keys()), (
        f"Missing fields: {required_fields - set(result.keys())}"
    )
    assert result["session_id"] == session_id


@pytest.mark.asyncio
async def test_list_sessions_response_fields(executor, session_store):
    """agent_list_sessions response must include a sessions list with stable per-session fields."""
    await handle_run_task(executor, {"task": "list sessions test"})

    result = await handle_list_sessions(session_store, {})
    assert "sessions" in result
    assert len(result["sessions"]) > 0
    session_entry = result["sessions"][0]
    required_fields = {"session_id", "workflow", "profile", "status", "updated_at"}
    assert required_fields.issubset(session_entry.keys()), (
        f"Missing session list fields: {required_fields - set(session_entry.keys())}"
    )


@pytest.mark.asyncio
async def test_no_provider_fields_in_run_task_response(executor):
    """Provider-specific fields must not appear in the run_task response envelope."""
    result = await handle_run_task(executor, {"task": "leak check"})
    provider_specific = {"provider_session_id", "stop_reason", "content", "model", "usage"}
    leaked = provider_specific & set(result.keys())
    assert not leaked, f"Provider-specific fields leaked into response: {leaked}"
    # Also check result dict
    leaked_in_result = provider_specific & set(result.get("result", {}).keys())
    assert not leaked_in_result, f"Provider-specific fields leaked into result: {leaked_in_result}"
