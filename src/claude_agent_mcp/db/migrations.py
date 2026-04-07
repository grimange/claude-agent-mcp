"""SQLite schema bootstrap and migration runner for claude-agent-mcp."""

from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

# Current schema version — bump when adding migrations.
CURRENT_VERSION = 1


async def bootstrap(db: aiosqlite.Connection) -> None:
    """Apply schema DDL and record the initial migration if not present."""
    schema_path = Path(__file__).parent / "schema.sql"
    ddl = schema_path.read_text()

    await db.executescript(ddl)
    await db.commit()

    # Record version 1 if not already present.
    await db.execute(
        """
        INSERT OR IGNORE INTO schema_migrations(version, applied_at)
        VALUES (1, datetime('now'))
        """
    )
    await db.commit()
    logger.debug("Schema bootstrap complete (version=%d)", CURRENT_VERSION)


async def get_schema_version(db: aiosqlite.Connection) -> int:
    """Return the highest applied migration version, or 0 if none."""
    try:
        async with db.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ) as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        return 0
