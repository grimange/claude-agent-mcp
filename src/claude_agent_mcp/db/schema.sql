-- claude-agent-mcp v0.1 SQLite schema
-- All timestamps stored as ISO-8601 UTC strings.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    workflow            TEXT NOT NULL,
    profile             TEXT NOT NULL,
    provider            TEXT NOT NULL DEFAULT 'claude',
    provider_session_id TEXT,
    status              TEXT NOT NULL DEFAULT 'created',
    working_directory   TEXT,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    last_activity_at    TEXT NOT NULL,
    request_count       INTEGER NOT NULL DEFAULT 0,
    turn_count          INTEGER NOT NULL DEFAULT 0,
    artifact_count      INTEGER NOT NULL DEFAULT 0,
    summary_latest      TEXT,
    locked_by           TEXT,
    lock_expires_at     TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);

CREATE TABLE IF NOT EXISTS session_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL REFERENCES sessions(session_id),
    event_type  TEXT NOT NULL,
    turn_index  INTEGER NOT NULL DEFAULT 0,
    payload     TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_session ON session_events(session_id, event_id);

CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL REFERENCES sessions(session_id),
    workflow      TEXT NOT NULL,
    profile       TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    logical_name  TEXT NOT NULL,
    mime_type     TEXT NOT NULL DEFAULT 'application/octet-stream',
    path          TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL DEFAULT 0,
    sha256        TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL,
    turn_index    INTEGER NOT NULL DEFAULT 0,
    producer_tool TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_artifacts_session ON artifacts(session_id);
