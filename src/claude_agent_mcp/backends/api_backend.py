"""API execution backend for claude-agent-mcp (v0.4).

Executes tasks through the Anthropic Messages API using ANTHROPIC_API_KEY.
This is the default backend and preserves existing v0.1–v0.3 behavior.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from claude_agent_mcp.backends.base import ExecutionBackend, ToolExecutor
from claude_agent_mcp.errors import ExecutionBackendAuthError, ExecutionBackendConfigError
from claude_agent_mcp.runtime.agent_adapter import ClaudeAdapter

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.types import NormalizedProviderResult

logger = logging.getLogger(__name__)


class ApiExecutionBackend(ExecutionBackend):
    """Execution backend backed by the Anthropic Messages API.

    Authentication: ANTHROPIC_API_KEY environment variable.

    This backend wraps the existing ClaudeAdapter. It is the backward-compatible
    default and preserves all existing execution semantics.
    """

    def __init__(self, config: "Config") -> None:
        self._config = config
        self._adapter = ClaudeAdapter(config)

    @property
    def name(self) -> str:
        return "api"

    def validate_startup(self, config: "Config") -> None:
        """Fail clearly if ANTHROPIC_API_KEY is absent."""
        if not config.anthropic_api_key:
            raise ExecutionBackendAuthError(
                "ANTHROPIC_API_KEY is required for the 'api' execution backend. "
                "Set the environment variable or use a .env file."
            )
        logger.debug("api backend: ANTHROPIC_API_KEY present")

    def is_available(self, config: "Config") -> bool:
        return bool(config.anthropic_api_key)

    async def execute(
        self,
        *,
        system_prompt: str,
        task: str,
        max_turns: int,
        tools: list[dict[str, Any]] | None = None,
        tool_executor: ToolExecutor | None = None,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> "NormalizedProviderResult":
        """Execute via Anthropic Messages API.

        Routes to run_with_tools if tools are provided, otherwise run().
        """
        if tools and tool_executor:
            return await self._adapter.run_with_tools(
                system_prompt=system_prompt,
                task=task,
                max_turns=max_turns,
                tools=tools,
                tool_executor=tool_executor,
                conversation_history=conversation_history,
            )
        return await self._adapter.run(
            system_prompt=system_prompt,
            task=task,
            max_turns=max_turns,
            conversation_history=conversation_history,
        )
