"""Execution backend interface for claude-agent-mcp (v0.4/v0.5).

All execution backends must implement ExecutionBackend.
Backend-specific types must not escape this boundary.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Awaitable, Callable

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config
    from claude_agent_mcp.types import NormalizedProviderResult

# Tool executor callback: (normalized_tool_name, tool_input) -> result_string
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


@dataclass(frozen=True)
class BackendCapabilities:
    """Declares what an execution backend supports (v0.5).

    Used internally by the workflow executor to emit warnings, suppress
    unsupported paths, and improve observability. Not exposed in MCP contracts.
    """

    supports_downstream_tools: bool = False
    """Backend can receive and invoke downstream federation tools."""

    supports_structured_tool_use: bool = False
    """Backend participates in a structured agentic tool-use loop."""

    supports_native_multiturn: bool = False
    """Backend maintains its own native conversation state across turns."""

    supports_rich_stop_reason: bool = False
    """Backend returns semantically rich stop_reason values (not just end_turn)."""

    supports_structured_messages: bool = False
    """Backend accepts structured role/content message objects (not flat text)."""

    supports_workspace_assumptions: bool = False
    """Backend can operate on a local workspace directory natively."""

    supports_limited_downstream_tools: bool = False
    """Backend supports limited downstream tool description injection (text-based, not invocable)."""


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

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Declare supported capabilities for this backend (v0.5).

        Used by the workflow executor to emit appropriate warnings and
        suppress unsupported forwarding paths.
        """
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
        session_summary: str | None = None,
        is_continuation: bool = False,
    ) -> "NormalizedProviderResult":
        """Execute a task (or continue a session) and return a normalized result.

        Args:
            system_prompt: System prompt resolved from the active profile.
            task: The task or continuation message to execute.
            max_turns: Maximum turn count enforced by policy.
            tools: Optional list of visible tool definitions (Anthropic format).
            tool_executor: Async callable for invoking tools by name.
            conversation_history: Prior conversation messages for continuation.
            session_summary: Optional summary of the session so far, used by
                backends that reconstruct context from text (e.g. claude_code).
            is_continuation: When True, the backend may use a continuation-optimized
                prompt structure.

        Returns:
            NormalizedProviderResult — no backend-specific types.

        Raises:
            ProviderRuntimeError or a backend-specific subclass on execution failure.
            NormalizationError if the result cannot be normalized.
        """
        ...
