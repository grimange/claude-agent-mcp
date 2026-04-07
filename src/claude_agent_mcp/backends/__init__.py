"""Execution backend package for claude-agent-mcp.

Provides pluggable execution backend support (v0.4).

Supported backends:
  api          — Anthropic API (ANTHROPIC_API_KEY)
  claude_code  — Claude Code CLI

Usage:
    from claude_agent_mcp.backends import build_backend, VALID_BACKENDS
    backend = build_backend(config)
"""

from __future__ import annotations

from claude_agent_mcp.backends.base import ExecutionBackend, ToolExecutor
from claude_agent_mcp.backends.registry import BackendRegistry

VALID_BACKENDS: frozenset[str] = frozenset({"api", "claude_code"})


def build_backend(config) -> ExecutionBackend:
    """Resolve and validate the configured execution backend.

    Raises ExecutionBackendConfigError on unknown or misconfigured backend.
    """
    from claude_agent_mcp.backends.api_backend import ApiExecutionBackend
    from claude_agent_mcp.backends.claude_code_backend import ClaudeCodeExecutionBackend

    registry = BackendRegistry()
    registry.register(ApiExecutionBackend(config))
    registry.register(ClaudeCodeExecutionBackend(config))

    backend = registry.get(config.execution_backend)
    backend.validate_startup(config)
    return backend


__all__ = [
    "ExecutionBackend",
    "ToolExecutor",
    "BackendRegistry",
    "VALID_BACKENDS",
    "build_backend",
]
