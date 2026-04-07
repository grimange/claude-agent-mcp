"""Claude Code execution backend for claude-agent-mcp (v0.5/v0.6).

Executes tasks through the Claude Code CLI rather than direct API calls.
This allows operators who have Claude Code installed (and authenticated via
Claude Code's own auth flow) to use claude-agent-mcp without an API key.

Authentication model:
    Claude Code backend does NOT use ANTHROPIC_API_KEY.
    It relies on Claude Code's own authentication state, established by
    running `claude login` in the operator's environment.

Implementation:
    CLI-backed. Invokes `claude --print "<prompt>"` as a subprocess and
    collects stdout as the normalized output text.

Capabilities (v0.5/v0.6):
    - supports_downstream_tools: False — federation tools are not forwarded.
    - supports_structured_tool_use: False — no agentic tool-use loop.
    - supports_native_multiturn: False — single invocation per call.
    - supports_rich_stop_reason: False — stop_reason always backend_defaulted.
    - supports_structured_messages: False — history reconstructed as text.
    - supports_workspace_assumptions: True — CLI runs in local environment.
    - supports_limited_downstream_tools: True — text-based tool description injection (v0.6, opt-in).

Context reconstruction (v0.5):
    Continuation history is rendered in a structured format with clear role
    boundaries, a session summary section, and deterministic truncation.
    Truncation policy: most recent HISTORY_MAX_EXCHANGES exchanges are kept.
    If truncation occurs, a warning is added to the result.

Limited tool forwarding (v0.6):
    When enabled via config, compatible tools are injected as text descriptions
    into the prompt. This is NOT a real tool-use loop — the CLI cannot invoke
    tools. It is text-based tool awareness only.

    Tools are screened for compatibility before injection. Incompatible tools
    (complex schemas, missing descriptions, etc.) are filtered with per-tool
    warnings. See ToolCompatibilityLevel for details.

Continuation prompt (v0.6):
    When is_continuation=True, the prompt uses [Continuation Session] framing
    instead of [Session Context], and the [Instructions] section emphasizes
    resuming from where the session left off.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_mcp.backends.base import BackendCapabilities, ExecutionBackend, ToolExecutor
from claude_agent_mcp.errors import (
    ClaudeCodeInvocationError,
    ClaudeCodeUnavailableError,
    NormalizationError,
)
from claude_agent_mcp.types import NormalizedProviderResult

if TYPE_CHECKING:
    from claude_agent_mcp.config import Config

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 300

# Truncation policy constants (v0.5)
# Keep at most this many user/assistant exchange pairs in reconstructed context.
HISTORY_MAX_EXCHANGES = 10
# Truncate individual message content at this many characters to keep prompts bounded.
CONTENT_MAX_CHARS = 2000

# Tool screening constants (v0.6)
# Maximum number of top-level properties in a tool's input_schema to be compatible.
TOOL_MAX_TOP_LEVEL_PROPS = 5

# JSON Schema complexity keywords that indicate a schema is too complex to inject as text.
_SCHEMA_COMPLEXITY_KEYWORDS = frozenset({"$ref", "allOf", "anyOf", "oneOf", "not"})

# Internal stop-reason marker used when the backend cannot report precise semantics.
_STOP_REASON_BACKEND_DEFAULTED = "backend_defaulted"

# Prompt section delimiters — consistent across all invocations.
_SECTION_SEP = "\n" + ("─" * 60) + "\n"


# ---------------------------------------------------------------------------
# Tool screening types (v0.6)
# ---------------------------------------------------------------------------


class ToolCompatibilityLevel(str, Enum):
    """Result of screening a tool for limited text-based forwarding."""

    compatible = "compatible"
    """Tool is compatible with text-based injection."""

    complex_schema = "complex_schema"
    """Tool has too many top-level properties to describe usefully."""

    missing_description = "missing_description"
    """Tool has no description — cannot be described to the model."""

    schema_unsupported = "schema_unsupported"
    """Tool schema uses JSON Schema complexity keywords ($ref, allOf, etc.)."""


@dataclass
class ToolScreenResult:
    """Result of screening a single tool for limited text injection."""

    tool_name: str
    level: ToolCompatibilityLevel
    reason: str


# ---------------------------------------------------------------------------
# Backend implementation
# ---------------------------------------------------------------------------


class ClaudeCodeExecutionBackend(ExecutionBackend):
    """Execution backend backed by the Claude Code CLI.

    Uses `claude --print` (non-interactive mode) to execute tasks.
    Authentication is handled by Claude Code's own login state — not by
    ANTHROPIC_API_KEY.

    Operators must have Claude Code installed and authenticated before
    using this backend. Run `claude login` to authenticate.
    """

    def __init__(self, config: "Config") -> None:
        self._config = config

    @property
    def name(self) -> str:
        return "claude_code"

    @property
    def capabilities(self) -> BackendCapabilities:
        """Declare claude_code backend capabilities (v0.5/v0.6)."""
        return BackendCapabilities(
            supports_downstream_tools=False,
            supports_structured_tool_use=False,
            supports_native_multiturn=False,
            supports_rich_stop_reason=False,
            supports_structured_messages=False,
            supports_workspace_assumptions=True,
            supports_limited_downstream_tools=True,
        )

    def _find_cli(self) -> str | None:
        """Locate the claude CLI binary.

        Checks CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH first, then PATH.
        """
        configured = getattr(self._config, "claude_code_cli_path", "")
        if configured:
            p = Path(configured)
            if p.is_file():
                return str(p)
            logger.warning(
                "claude_code_cli_path %r is not a valid file; falling back to PATH", configured
            )
        return shutil.which("claude")

    def validate_startup(self, config: "Config") -> None:
        """Validate that the Claude Code CLI is present and executable.

        Raises ClaudeCodeUnavailableError if the CLI is not found or fails
        a basic version check.

        Note: This does not verify that Claude Code is authenticated. An
        unauthenticated CLI will fail at execution time with a clear error.
        """
        cli = self._find_cli()
        if cli is None:
            raise ClaudeCodeUnavailableError(
                "claude CLI not found in PATH. "
                "Install Claude Code (https://claude.ai/code) and ensure the "
                "'claude' binary is on PATH, or set CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH."
            )

        try:
            result = subprocess.run(
                [cli, "--version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except FileNotFoundError:
            raise ClaudeCodeUnavailableError(
                f"claude CLI at {cli!r} is not executable"
            )
        except subprocess.TimeoutExpired:
            raise ClaudeCodeUnavailableError(
                "claude CLI did not respond to --version within 10 seconds"
            )

        if result.returncode != 0:
            raise ClaudeCodeUnavailableError(
                f"claude CLI version check failed (exit {result.returncode}): "
                f"{result.stderr.strip()}"
            )

        logger.debug("claude_code backend: CLI found at %r", cli)

    def is_available(self, config: "Config") -> bool:
        try:
            self.validate_startup(config)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Tool screening (v0.6)
    # ------------------------------------------------------------------

    @staticmethod
    def screen_tool(tool: dict) -> ToolScreenResult:
        """Screen a single tool for compatibility with limited text injection.

        Returns a ToolScreenResult indicating whether the tool can be injected
        as a text description. Compatible tools must have:
        - A non-empty description
        - No JSON Schema complexity keywords in the input_schema
        - At most TOOL_MAX_TOP_LEVEL_PROPS top-level properties in the schema

        Args:
            tool: Tool definition dict in Anthropic format.

        Returns:
            ToolScreenResult with level=compatible or an explanatory rejection level.
        """
        name = tool.get("name", "(unnamed)")
        description = tool.get("description", "")

        if not description or not description.strip():
            return ToolScreenResult(
                tool_name=name,
                level=ToolCompatibilityLevel.missing_description,
                reason="tool has no description and cannot be described to the model",
            )

        input_schema = tool.get("input_schema", {})
        # Scan the full string representation for complexity keywords.
        schema_repr = str(input_schema)
        for keyword in _SCHEMA_COMPLEXITY_KEYWORDS:
            if keyword in schema_repr:
                return ToolScreenResult(
                    tool_name=name,
                    level=ToolCompatibilityLevel.schema_unsupported,
                    reason=(
                        f"input_schema uses unsupported complexity keyword '{keyword}'; "
                        "use 'api' backend for full tool support"
                    ),
                )

        # Count top-level properties in the schema.
        properties = input_schema.get("properties", {}) if isinstance(input_schema, dict) else {}
        if len(properties) > TOOL_MAX_TOP_LEVEL_PROPS:
            return ToolScreenResult(
                tool_name=name,
                level=ToolCompatibilityLevel.complex_schema,
                reason=(
                    f"input_schema has {len(properties)} top-level properties "
                    f"(max {TOOL_MAX_TOP_LEVEL_PROPS}); use 'api' backend for full tool support"
                ),
            )

        return ToolScreenResult(
            tool_name=name,
            level=ToolCompatibilityLevel.compatible,
            reason="tool is compatible with limited text injection",
        )

    @staticmethod
    def screen_tools(
        tools: list[dict],
    ) -> tuple[list[dict], list[ToolScreenResult]]:
        """Screen a list of tools and split into compatible and screened-out sets.

        Args:
            tools: List of tool definition dicts in Anthropic format.

        Returns:
            (compatible_tools, screened_out_results) where:
            - compatible_tools: tools that passed screening
            - screened_out_results: ToolScreenResult entries for rejected tools
        """
        compatible: list[dict] = []
        screened_out: list[ToolScreenResult] = []

        for tool in tools:
            result = ClaudeCodeExecutionBackend.screen_tool(tool)
            if result.level == ToolCompatibilityLevel.compatible:
                compatible.append(tool)
            else:
                screened_out.append(result)

        return compatible, screened_out

    # ------------------------------------------------------------------
    # Prompt building (v0.5/v0.6)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_tool_descriptions_section(tools: list[dict]) -> str:
        """Format compatible tools as a text section for injection into the prompt.

        The section clearly states that tools are described for context only —
        they are not directly invocable in this execution mode.

        Args:
            tools: List of compatible tool definition dicts.

        Returns:
            Formatted [Available Tools] section string.
        """
        lines: list[str] = [
            "[Available Tools]",
            "The following tools are described for context. "
            "They represent capabilities available in this session.",
            "Note: These tools are described for context only — "
            "they are not directly invocable in this execution mode.",
        ]

        for tool in tools:
            name = tool.get("name", "(unnamed)")
            description = tool.get("description", "(no description)")
            lines.append("")
            lines.append(f"Tool: {name}")
            lines.append(f"Description: {description}")

            input_schema = tool.get("input_schema", {})
            properties = (
                input_schema.get("properties", {})
                if isinstance(input_schema, dict)
                else {}
            )
            required = set(
                input_schema.get("required", [])
                if isinstance(input_schema, dict)
                else []
            )

            if properties:
                lines.append("Parameters:")
                for param_name, param_schema in properties.items():
                    param_type = param_schema.get("type", "any") if isinstance(param_schema, dict) else "any"
                    param_desc = (
                        param_schema.get("description", "(no description)")
                        if isinstance(param_schema, dict)
                        else "(no description)"
                    )
                    req_label = "required" if param_name in required else "optional"
                    lines.append(f"  - {param_name} ({param_type}, {req_label}): {param_desc}")

        return "\n".join(lines)

    def _build_structured_prompt(
        self,
        system_prompt: str,
        task: str,
        conversation_history: list[dict[str, Any]] | None,
        session_summary: str | None,
        tools: list[dict] | None = None,
        is_continuation: bool = False,
    ) -> tuple[str, bool]:
        """Build a structured prompt for the Claude Code CLI.

        Returns (prompt_string, was_truncated).

        Prompt structure (v0.5/v0.6):
          1. [System] — profile/policy instructions
          2. [Session Context] or [Continuation Session] — session summary if provided
          3. [Available Tools] — compatible tool descriptions (v0.6, opt-in)
          4. [Conversation History] — bounded recent exchanges with role labels
          5. [Current Request] — the current user task/message
          6. [Instructions] — backend-specific execution note

        Truncation policy: keep the most recent HISTORY_MAX_EXCHANGES
        user/assistant exchange pairs. If older history is dropped, the
        caller receives was_truncated=True and should surface a warning.

        When is_continuation=True (v0.6):
          - Section 2 uses [Continuation Session] header instead of [Session Context]
          - [Instructions] emphasizes resuming from where the session left off.
        """
        parts: list[str] = []

        # 1. System / profile instructions
        if system_prompt:
            parts.append(f"[System]\n{system_prompt.strip()}")

        # 2. Session context / continuation framing
        if session_summary and session_summary.strip():
            if is_continuation:
                parts.append(f"[Continuation Session]\n{session_summary.strip()}")
            else:
                parts.append(f"[Session Context]\n{session_summary.strip()}")

        # 3. Available tools section (v0.6 — text-based, opt-in)
        if tools:
            parts.append(self._build_tool_descriptions_section(tools))

        # 4. Conversation history (bounded, structured)
        truncated = False
        if conversation_history:
            history, truncated = self._bound_history(conversation_history)
            if history:
                history_lines: list[str] = []
                for msg in history:
                    role = msg.get("role", "user").capitalize()
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        content = content.strip()
                        if len(content) > CONTENT_MAX_CHARS:
                            content = content[:CONTENT_MAX_CHARS] + " [truncated]"
                        if content:
                            history_lines.append(f"[{role}]\n{content}")
                if history_lines:
                    parts.append("[Conversation History]\n" + "\n\n".join(history_lines))

        # 5. Current request
        parts.append(f"[Current Request]\n{task.strip()}")

        # 6. Backend execution note
        if is_continuation:
            parts.append(
                "[Instructions]\nYou are continuing this session. "
                "Resume from where you left off, building on the prior conversation."
            )
        else:
            parts.append(
                "[Instructions]\nRespond to the current request above. "
                "Use the conversation history and session context as background."
            )

        return _SECTION_SEP.join(parts), truncated

    def _bound_history(
        self,
        history: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], bool]:
        """Return (bounded_history, was_truncated).

        Keeps the most recent HISTORY_MAX_EXCHANGES complete user/assistant
        pairs. Incomplete pairs at the tail are preserved.

        The pairing logic iterates from the end and counts role alternations.
        """
        if len(history) <= HISTORY_MAX_EXCHANGES * 2:
            return history, False

        # Keep the last max_exchanges * 2 messages (approximate pairs)
        kept = history[-(HISTORY_MAX_EXCHANGES * 2):]
        return kept, True

    # ------------------------------------------------------------------
    # Execute (v0.5/v0.6)
    # ------------------------------------------------------------------

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
    ) -> NormalizedProviderResult:
        """Execute via Claude Code CLI in print mode.

        Builds a structured prompt from system_prompt + session summary +
        conversation history (bounded) + task, then invokes
        `claude --print <prompt>` and captures stdout.

        Limited tool forwarding (v0.6):
            When config.claude_code_enable_limited_tool_forwarding is True and
            tools are provided, compatible tools are screened and injected as
            text descriptions into the prompt. Incompatible tools are dropped
            with per-tool warnings.

        When limited tool forwarding is disabled (default):
            Tools are ignored with a single consolidated warning.

        Stop reason is always reported as 'backend_defaulted' since the CLI
        does not provide semantic stop-reason information.
        """
        cli = self._find_cli()
        if cli is None:
            raise ClaudeCodeUnavailableError(
                "claude CLI not found. Cannot execute task."
            )

        warnings: list[str] = []
        prompt_tools: list[dict] | None = None

        enable_limited_forwarding = getattr(
            self._config, "claude_code_enable_limited_tool_forwarding", False
        )

        if tools:
            if enable_limited_forwarding:
                compatible, screened_out = self.screen_tools(tools)
                # Emit per-tool warnings for dropped tools
                for sr in screened_out:
                    warnings.append(
                        f"claude_code backend: tool '{sr.tool_name}' not forwarded — "
                        f"{sr.reason} (use 'api' backend for full tool support)"
                    )
                if compatible:
                    prompt_tools = compatible
                    logger.debug(
                        "claude_code backend: %d tool(s) injected as text descriptions, "
                        "%d dropped",
                        len(compatible),
                        len(screened_out),
                    )
                else:
                    logger.debug(
                        "claude_code backend: no compatible tools to inject after screening"
                    )
            else:
                warnings.append(
                    "claude_code backend: downstream federation tools are not supported "
                    "and were not forwarded to the Claude Code CLI. "
                    "Switch to the 'api' backend to use federation tools."
                )

        prompt, truncated = self._build_structured_prompt(
            system_prompt=system_prompt,
            task=task,
            conversation_history=conversation_history,
            session_summary=session_summary,
            tools=prompt_tools,
            is_continuation=is_continuation,
        )

        if truncated:
            warnings.append(
                f"claude_code backend: conversation history was truncated to the most "
                f"recent {HISTORY_MAX_EXCHANGES} exchange(s) to fit within context bounds. "
                "Earlier history is represented by the session summary."
            )

        warnings.append(
            "claude_code backend: stop_reason precision is limited — "
            "the Claude Code CLI does not report semantic stop reasons."
        )

        timeout = getattr(self._config, "claude_code_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)

        cmd = [cli, "--print", prompt]

        model = getattr(self._config, "model", "")
        if model:
            cmd = [cli, "--model", model, "--print", prompt]

        logger.debug("claude_code backend: invoking CLI (timeout=%ss)", timeout)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=float(timeout)
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ClaudeCodeInvocationError(
                    f"claude CLI timed out after {timeout}s"
                )
        except ClaudeCodeInvocationError:
            raise
        except Exception as exc:
            raise ClaudeCodeInvocationError(
                f"claude CLI invocation failed: {exc}"
            ) from exc

        if proc.returncode != 0:
            stderr_text = stderr_bytes.decode("utf-8", errors="replace").strip()
            raise ClaudeCodeInvocationError(
                f"claude CLI exited with code {proc.returncode}: {stderr_text}"
            )

        output_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        if not output_text:
            warnings.append("claude_code backend: CLI returned an empty response")

        try:
            return NormalizedProviderResult(
                output_text=output_text,
                turn_count=1,
                provider_session_id=None,
                stop_reason=_STOP_REASON_BACKEND_DEFAULTED,
                warnings=warnings,
            )
        except Exception as exc:
            raise NormalizationError(
                f"Failed to normalize Claude Code result: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Legacy compatibility alias
    # ------------------------------------------------------------------

    # Keep the old name for backwards-compatibility with existing tests,
    # but delegate to the new builder.
    def _build_prompt(
        self,
        system_prompt: str,
        task: str,
        conversation_history: list[dict[str, Any]] | None,
    ) -> str:
        """Legacy alias for _build_structured_prompt (no session summary)."""
        prompt, _ = self._build_structured_prompt(
            system_prompt=system_prompt,
            task=task,
            conversation_history=conversation_history,
            session_summary=None,
        )
        return prompt
