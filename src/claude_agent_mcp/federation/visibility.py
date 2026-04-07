"""Federation tool visibility resolver.

Visibility is resolved per session at execution time based on the active profile.

Rules:
- A tool must be allowlisted in the catalog before it can be visible.
- Even an allowlisted tool is only visible if the active profile is in
  the tool's profiles_allowed list.
- The 'verification' profile has NO downstream tool visibility by default.
  It gains visibility only if explicitly added to a server's profiles_allowed.
- The 'general' profile may receive explicitly allowed downstream tools.
"""

from __future__ import annotations

import logging

from claude_agent_mcp.federation.catalog import ToolCatalog
from claude_agent_mcp.federation.models import DiscoveredTool
from claude_agent_mcp.types import ProfileName

logger = logging.getLogger(__name__)

# Profiles that must never receive downstream tools unless explicitly permitted.
# Listed for documentation purposes — enforcement is through the profiles_allowed check.
_RESTRICTED_BY_DEFAULT: frozenset[str] = frozenset({"verification"})


class ToolVisibilityResolver:
    """Resolves which downstream tools are visible for a given execution profile.

    This is the gating layer between the allowlist catalog and the provider adapter.
    The adapter receives only the filtered visible set — it does not see the global catalog.
    """

    def __init__(self, catalog: ToolCatalog) -> None:
        self._catalog = catalog

    def resolve(self, profile: ProfileName) -> list[DiscoveredTool]:
        """Return the set of downstream tools visible to the given profile.

        A tool is visible if and only if:
        1. It is in the catalog (discovered).
        2. It is allowlisted (allowed=True).
        3. The profile name is in the tool's profiles_allowed list.
        """
        profile_str = profile.value
        visible = []

        for tool in self._catalog.allowed_tools():
            if profile_str in tool.profiles_allowed:
                visible.append(tool)

        if visible:
            logger.debug(
                "Profile %r: %d downstream tool(s) visible: %s",
                profile_str,
                len(visible),
                [t.normalized_name for t in visible],
            )
        else:
            logger.debug("Profile %r: no downstream tools visible", profile_str)

        return visible

    def is_visible(self, normalized_name: str, profile: ProfileName) -> bool:
        """Check whether a specific tool is visible for the given profile."""
        tool = self._catalog.get(normalized_name)
        if tool is None or not tool.allowed:
            return False
        return profile.value in tool.profiles_allowed

    @classmethod
    def from_empty(cls) -> "ToolVisibilityResolver":
        """Return a resolver with an empty catalog (federation disabled or no tools)."""
        return cls(catalog=ToolCatalog.empty())
