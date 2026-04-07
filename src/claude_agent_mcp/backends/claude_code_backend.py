"""Claude Code execution backend for claude-agent-mcp (v0.4).

Executes tasks through the Claude Code CLI rather than direct API calls.
This allows operators who have Claude Code installed (and authenticated via
Claude Code's own auth flow) to use claude-agent-mcp without an API key.

Authentication model:
    Claude Code backend does NOT use ANTHROPIC_API_KEY.
    It relies on Claude Code's own authentication state, established by
    running `claude login` in the operator's environment.

Implementation:
    CLI-backed. Invokes `claude -p "<prompt>"` as a subprocess and collects
    stdout as the normalized output text.

Limitations (v0.4):
    - Single-turn execution only (no native multi-turn tool-use loop).
    - Downstream federation tools are not forwarded to the CLI invocation.
    - Conversation history is serialized as plain text in the prompt.
    - Output normalization is best-effort (no stop_reason from CLI).
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claude_agent_mcp.backends.base import ExecutionBackend, ToolExecutor
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


class ClaudeCodeExecutionBackend(ExecutionBackend):
    """Execution backend backed by the Claude Code CLI.

    Uses `claude -p` (print/non-interactive mode) to execute tasks.
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
    ) -> NormalizedProviderResult:
        """Execute via Claude Code CLI in print mode.

        Builds a prompt string from system_prompt + conversation history + task,
        then invokes `claude -p <prompt>` and captures stdout.

        If downstream tools are provided, they are noted as a warning — the
        claude_code backend does not forward federation tools to the CLI in v0.4.
        """
        cli = self._find_cli()
        if cli is None:
            raise ClaudeCodeUnavailableError(
                "claude CLI not found. Cannot execute task."
            )

        prompt = self._build_prompt(system_prompt, task, conversation_history)
        warnings: list[str] = []

        if tools:
            warnings.append(
                "claude_code backend: downstream federation tools are not forwarded "
                "to the Claude Code CLI in v0.4. Tools were ignored."
            )

        timeout = getattr(self._config, "claude_code_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)

        cmd = [cli, "--print", prompt]

        # Pass model hint if configured and it is a known API model name.
        # The claude CLI may not accept all model IDs; skip silently if not set.
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
            warnings.append("Claude Code backend returned an empty response")

        try:
            return NormalizedProviderResult(
                output_text=output_text,
                turn_count=1,
                provider_session_id=None,
                stop_reason="end_turn",
                warnings=warnings,
            )
        except Exception as exc:
            raise NormalizationError(
                f"Failed to normalize Claude Code result: {exc}"
            ) from exc

    def _build_prompt(
        self,
        system_prompt: str,
        task: str,
        conversation_history: list[dict[str, Any]] | None,
    ) -> str:
        """Serialize system prompt + history + task into a single CLI prompt string.

        The Claude Code CLI does not natively accept structured conversation turns,
        so history is rendered as labelled plain-text blocks.
        """
        parts: list[str] = []

        if system_prompt:
            parts.append(f"[System]\n{system_prompt}")
            parts.append("")

        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role", "user").capitalize()
                content = msg.get("content", "")
                if isinstance(content, str) and content.strip():
                    parts.append(f"[{role}]\n{content}")
                    parts.append("")

        parts.append(f"[User]\n{task}")
        return "\n".join(parts)
