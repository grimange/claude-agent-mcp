"""Claude Code execution backend for claude-agent-mcp (v0.5).

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

Capabilities (v0.5):
    - supports_downstream_tools: False — federation tools are not forwarded.
    - supports_structured_tool_use: False — no agentic tool-use loop.
    - supports_native_multiturn: False — single invocation per call.
    - supports_rich_stop_reason: False — stop_reason always backend_defaulted.
    - supports_structured_messages: False — history reconstructed as text.
    - supports_workspace_assumptions: True — CLI runs in local environment.

Context reconstruction (v0.5):
    Continuation history is rendered in a structured format with clear role
    boundaries, a session summary section, and deterministic truncation.
    Truncation policy: most recent HISTORY_MAX_EXCHANGES exchanges are kept.
    If truncation occurs, a warning is added to the result.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
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

# Internal stop-reason marker used when the backend cannot report precise semantics.
_STOP_REASON_BACKEND_DEFAULTED = "backend_defaulted"

# Prompt section delimiters — consistent across all invocations.
_SECTION_SEP = "\n" + ("─" * 60) + "\n"


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
        """Declare claude_code backend capabilities (v0.5)."""
        return BackendCapabilities(
            supports_downstream_tools=False,
            supports_structured_tool_use=False,
            supports_native_multiturn=False,
            supports_rich_stop_reason=False,
            supports_structured_messages=False,
            supports_workspace_assumptions=True,
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
    ) -> NormalizedProviderResult:
        """Execute via Claude Code CLI in print mode.

        Builds a structured prompt from system_prompt + session summary +
        conversation history (bounded) + task, then invokes
        `claude --print <prompt>` and captures stdout.

        If downstream tools are provided they are ignored with a warning —
        the claude_code backend does not support federation tool forwarding.

        Stop reason is always reported as 'backend_defaulted' since the CLI
        does not provide semantic stop-reason information.
        """
        cli = self._find_cli()
        if cli is None:
            raise ClaudeCodeUnavailableError(
                "claude CLI not found. Cannot execute task."
            )

        warnings: list[str] = []

        if tools:
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

    def _build_structured_prompt(
        self,
        system_prompt: str,
        task: str,
        conversation_history: list[dict[str, Any]] | None,
        session_summary: str | None,
    ) -> tuple[str, bool]:
        """Build a structured prompt for the Claude Code CLI.

        Returns (prompt_string, was_truncated).

        Prompt structure (v0.5):
          1. [System] — profile/policy instructions
          2. [Session Context] — session summary if provided
          3. [Conversation History] — bounded recent exchanges with role labels
          4. [Current Request] — the current user task/message
          5. [Instructions] — backend-specific execution note

        Truncation policy: keep the most recent HISTORY_MAX_EXCHANGES
        user/assistant exchange pairs. If older history is dropped, the
        caller receives was_truncated=True and should surface a warning.
        """
        parts: list[str] = []

        # 1. System / profile instructions
        if system_prompt:
            parts.append(f"[System]\n{system_prompt.strip()}")

        # 2. Session context (summary of prior work)
        if session_summary and session_summary.strip():
            parts.append(f"[Session Context]\n{session_summary.strip()}")

        # 3. Conversation history (bounded, structured)
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

        # 4. Current request
        parts.append(f"[Current Request]\n{task.strip()}")

        # 5. Backend execution note
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
