"""Configuration loading for claude-agent-mcp.

Reads from environment variables (with .env support).
All paths are resolved to absolute strings at load time.

Environment variable reference:
  ANTHROPIC_API_KEY               — Claude API key (required for execution)
  CLAUDE_AGENT_MCP_TRANSPORT      — Transport mode: stdio | streamable-http (default: stdio)
  CLAUDE_AGENT_MCP_HOST           — Bind host for network transport (default: 127.0.0.1)
  CLAUDE_AGENT_MCP_PORT           — Bind port for network transport (default: 8000)
  CLAUDE_AGENT_MCP_STATE_DIR      — State storage directory (default: .state)
  CLAUDE_AGENT_MCP_DB_PATH        — SQLite path override (default: <state_dir>/claude-agent-mcp.db)
  CLAUDE_AGENT_MCP_ARTIFACT_DIR   — Artifact storage directory override
  CLAUDE_AGENT_MCP_LOG_LEVEL      — Log level (default: INFO)
  CLAUDE_AGENT_MCP_MODEL          — Claude model (default: claude-sonnet-4-6)
  CLAUDE_AGENT_MCP_LOCK_TTL       — Session lock TTL in seconds (default: 300)
  CLAUDE_AGENT_MCP_ALLOWED_DIRS   — Comma-separated allowed working directories

Legacy variable names (still supported, lower priority than MCP-prefixed names):
  CLAUDE_AGENT_STATE_DIR, CLAUDE_AGENT_MODEL, CLAUDE_AGENT_LOCK_TTL_SECONDS,
  CLAUDE_AGENT_ALLOWED_DIRS, CLAUDE_AGENT_MAX_ARTIFACT_BYTES, CLAUDE_AGENT_LOG_LEVEL
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_TRANSPORTS = {"stdio", "streamable-http"}


def _env(primary: str, fallback: str | None = None, default: str = "") -> str:
    """Return primary env var, falling back to legacy name, then default."""
    v = os.environ.get(primary)
    if v is not None:
        return v
    if fallback:
        v = os.environ.get(fallback)
        if v is not None:
            return v
    return default


class Config:
    """Runtime configuration derived from environment."""

    def __init__(self) -> None:
        self.anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

        # --- Transport ---
        self.transport: str = _env(
            "CLAUDE_AGENT_MCP_TRANSPORT", default="stdio"
        ).strip().lower()

        self.host: str = _env("CLAUDE_AGENT_MCP_HOST", default="127.0.0.1").strip()
        self.port: int = int(_env("CLAUDE_AGENT_MCP_PORT", default="8000").strip())

        # --- Storage ---
        state_dir_raw = _env(
            "CLAUDE_AGENT_MCP_STATE_DIR", "CLAUDE_AGENT_STATE_DIR", default=".state"
        )
        self.state_dir: Path = Path(state_dir_raw).resolve()

        db_override = _env("CLAUDE_AGENT_MCP_DB_PATH", default="")
        self.db_path: Path = (
            Path(db_override).resolve() if db_override else self.state_dir / "claude-agent-mcp.db"
        )

        artifact_override = _env("CLAUDE_AGENT_MCP_ARTIFACT_DIR", default="")
        self.artifacts_dir: Path = (
            Path(artifact_override).resolve() if artifact_override else self.state_dir / "artifacts"
        )

        # --- Runtime ---
        self.model: str = _env(
            "CLAUDE_AGENT_MCP_MODEL", "CLAUDE_AGENT_MODEL", default="claude-sonnet-4-6"
        )

        self.lock_ttl_seconds: int = int(
            _env("CLAUDE_AGENT_MCP_LOCK_TTL", "CLAUDE_AGENT_LOCK_TTL_SECONDS", default="300")
        )

        allowed_raw = _env(
            "CLAUDE_AGENT_MCP_ALLOWED_DIRS", "CLAUDE_AGENT_ALLOWED_DIRS", default=""
        )
        if allowed_raw:
            self.allowed_dirs: list[str] = [
                str(Path(d.strip()).resolve())
                for d in allowed_raw.split(",")
                if d.strip()
            ]
        else:
            self.allowed_dirs = [str(Path.cwd().resolve())]

        self.max_artifact_bytes: int = int(
            _env(
                "CLAUDE_AGENT_MCP_MAX_ARTIFACT_BYTES",
                "CLAUDE_AGENT_MAX_ARTIFACT_BYTES",
                default=str(10 * 1024 * 1024),
            )
        )

        self.log_level: str = _env(
            "CLAUDE_AGENT_MCP_LOG_LEVEL", "CLAUDE_AGENT_LOG_LEVEL", default="INFO"
        ).upper()

    def validate(self) -> None:
        """Fail early and clearly on invalid startup configuration."""
        errors: list[str] = []

        if self.transport not in VALID_TRANSPORTS:
            errors.append(
                f"CLAUDE_AGENT_MCP_TRANSPORT={self.transport!r} is not valid. "
                f"Choose from: {sorted(VALID_TRANSPORTS)}"
            )

        if self.transport == "streamable-http":
            if not self.host:
                errors.append("CLAUDE_AGENT_MCP_HOST must not be empty for streamable-http transport")
            if not (1 <= self.port <= 65535):
                errors.append(
                    f"CLAUDE_AGENT_MCP_PORT={self.port} is out of range (1–65535)"
                )

        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append(
                f"CLAUDE_AGENT_MCP_LOG_LEVEL={self.log_level!r} is not a valid log level"
            )

        if errors:
            raise SystemExit(
                "claude-agent-mcp startup configuration error(s):\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

    def ensure_dirs(self) -> None:
        """Create state directories if they do not exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
