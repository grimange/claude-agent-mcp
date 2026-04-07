"""Artifact storage: metadata in SQLite, bodies on local filesystem."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import ArtifactPersistenceError
from claude_agent_mcp.types import ArtifactRecord, ArtifactReference

logger = logging.getLogger(__name__)


def _now_utc() -> datetime:
    return datetime.now(tz=timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.isoformat()


def _parse(s: str) -> datetime:
    return datetime.fromisoformat(s)


class ArtifactStore:
    """Manages artifact bodies on disk and metadata in SQLite."""

    def __init__(self, config: Config, db: aiosqlite.Connection) -> None:
        self._config = config
        self._db = db

    async def save_artifact(
        self,
        session_id: str,
        content: bytes,
        *,
        workflow: str,
        profile: str,
        artifact_type: str,
        logical_name: str,
        mime_type: str = "application/octet-stream",
        turn_index: int = 0,
        producer_tool: str = "",
    ) -> ArtifactRecord:
        size = len(content)
        if size > self._config.max_artifact_bytes:
            raise ArtifactPersistenceError(
                f"Artifact exceeds max size: {size} > {self._config.max_artifact_bytes}"
            )

        artifact_id = f"art_{uuid.uuid4().hex[:16]}"
        sha256 = hashlib.sha256(content).hexdigest()

        session_dir = self._config.artifacts_dir / session_id
        session_dir.mkdir(parents=True, exist_ok=True)

        safe_name = logical_name.replace("/", "_").replace("..", "_")
        file_path = session_dir / f"{artifact_id}-{safe_name}"
        file_path.write_bytes(content)

        now = _now_utc()
        rec = ArtifactRecord(
            artifact_id=artifact_id,
            session_id=session_id,
            workflow=workflow,
            profile=profile,
            artifact_type=artifact_type,
            logical_name=logical_name,
            mime_type=mime_type,
            path=str(file_path),
            size_bytes=size,
            sha256=sha256,
            created_at=now,
            turn_index=turn_index,
            producer_tool=producer_tool,
        )

        await self._db.execute(
            """INSERT INTO artifacts
               (artifact_id, session_id, workflow, profile, artifact_type,
                logical_name, mime_type, path, size_bytes, sha256,
                created_at, turn_index, producer_tool)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                rec.artifact_id, rec.session_id, rec.workflow, rec.profile,
                rec.artifact_type, rec.logical_name, rec.mime_type, rec.path,
                rec.size_bytes, rec.sha256, _fmt(rec.created_at),
                rec.turn_index, rec.producer_tool,
            ),
        )
        await self._db.commit()
        logger.debug("Saved artifact %s for session %s", artifact_id, session_id)
        return rec

    async def list_artifacts(self, session_id: str) -> list[ArtifactRecord]:
        async with self._db.execute(
            "SELECT * FROM artifacts WHERE session_id = ? ORDER BY created_at",
            (session_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [self._row_to_record(r) for r in rows]

    def to_reference(self, rec: ArtifactRecord) -> ArtifactReference:
        return ArtifactReference(
            artifact_id=rec.artifact_id,
            artifact_type=rec.artifact_type,
            logical_name=rec.logical_name,
            mime_type=rec.mime_type,
        )

    def _row_to_record(self, row: aiosqlite.Row) -> ArtifactRecord:
        return ArtifactRecord(
            artifact_id=row["artifact_id"],
            session_id=row["session_id"],
            workflow=row["workflow"],
            profile=row["profile"],
            artifact_type=row["artifact_type"],
            logical_name=row["logical_name"],
            mime_type=row["mime_type"],
            path=row["path"],
            size_bytes=row["size_bytes"],
            sha256=row["sha256"],
            created_at=_parse(row["created_at"]),
            turn_index=row["turn_index"],
            producer_tool=row["producer_tool"],
        )
