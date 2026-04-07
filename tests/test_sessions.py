"""Tests for session persistence, locking, and lifecycle."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from claude_agent_mcp.errors import SessionConflictError, SessionNotFoundError
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.types import EventType, ProfileName, SessionStatus, WorkflowName


@pytest.mark.asyncio
async def test_create_session(session_store: SessionStore):
    session = await session_store.create_session(
        workflow=WorkflowName.run_task,
        profile=ProfileName.general,
    )
    assert session.session_id.startswith("sess_")
    assert session.status == SessionStatus.created
    assert session.workflow == WorkflowName.run_task
    assert session.profile == ProfileName.general


@pytest.mark.asyncio
async def test_get_session_not_found(session_store: SessionStore):
    with pytest.raises(SessionNotFoundError):
        await session_store.get_session("sess_nonexistent")


@pytest.mark.asyncio
async def test_update_session_status(session_store: SessionStore):
    session = await session_store.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    await session_store.update_session(
        session.session_id, status=SessionStatus.completed, summary_latest="done"
    )
    updated = await session_store.get_session(session.session_id)
    assert updated.status == SessionStatus.completed
    assert updated.summary_latest == "done"


@pytest.mark.asyncio
async def test_list_sessions(session_store: SessionStore):
    for _ in range(3):
        await session_store.create_session(WorkflowName.run_task, ProfileName.general)

    sessions = await session_store.list_sessions(limit=10)
    assert len(sessions) == 3


@pytest.mark.asyncio
async def test_list_sessions_status_filter(session_store: SessionStore):
    s1 = await session_store.create_session(WorkflowName.run_task, ProfileName.general)
    s2 = await session_store.create_session(WorkflowName.run_task, ProfileName.general)
    await session_store.update_session(s1.session_id, status=SessionStatus.completed)

    completed = await session_store.list_sessions(status=SessionStatus.completed)
    assert len(completed) == 1
    assert completed[0].session_id == s1.session_id


@pytest.mark.asyncio
async def test_session_event_log(session_store: SessionStore):
    session = await session_store.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    await session_store.append_event(
        session.session_id, EventType.user_input, 0, {"task": "hello"}
    )
    await session_store.append_event(
        session.session_id, EventType.provider_response_summary, 1, {"summary": "done"}
    )

    events = await session_store.get_events(session.session_id)
    assert len(events) == 2
    assert events[0].event_type == EventType.user_input
    assert events[0].payload["task"] == "hello"
    assert events[1].event_type == EventType.provider_response_summary


@pytest.mark.asyncio
async def test_session_lock_acquire_release(session_store: SessionStore):
    session = await session_store.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    sid = session.session_id

    await session_store.acquire_lock(sid, "owner_a")
    rec = await session_store.get_session(sid)
    assert rec.locked_by == "owner_a"

    await session_store.release_lock(sid, "owner_a")
    rec = await session_store.get_session(sid)
    assert rec.locked_by is None


@pytest.mark.asyncio
async def test_session_lock_conflict(session_store: SessionStore):
    session = await session_store.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    sid = session.session_id

    await session_store.acquire_lock(sid, "owner_a")

    with pytest.raises(SessionConflictError):
        await session_store.acquire_lock(sid, "owner_b")


@pytest.mark.asyncio
async def test_stale_lock_takeover(session_store: SessionStore):
    """A lock with an expired TTL should be claimable by another owner."""
    session = await session_store.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    sid = session.session_id

    # Manually insert a stale lock (expired in the past)
    past = (datetime.now(tz=timezone.utc) - timedelta(seconds=3600)).isoformat()
    await session_store.db.execute(
        "UPDATE sessions SET locked_by = ?, lock_expires_at = ? WHERE session_id = ?",
        ("stale_owner", past, sid),
    )
    await session_store.db.commit()

    # New owner should be able to take over
    await session_store.acquire_lock(sid, "new_owner")
    rec = await session_store.get_session(sid)
    assert rec.locked_by == "new_owner"


@pytest.mark.asyncio
async def test_crash_recovery_marks_running_as_interrupted(config):
    """On open(), stale 'running' sessions must be reclassified as 'interrupted'."""
    # Create a running session in one store instance
    store1 = SessionStore(config)
    await store1.open()
    session = await store1.create_session(WorkflowName.run_task, ProfileName.general)
    await store1.update_session(session.session_id, status=SessionStatus.running)
    await store1.close()

    # Re-open — crash recovery should fire
    store2 = SessionStore(config)
    await store2.open()
    recovered = await store2.get_session(session.session_id)
    assert recovered.status == SessionStatus.interrupted
    await store2.close()


@pytest.mark.asyncio
async def test_session_persists_across_reopen(config):
    """Session data must be durable across store close/reopen."""
    store1 = SessionStore(config)
    await store1.open()
    session = await store1.create_session(
        WorkflowName.run_task, ProfileName.general
    )
    await store1.update_session(
        session.session_id, status=SessionStatus.completed, summary_latest="persisted"
    )
    await store1.close()

    store2 = SessionStore(config)
    await store2.open()
    # Manually mark as completed so recovery doesn't reclassify it
    rec = await store2.get_session(session.session_id)
    assert rec.session_id == session.session_id
    assert rec.summary_latest == "persisted"
    assert rec.status == SessionStatus.completed
    await store2.close()
