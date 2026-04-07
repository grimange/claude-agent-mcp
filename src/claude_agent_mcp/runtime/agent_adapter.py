"""Claude provider adapter.

Wraps the Anthropic SDK (Messages API) to produce normalized internal results.
Provider-specific types must not escape this module.

v0.1 uses the Messages API directly for task execution.
v0.3 adds run_with_tools() for federation tool-use loop support.
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import NormalizationError, ProviderRuntimeError
from claude_agent_mcp.types import NormalizedProviderResult

logger = logging.getLogger(__name__)

# Tool executor callback type: (tool_name, tool_input) -> result_string
ToolExecutor = Callable[[str, dict[str, Any]], Awaitable[str]]


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

    async def run_with_tools(
        self,
        *,
        system_prompt: str,
        task: str,
        max_turns: int,
        tools: list[dict[str, Any]],
        tool_executor: ToolExecutor,
        conversation_history: list[dict[str, Any]] | None = None,
    ) -> NormalizedProviderResult:
        """Execute a task with downstream tool-use loop support.

        Iterates the tool-use loop: Claude responds → if tool_use blocks are
        present, invoke via tool_executor → send tool_results → repeat until
        end_turn or max_turns exhausted.

        tools: list of Anthropic tool definition dicts (name, description, input_schema)
        tool_executor: async callable (normalized_name, input_dict) -> result_string
        """
        client = self._get_client()

        messages: list[dict[str, Any]] = list(conversation_history or [])
        messages.append({"role": "user", "content": task})

        turn_count = 0
        output_text = ""
        stop_reason = None
        warnings: list[str] = []

        try:
            while turn_count < max_turns:
                call_kwargs: dict[str, Any] = dict(
                    model=self._config.model,
                    max_tokens=8192,
                    system=system_prompt,
                    messages=messages,
                )
                if tools:
                    call_kwargs["tools"] = tools

                response = await client.messages.create(**call_kwargs)
                stop_reason = response.stop_reason
                turn_count += 1

                # Collect text and tool_use blocks
                text_parts: list[str] = []
                tool_uses: list[Any] = []
                for block in response.content:
                    if hasattr(block, "text"):
                        text_parts.append(block.text)
                    elif getattr(block, "type", None) == "tool_use":
                        tool_uses.append(block)

                if text_parts:
                    output_text = "\n".join(text_parts)

                if not tool_uses or stop_reason != "tool_use":
                    # No tool calls — done
                    break

                # Add assistant message with content blocks to history
                messages.append({"role": "assistant", "content": response.content})

                # Execute each tool call and collect results
                tool_results: list[dict[str, Any]] = []
                for tu in tool_uses:
                    tool_name = getattr(tu, "name", "")
                    tool_input = getattr(tu, "input", {}) or {}
                    tool_use_id = getattr(tu, "id", "")

                    try:
                        result_str = await tool_executor(tool_name, tool_input)
                    except Exception as exc:
                        logger.warning("Tool executor error for %r: %s", tool_name, exc)
                        result_str = f"Error: {exc}"

                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": result_str,
                    })

                messages.append({"role": "user", "content": tool_results})

            if not output_text:
                warnings.append("Provider returned an empty text response")

        except ProviderRuntimeError:
            raise
        except Exception as exc:
            raise ProviderRuntimeError(f"Claude API error (with tools): {exc}") from exc

        try:
            return NormalizedProviderResult(
                output_text=output_text,
                turn_count=turn_count,
                provider_session_id=None,
                stop_reason=stop_reason,
                warnings=warnings,
            )
        except Exception as exc:
            raise NormalizationError(f"Failed to normalize provider result: {exc}") from exc
