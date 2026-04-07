"""Profile registry — loads and resolves built-in execution policy bundles.

A profile is not a prompt preset. It is a full execution policy that controls
tool access, filesystem permissions, turn caps, timeouts, and fail-closed behavior.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claude_agent_mcp.errors import ConfigurationError, ValidationError
from claude_agent_mcp.prompts.system_profiles import (
    GENERAL_SYSTEM_PROMPT,
    VERIFICATION_SYSTEM_PROMPT,
)
from claude_agent_mcp.types import ProfileName, ToolClass


@dataclass(frozen=True)
class ArtifactPolicy:
    allow_write: bool = True
    max_size_bytes: int = 10 * 1024 * 1024  # 10 MB
    allowed_types: tuple[str, ...] = ("report", "summary", "plan", "output")


@dataclass(frozen=True)
class WorkingDirectoryPolicy:
    """Controls filesystem access for a profile."""

    require_explicit: bool = False  # if True, working_directory must be provided
    allow_cwd_fallback: bool = True  # if True, fall back to server CWD when not provided
    validate_against_allowlist: bool = True  # check against server-level allowed_dirs


@dataclass(frozen=True)
class Profile:
    name: ProfileName
    system_prompt: str
    allowed_tool_classes: tuple[ToolClass, ...]
    read_only: bool
    working_directory_policy: WorkingDirectoryPolicy
    max_turns_default: int
    max_turns_max: int
    timeout_seconds_default: int
    timeout_seconds_max: int
    artifact_policy: ArtifactPolicy
    result_schema: str  # logical name of expected result shape
    fail_closed: bool


# ---------------------------------------------------------------------------
# Built-in profile definitions
# ---------------------------------------------------------------------------

GENERAL_PROFILE = Profile(
    name=ProfileName.general,
    system_prompt=GENERAL_SYSTEM_PROMPT,
    allowed_tool_classes=(
        ToolClass.workspace_read,
        ToolClass.workspace_write,
        ToolClass.artifact_write,
        ToolClass.state_inspection,
    ),
    read_only=False,
    working_directory_policy=WorkingDirectoryPolicy(
        require_explicit=False,
        allow_cwd_fallback=True,
        validate_against_allowlist=True,
    ),
    max_turns_default=10,
    max_turns_max=50,
    timeout_seconds_default=300,
    timeout_seconds_max=900,
    artifact_policy=ArtifactPolicy(allow_write=True),
    result_schema="run_task_result",
    fail_closed=True,
)

VERIFICATION_PROFILE = Profile(
    name=ProfileName.verification,
    system_prompt=VERIFICATION_SYSTEM_PROMPT,
    allowed_tool_classes=(
        ToolClass.workspace_read,
        ToolClass.state_inspection,
    ),
    read_only=True,
    working_directory_policy=WorkingDirectoryPolicy(
        require_explicit=False,
        allow_cwd_fallback=True,
        validate_against_allowlist=True,
    ),
    max_turns_default=5,
    max_turns_max=20,
    timeout_seconds_default=180,
    timeout_seconds_max=600,
    artifact_policy=ArtifactPolicy(
        allow_write=True,
        allowed_types=("verification-report",),
    ),
    result_schema="verify_task_result",
    fail_closed=True,
)

_REGISTRY: dict[ProfileName, Profile] = {
    ProfileName.general: GENERAL_PROFILE,
    ProfileName.verification: VERIFICATION_PROFILE,
}


class ProfileRegistry:
    """Resolves profile names to Policy bundles."""

    def get(self, name: ProfileName) -> Profile:
        profile = _REGISTRY.get(name)
        if profile is None:
            raise ConfigurationError(f"Unknown profile: {name}")
        return profile

    def resolve_turns(self, profile: Profile, requested: int | None) -> int:
        """Return clamped turn count respecting profile caps."""
        default = profile.max_turns_default
        cap = profile.max_turns_max
        turns = requested if requested is not None else default
        return min(turns, cap)

    def resolve_timeout(self, profile: Profile, requested: int | None) -> int:
        """Return clamped timeout respecting profile caps."""
        default = profile.timeout_seconds_default
        cap = profile.timeout_seconds_max
        timeout = requested if requested is not None else default
        return min(timeout, cap)
