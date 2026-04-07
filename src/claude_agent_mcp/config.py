"""Configuration loading for claude-agent-mcp.

Reads from environment variables (with .env support).
All paths are resolved to absolute strings at load time.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class Config:
    """Runtime configuration derived from environment."""

    def __init__(self) -> None:
        self.anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

        state_dir_raw = os.environ.get("CLAUDE_AGENT_STATE_DIR", ".state")
        self.state_dir: Path = Path(state_dir_raw).resolve()

        self.db_path: Path = self.state_dir / "claude-agent-mcp.db"
        self.artifacts_dir: Path = self.state_dir / "artifacts"

        self.model: str = os.environ.get("CLAUDE_AGENT_MODEL", "claude-sonnet-4-6")

        self.lock_ttl_seconds: int = int(
            os.environ.get("CLAUDE_AGENT_LOCK_TTL_SECONDS", "300")
        )

        allowed_raw = os.environ.get("CLAUDE_AGENT_ALLOWED_DIRS", "")
        if allowed_raw:
            self.allowed_dirs: list[str] = [
                str(Path(d.strip()).resolve())
                for d in allowed_raw.split(",")
                if d.strip()
            ]
        else:
            self.allowed_dirs = [str(Path.cwd().resolve())]

        self.max_artifact_bytes: int = int(
            os.environ.get("CLAUDE_AGENT_MAX_ARTIFACT_BYTES", str(10 * 1024 * 1024))
        )

        self.log_level: str = os.environ.get("CLAUDE_AGENT_LOG_LEVEL", "INFO").upper()

    def ensure_dirs(self) -> None:
        """Create state directories if they do not exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
