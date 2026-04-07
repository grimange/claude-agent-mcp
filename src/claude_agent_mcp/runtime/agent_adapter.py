"""Claude provider adapter.

Wraps the Anthropic SDK (Messages API) to produce normalized internal results.
Provider-specific types must not escape this module.

v0.1 uses the Messages API directly for task execution. The Claude Agent SDK
(claude_agent_sdk) may be used in a future version; for now, we use the
Anthropic Python SDK's Messages API which is stable and well-documented.
"""

from __future__ import annotations

import logging
from typing import Any

from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import NormalizationError, ProviderRuntimeError
from claude_agent_mcp.types import NormalizedProviderResult

logger = logging.getLogger(__name__)


class ClaudeAdapter:
    """Adapter over the Anthropic Messages API.

    Produces NormalizedProviderResult objects — no SDK types escape.
    """

    def __init__(self, config: Config) -> None:
        self._config = config
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic  # type: ignore[import]
                self._client = anthropic.AsyncAnthropic(
                    api_key=self._config.anthropic_api_key
                )
            except ImportError as exc:
                raise ProviderRuntimeError(
                    "anthropic package is not installed. Run: pip install anthropic"
                ) from exc
        return self._client

    async def run(
        self,
        *,
        system_prompt: str,
        task: str,
        max_turns: int,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> NormalizedProviderResult:
        """Execute a task using the Messages API.

        Handles multi-turn via recursive conversation history.
        Returns a NormalizedProviderResult.
        """
        client = self._get_client()

        messages: list[dict[str, Any]] = list(conversation_history or [])
        messages.append({"role": "user", "content": task})

        turn_count = 0
        output_text = ""
        stop_reason = None
        warnings: list[str] = []

        try:
            # Single-shot call for v0.1 (no tool-use loop)
            response = await client.messages.create(
                model=self._config.model,
                max_tokens=8192,
                system=system_prompt,
                messages=messages,
            )

            stop_reason = response.stop_reason
            turn_count = 1

            # Collect text blocks
            text_parts: list[str] = []
            for block in response.content:
                if hasattr(block, "text"):
                    text_parts.append(block.text)
            output_text = "\n".join(text_parts)

            if not output_text:
                warnings.append("Provider returned an empty response")

        except ProviderRuntimeError:
            raise
        except Exception as exc:
            raise ProviderRuntimeError(f"Claude API error: {exc}") from exc

        try:
            return NormalizedProviderResult(
                output_text=output_text,
                turn_count=turn_count,
                provider_session_id=None,  # Messages API is stateless
                stop_reason=stop_reason,
                warnings=warnings,
            )
        except Exception as exc:
            raise NormalizationError(f"Failed to normalize provider result: {exc}") from exc

    async def continue_run(
        self,
        *,
        system_prompt: str,
        message: str,
        conversation_history: list[dict[str, Any]],
        max_turns: int,
    ) -> NormalizedProviderResult:
        """Continue a conversation by appending a new user message."""
        return await self.run(
            system_prompt=system_prompt,
            task=message,
            max_turns=max_turns,
            conversation_history=conversation_history,
        )
