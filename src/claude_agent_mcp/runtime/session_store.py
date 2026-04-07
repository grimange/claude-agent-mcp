"""SQLite-backed session store.

Provides CRUD for sessions, session events, and session locking.
This module is the single authority for all session persistence operations.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import aiosqlite

from claude_agent_mcp.config import Config
from claude_agent_mcp.db.migrations import bootstrap
from claude_agent_mcp.errors import SessionConflictError, SessionNotFoundError
from claude_agent_mcp.types import (
    EventType,
    ProfileName,
    SessionDetail,
    SessionEventRecord,
    SessionRecord,
    SessionStatus,
    SessionSummary,
    WorkflowName,
)

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


class SessionStore:
    """Async session store backed by SQLite."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self._config.ensure_dirs()
        self._db = await aiosqlite.connect(str(self._config.db_path))
        self._db.row_factory = aiosqlite.Row
        await bootstrap(self._db)
        await self._recover_stale_sessions()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "SessionStore not opened"
        return self._db

    # ------------------------------------------------------------------
    # Crash recovery
    # ------------------------------------------------------------------

    async def _recover_stale_sessions(self) -> None:
        """On startup, reclassify stale running sessions as interrupted."""
        now = _fmt(_now_utc())
        async with self.db.execute(
            """
            UPDATE sessions
            SET status = 'interrupted',
                updated_at = ?,
                locked_by = NULL,
                lock_expires_at = NULL
            WHERE status = 'running'
            """,
            (now,),
        ) as cur:
            count = cur.rowcount
        await self.db.commit()
        if count:
            logger.warning(
                "Crash recovery: %d stale running session(s) marked interrupted", count
            )

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    async def create_session(
        self,
        workflow: WorkflowName,
        profile: ProfileName,
        working_directory: str | None = None,
        provider: str = "claude",
    ) -> SessionRecord:
        now = _now_utc()
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        rec = SessionRecord(
            session_id=session_id,
            workflow=workflow,
            profile=profile,
            provider=provider,
            status=SessionStatus.created,
            working_directory=working_directory,
            created_at=now,
            updated_at=now,
            last_activity_at=now,
        )
        await self.db.execute(
            """
            INSERT INTO sessions
            (session_id, workflow, profile, provider, provider_session_id,
             status, working_directory, created_at, updated_at, last_activity_at,
             request_count, turn_count, artifact_count, summary_latest,
             locked_by, lock_expires_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                rec.session_id, rec.workflow.value, rec.profile.value, rec.provider,
                rec.provider_session_id, rec.status.value, rec.working_directory,
                _fmt(rec.created_at), _fmt(rec.updated_at), _fmt(rec.last_activity_at),
                rec.request_count, rec.turn_count, rec.artifact_count,
                rec.summary_latest, rec.locked_by, rec.lock_expires_at,
            ),
        )
        await self.db.commit()
        logger.debug("Created session %s (workflow=%s)", session_id, workflow.value)
        return rec

    async def get_session(self, session_id: str) -> SessionRecord:
        async with self.db.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")
        return self._row_to_record(row)

    async def update_session(
        self,
        session_id: str,
        *,
        status: SessionStatus | None = None,
        turn_count: int | None = None,
        artifact_count: int | None = None,
        summary_latest: str | None = None,
        provider_session_id: str | None = None,
        request_count_delta: int = 0,
    ) -> None:
        now = _fmt(_now_utc())
        parts: list[str] = ["updated_at = ?", "last_activity_at = ?"]
        params: list[object] = [now, now]

        if status is not None:
            parts.append("status = ?")
            params.append(status.value)
        if turn_count is not None:
            parts.append("turn_count = ?")
            params.append(turn_count)
        if artifact_count is not None:
            parts.append("artifact_count = ?")
            params.append(artifact_count)
        if summary_latest is not None:
            parts.append("summary_latest = ?")
            params.append(summary_latest)
        if provider_session_id is not None:
            parts.append("provider_session_id = ?")
            params.append(provider_session_id)
        if request_count_delta:
            parts.append("request_count = request_count + ?")
            params.append(request_count_delta)

        params.append(session_id)
        await self.db.execute(
            f"UPDATE sessions SET {', '.join(parts)} WHERE session_id = ?",
            params,
        )
        await self.db.commit()

    async def list_sessions(
        self,
        limit: int = 20,
        status: SessionStatus | None = None,
    ) -> list[SessionSummary]:
        if status is not None:
            async with self.db.execute(
                """SELECT session_id, workflow, profile, status, updated_at, summary_latest
                   FROM sessions WHERE status = ? ORDER BY updated_at DESC LIMIT ?""",
                (status.value, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self.db.execute(
                """SELECT session_id, workflow, profile, status, updated_at, summary_latest
                   FROM sessions ORDER BY updated_at DESC LIMIT ?""",
                (limit,),
            ) as cur:
                rows = await cur.fetchall()

        return [
            SessionSummary(
                session_id=r["session_id"],
                workflow=WorkflowName(r["workflow"]),
                profile=ProfileName(r["profile"]),
                status=SessionStatus(r["status"]),
                updated_at=_parse(r["updated_at"]),
                summary_latest=r["summary_latest"],
            )
            for r in rows
        ]

    async def get_session_detail(self, session_id: str) -> SessionDetail:
        rec = await self.get_session(session_id)
        return SessionDetail(
            session_id=rec.session_id,
            workflow=rec.workflow,
            profile=rec.profile,
            status=rec.status,
            created_at=rec.created_at,
            updated_at=rec.updated_at,
            last_activity_at=rec.last_activity_at,
            summary_latest=rec.summary_latest,
            artifact_count=rec.artifact_count,
            turn_count=rec.turn_count,
            request_count=rec.request_count,
            working_directory=rec.working_directory,
        )

    # ------------------------------------------------------------------
    # Session locking (single-writer enforcement)
    # ------------------------------------------------------------------

    async def acquire_lock(self, session_id: str, owner: str) -> None:
        """Acquire a write lock on a session.

        Raises SessionConflictError if the session is already locked by a
        non-expired lock. Raises SessionNotFoundError if the session is absent.
        """
        now = _now_utc()
        expires_at = now + timedelta(seconds=self._config.lock_ttl_seconds)

        async with self.db.execute(
            "SELECT locked_by, lock_expires_at, status FROM sessions WHERE session_id = ?",
            (session_id,),
        ) as cur:
            row = await cur.fetchone()

        if row is None:
            raise SessionNotFoundError(f"Session not found: {session_id}")

        existing_owner = row["locked_by"]
        existing_expires_raw = row["lock_expires_at"]

        if existing_owner is not None:
            existing_expires = _parse(existing_expires_raw) if existing_expires_raw else None
            if existing_expires is None or existing_expires > now:
                raise SessionConflictError(
                    f"Session {session_id} is locked by {existing_owner}"
                )
            # Stale lock — can be taken over.

        await self.db.execute(
            "UPDATE sessions SET locked_by = ?, lock_expires_at = ? WHERE session_id = ?",
            (owner, _fmt(expires_at), session_id),
        )
        await self.db.commit()
        logger.debug("Lock acquired: session=%s owner=%s", session_id, owner)

    async def release_lock(self, session_id: str, owner: str) -> None:
        """Release a lock owned by owner. No-op if not held by owner."""
        await self.db.execute(
            """UPDATE sessions SET locked_by = NULL, lock_expires_at = NULL
               WHERE session_id = ? AND locked_by = ?""",
            (session_id, owner),
        )
        await self.db.commit()
        logger.debug("Lock released: session=%s owner=%s", session_id, owner)

    async def expire_stale_locks(self) -> int:
        """Clear expired locks. Returns number of locks cleared."""
        now = _fmt(_now_utc())
        async with self.db.execute(
            """UPDATE sessions SET locked_by = NULL, lock_expires_at = NULL
               WHERE locked_by IS NOT NULL AND lock_expires_at < ?""",
            (now,),
        ) as cur:
            count = cur.rowcount
        await self.db.commit()
        if count:
            logger.info("Expired %d stale lock(s)", count)
        return count

    # ------------------------------------------------------------------
    # Session events
    # ------------------------------------------------------------------

    async def append_event(
        self,
        session_id: str,
        event_type: EventType,
        turn_index: int,
        payload: dict,
    ) -> None:
        now = _fmt(_now_utc())
        await self.db.execute(
            """INSERT INTO session_events(session_id, event_type, turn_index, payload, created_at)
               VALUES (?,?,?,?,?)""",
            (session_id, event_type.value, turn_index, json.dumps(payload), now),
        )
        await self.db.commit()

    async def get_events(self, session_id: str) -> list[SessionEventRecord]:
        async with self.db.execute(
            """SELECT event_id, session_id, event_type, turn_index, payload, created_at
               FROM session_events WHERE session_id = ? ORDER BY event_id""",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [
            SessionEventRecord(
                event_id=r["event_id"],
                session_id=r["session_id"],
                event_type=EventType(r["event_type"]),
                turn_index=r["turn_index"],
                payload=json.loads(r["payload"]),
                created_at=_parse(r["created_at"]),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_record(self, row: aiosqlite.Row) -> SessionRecord:
        return SessionRecord(
            session_id=row["session_id"],
            workflow=WorkflowName(row["workflow"]),
            profile=ProfileName(row["profile"]),
            provider=row["provider"],
            provider_session_id=row["provider_session_id"],
            status=SessionStatus(row["status"]),
            working_directory=row["working_directory"],
            created_at=_parse(row["created_at"]),
            updated_at=_parse(row["updated_at"]),
            last_activity_at=_parse(row["last_activity_at"]),
            request_count=row["request_count"],
            turn_count=row["turn_count"],
            artifact_count=row["artifact_count"],
            summary_latest=row["summary_latest"],
            locked_by=row["locked_by"],
            lock_expires_at=_parse(row["lock_expires_at"]) if row["lock_expires_at"] else None,
        )
