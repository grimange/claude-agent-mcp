"""Tests for workflow executor, policy engine, and profile registry."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.errors import PolicyDeniedError, ValidationError
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import (
    NormalizedProviderResult,
    ProfileName,
    RunTaskRequest,
    SessionStatus,
    WorkflowName,
)


# ---------------------------------------------------------------------------
# Profile registry
# ---------------------------------------------------------------------------


def test_profile_registry_returns_general():
    reg = ProfileRegistry()
    profile = reg.get(ProfileName.general)
    assert profile.name == ProfileName.general
    assert not profile.read_only


def test_profile_registry_returns_verification():
    reg = ProfileRegistry()
    profile = reg.get(ProfileName.verification)
    assert profile.name == ProfileName.verification
    assert profile.read_only
    assert profile.fail_closed


def test_profile_turns_capped(config):
    reg = ProfileRegistry()
    profile = reg.get(ProfileName.general)
    # Request over the max
    resolved = reg.resolve_turns(profile, 9999)
    assert resolved == profile.max_turns_max


# ---------------------------------------------------------------------------
# Policy engine
# ---------------------------------------------------------------------------


def test_policy_engine_allows_valid_dir(config, tmp_path):
    engine = PolicyEngine(config)
    profile = ProfileRegistry().get(ProfileName.general)
    # tmp_path is in config.allowed_dirs
    result = engine._resolve_working_directory(profile, str(tmp_path))
    assert result == str(tmp_path.resolve())


def test_policy_engine_blocks_outside_dir(config, tmp_path):
    engine = PolicyEngine(config)
    profile = ProfileRegistry().get(ProfileName.general)
    # /etc is not in allowed_dirs
    with pytest.raises(PolicyDeniedError):
        engine._resolve_working_directory(profile, "/etc")


def test_policy_engine_blocks_over_turn_cap(config):
    engine = PolicyEngine(config)
    profile = ProfileRegistry().get(ProfileName.general)
    with pytest.raises(PolicyDeniedError):
        engine._validate_turns(profile, profile.max_turns_max + 1)


def test_policy_engine_blocks_zero_turns(config):
    engine = PolicyEngine(config)
    profile = ProfileRegistry().get(ProfileName.general)
    with pytest.raises(ValidationError):
        engine._validate_turns(profile, 0)


# ---------------------------------------------------------------------------
# Workflow executor (with mock adapter)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_task_creates_session_and_returns_envelope(executor, session_store):
    req = RunTaskRequest(task="Say hello")
    response = await executor.run_task(req)

    assert response.ok is True
    assert response.session_id.startswith("sess_")
    assert response.status == SessionStatus.completed
    assert response.workflow == WorkflowName.run_task
    assert response.profile == ProfileName.general
    assert "output_text" in response.result


@pytest.mark.asyncio
async def test_run_task_persists_session(executor, session_store):
    req = RunTaskRequest(task="Do something")
    response = await executor.run_task(req)

    # Session should be retrievable after execution
    detail = await session_store.get_session_detail(response.session_id)
    assert detail.status == SessionStatus.completed


@pytest.mark.asyncio
async def test_run_task_policy_denied_outside_dir(executor, session_store):
    req = RunTaskRequest(task="Do something", working_directory="/etc/passwd")
    response = await executor.run_task(req)

    assert response.ok is False
    assert any(
        (e.code if hasattr(e, "code") else e.get("code")) == "policy_denied"
        for e in response.errors
    )


@pytest.mark.asyncio
async def test_continue_session_appends_to_transcript(executor, session_store):
    from claude_agent_mcp.types import ContinueSessionRequest

    run_req = RunTaskRequest(task="First task")
    run_resp = await executor.run_task(run_req)

    cont_req = ContinueSessionRequest(
        session_id=run_resp.session_id, message="Follow-up"
    )
    cont_resp = await executor.continue_session(cont_req)

    assert cont_resp.ok is True
    assert cont_resp.session_id == run_resp.session_id
    assert cont_resp.workflow == WorkflowName.continue_session


@pytest.mark.asyncio
async def test_continue_session_missing_session(executor):
    from claude_agent_mcp.types import ContinueSessionRequest

    req = ContinueSessionRequest(session_id="sess_nonexistent", message="hello")
    response = await executor.continue_session(req)

    assert response.ok is False


@pytest.mark.asyncio
async def test_continue_session_policy_denied_does_not_change_status(
    executor, session_store
):
    """A policy-denied continuation must not mark the session as failed.

    Simulate a session stuck in 'running' (e.g. concurrent execution) and
    verify that the policy denial leaves the session status unchanged.
    """
    from claude_agent_mcp.types import ContinueSessionRequest

    # Run a task to completion first
    run_req = RunTaskRequest(task="First task")
    run_resp = await executor.run_task(run_req)
    assert run_resp.ok is True

    # Manually force the session back to 'running' to simulate concurrent execution.
    # validate_continuation denies continuations of running sessions.
    await session_store.update_session(
        run_resp.session_id, status=SessionStatus.running
    )

    cont_req = ContinueSessionRequest(
        session_id=run_resp.session_id,
        message="Follow-up",
    )
    cont_resp = await executor.continue_session(cont_req)

    assert cont_resp.ok is False
    assert any(
        (e.code if hasattr(e, "code") else e.get("code")) == "policy_denied"
        for e in cont_resp.errors
    )

    # Session must remain 'running' — NOT changed to 'failed' by the policy denial
    detail_after = await session_store.get_session_detail(run_resp.session_id)
    assert detail_after.status == SessionStatus.running


@pytest.mark.asyncio
async def test_run_task_provider_error_marks_session_failed(
    config, session_store, tmp_path
):
    from claude_agent_mcp.runtime.agent_adapter import ClaudeAdapter
    from claude_agent_mcp.runtime.artifact_store import ArtifactStore
    from claude_agent_mcp.runtime.policy_engine import PolicyEngine
    from claude_agent_mcp.runtime.profile_registry import ProfileRegistry

    bad_adapter = MagicMock(spec=ClaudeAdapter)
    bad_adapter.run = AsyncMock(side_effect=Exception("API failure"))

    exec2 = WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=ArtifactStore(config, session_store.db),
        policy_engine=PolicyEngine(config),
        profile_registry=ProfileRegistry(),
        agent_adapter=bad_adapter,
    )

    req = RunTaskRequest(task="Fail me")
    response = await exec2.run_task(req)
    assert response.ok is False
    assert response.status == SessionStatus.failed
