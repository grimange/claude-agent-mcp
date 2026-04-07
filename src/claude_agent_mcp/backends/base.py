"""Execution backend interface for claude-agent-mcp (v0.4).

All execution backends must implement ExecutionBackend.
Backend-specific types must not escape this boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.types import NormalizedProviderResult

# Tool executor callback: (normalized_tool_name, tool_input) -> result_string
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


class ExecutionBackend(ABC):
    """Pluggable execution backend interface.

    Each backend implements task execution and startup validation.
    The workflow executor remains the central orchestrator; backends
    are execution substrates only.

    Contract rules:
    - Backends must return NormalizedProviderResult — no SDK types escape.
    - Backends must not own session state, policy enforcement, or artifact storage.
    - Authentication models are backend-specific and must not be conflated.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable identifier for this backend (e.g. 'api', 'claude_code')."""
        ...

    @abstractmethod
    def validate_startup(self, config: "Config") -> None:
        """Validate prerequisites for this backend.

        Raises ExecutionBackendConfigError, ExecutionBackendAuthError, or
        a backend-specific subclass if required prerequisites are absent.

        This is called at server startup. Fail clearly and early.
        """
        ...

    @abstractmethod
    def is_available(self, config: "Config") -> bool:
        """Return True if this backend can be used with the given config.

        Should not raise. Returns False if prerequisites are unavailable.
        """
        ...

    @abstractmethod
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
        """Execute a task (or continue a session) and return a normalized result.

        Args:
            system_prompt: System prompt resolved from the active profile.
            task: The task or continuation message to execute.
            max_turns: Maximum turn count enforced by policy.
            tools: Optional list of visible tool definitions (Anthropic format).
            tool_executor: Async callable for invoking tools by name.
            conversation_history: Prior conversation messages for continuation.

        Returns:
            NormalizedProviderResult — no backend-specific types.

        Raises:
            ProviderRuntimeError or a backend-specific subclass on execution failure.
            NormalizationError if the result cannot be normalized.
        """
        ...
