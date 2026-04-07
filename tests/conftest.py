"""Shared test fixtures for claude-agent-mcp tests."""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from typing import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.config import Config
from claude_agent_mcp.runtime.agent_adapter import ClaudeAdapter
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import NormalizedProviderResult

# ── additional config attributes required by v0.2 ──────────────────────────
# These defaults keep existing tests passing without modification.
_CONFIG_V02_DEFAULTS = {
    "transport": "stdio",
    "host": "127.0.0.1",
    "port": 8000,
}


@pytest.fixture
def tmp_state_dir(tmp_path: Path) -> Path:
    state = tmp_path / ".state"
    state.mkdir()
    return state


@pytest.fixture
def config(tmp_state_dir: Path, tmp_path: Path) -> Config:
    cfg = Config.__new__(Config)
    cfg.anthropic_api_key = "test-key"
    cfg.state_dir = tmp_state_dir
    cfg.db_path = tmp_state_dir / "test.db"
    cfg.artifacts_dir = tmp_state_dir / "artifacts"
    cfg.model = "claude-sonnet-4-6"
    cfg.lock_ttl_seconds = 60
    # Allow both tmp_path and CWD so tests can omit working_directory
    cfg.allowed_dirs = [str(tmp_path), str(Path.cwd().resolve())]
    cfg.max_artifact_bytes = 10 * 1024 * 1024
    cfg.log_level = "WARNING"
    # v0.2 transport fields
    cfg.transport = "stdio"
    cfg.host = "127.0.0.1"
    cfg.port = 8000
    return cfg


@pytest.fixture
async def session_store(config: Config) -> AsyncGenerator[SessionStore, None]:
    store = SessionStore(config)
    await store.open()
    yield store
    await store.close()


@pytest.fixture
def mock_adapter() -> ClaudeAdapter:
    adapter = MagicMock(spec=ClaudeAdapter)
    adapter.run = AsyncMock(
        return_value=NormalizedProviderResult(
            output_text="Task completed successfully.",
            turn_count=1,
            stop_reason="end_turn",
        )
    )
    adapter.continue_run = AsyncMock(
        return_value=NormalizedProviderResult(
            output_text="Continuation completed.",
            turn_count=1,
            stop_reason="end_turn",
        )
    )
    return adapter


@pytest.fixture
def artifact_store_fixture(config: Config, session_store: SessionStore) -> ArtifactStore:
    """Standalone ArtifactStore fixture for transport tests."""
    return ArtifactStore(config, session_store.db)


@pytest.fixture
async def executor(
    config: Config,
    session_store: SessionStore,
    mock_adapter: ClaudeAdapter,
) -> WorkflowExecutor:
    artifact_store = ArtifactStore(config, session_store.db)
    policy_engine = PolicyEngine(config)
    profile_registry = ProfileRegistry()
    return WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=artifact_store,
        policy_engine=policy_engine,
        profile_registry=profile_registry,
        agent_adapter=mock_adapter,
    )
