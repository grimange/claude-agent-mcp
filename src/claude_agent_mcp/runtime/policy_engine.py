"""Policy engine — evaluates and enforces runtime constraints before execution.

The policy engine is the authoritative decision point. It always fails closed.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from claude_agent_mcp.config import Config
from claude_agent_mcp.errors import PolicyDeniedError, ValidationError
from claude_agent_mcp.runtime.profile_registry import Profile
from claude_agent_mcp.types import ProfileName, SessionStatus

logger = logging.getLogger(__name__)


class PolicyEngine:
    """Enforces policy rules derived from the active profile and server config."""

    def __init__(self, config: Config) -> None:
        self._config = config

    def validate_run_request(
        self,
        profile: Profile,
        working_directory: str | None,
        max_turns: int,
        attachments: list[str],
    ) -> str:
        """Validate a run_task or verify_task request.

        Returns the resolved (absolute) working directory string.
        Raises PolicyDeniedError or ValidationError on failure.
        """
        resolved_dir = self._resolve_working_directory(profile, working_directory)
        self._validate_turns(profile, max_turns)
        self._validate_attachments(profile, attachments)
        return resolved_dir

    def validate_continuation(
        self,
        profile: Profile,
        session_status: SessionStatus,
        current_turn_count: int,
        max_turns: int,
    ) -> None:
        """Validate that a session continuation is policy-allowed."""
        allowed_statuses = {SessionStatus.completed, SessionStatus.failed, SessionStatus.interrupted}
        if session_status not in allowed_statuses:
            # running is blocked by locking, created is okay to continue
            if session_status == SessionStatus.running:
                raise PolicyDeniedError(
                    "Session is currently running — use locking to gate concurrent access"
                )

        self._validate_turns(profile, max_turns)

    # ------------------------------------------------------------------
    # Internal validators
    # ------------------------------------------------------------------

    def _resolve_working_directory(
        self, profile: Profile, requested: str | None
    ) -> str:
        wd_policy = profile.working_directory_policy

        if requested:
            resolved = str(Path(requested).resolve())
        elif wd_policy.allow_cwd_fallback:
            resolved = str(Path.cwd().resolve())
        elif wd_policy.require_explicit:
            raise PolicyDeniedError(
                f"Profile '{profile.name}' requires an explicit working_directory"
            )
        else:
            resolved = str(Path.cwd().resolve())

        if wd_policy.validate_against_allowlist:
            if not self._is_allowed_dir(resolved):
                raise PolicyDeniedError(
                    f"working_directory '{resolved}' is not in the server's allowed_dirs. "
                    f"Allowed: {self._config.allowed_dirs}"
                )

        return resolved

    def _is_allowed_dir(self, resolved: str) -> bool:
        for allowed in self._config.allowed_dirs:
            allowed_path = Path(allowed).resolve()
            candidate = Path(resolved)
            try:
                candidate.relative_to(allowed_path)
                return True
            except ValueError:
                continue
        return False

    def _validate_turns(self, profile: Profile, max_turns: int) -> None:
        if max_turns < 1:
            raise ValidationError(f"max_turns must be >= 1, got {max_turns}")
        if max_turns > profile.max_turns_max:
            raise PolicyDeniedError(
                f"max_turns {max_turns} exceeds profile cap {profile.max_turns_max}"
            )

    def _validate_attachments(self, profile: Profile, attachments: list[str]) -> None:
        for attachment in attachments:
            path = Path(attachment)
            if not path.is_absolute():
                path = Path.cwd() / path
            path = path.resolve()

            if not path.exists():
                raise ValidationError(f"Attachment path does not exist: {attachment}")

            if profile.read_only:
                # Read-only profiles can still read attachments — allowed.
                pass

            # Validate attachment is within an allowed directory.
            if not self._is_allowed_dir(str(path.parent)):
                raise PolicyDeniedError(
                    f"Attachment '{attachment}' is outside allowed directories"
                )
