"""Federation tool catalog — normalizes discovered tools and applies allowlisting.

The catalog is the authoritative internal source of which tools exist, which
are allowlisted, and which profiles may see them.

Rules:
- A discovered tool is NOT usable until it appears in the server's allowed_tools list.
- allowed_tools matching is exact (exact downstream tool name, not normalized name).
- Normalized names use the pattern: {server_name}__{tool_name}
- Name collisions across servers are detected and logged; first registration wins.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace

from claude_agent_mcp.federation.models import DiscoveredTool, DownstreamServerConfig

logger = logging.getLogger(__name__)


@dataclass
class ToolCatalog:
    """Immutable catalog of federation tools after discovery and allowlist filtering.

    All tools in the catalog have been discovered. The `allowed` flag indicates
    whether they passed the allowlist check.
    """

    # normalized_name -> DiscoveredTool
    _tools: dict[str, DiscoveredTool] = field(default_factory=dict)

    @classmethod
    def build(
        cls,
        discovered: list[DiscoveredTool],
        server_configs: list[DownstreamServerConfig],
    ) -> "ToolCatalog":
        """Build a catalog from discovered tools and their server configs.

        Applies allowlist filtering and profile visibility from server configs.
        Detects and logs normalized name collisions (first wins).
        """
        # Build lookup: server_name -> config
        config_map: dict[str, DownstreamServerConfig] = {
            sc.name: sc for sc in server_configs
        }

        tools: dict[str, DiscoveredTool] = {}

        for tool in discovered:
            server_cfg = config_map.get(tool.downstream_server_name)
            if server_cfg is None:
                logger.warning(
                    "Discovered tool %r has no matching server config — skipping",
                    tool.normalized_name,
                )
                continue

            # Check for name collision
            if tool.normalized_name in tools:
                logger.warning(
                    "Normalized tool name collision: %r already registered — "
                    "duplicate from server %r will be ignored",
                    tool.normalized_name,
                    tool.downstream_server_name,
                )
                continue

            # Apply allowlist: tool is allowed if its downstream name is in allowed_tools
            allowed = tool.downstream_tool_name in server_cfg.allowed_tools

            catalogued = replace(
                tool,
                allowed=allowed,
                profiles_allowed=list(server_cfg.profiles_allowed),
            )
            tools[tool.normalized_name] = catalogued

        if tools:
            allowed_count = sum(1 for t in tools.values() if t.allowed)
            logger.info(
                "Tool catalog built: %d discovered, %d allowlisted",
                len(tools),
                allowed_count,
            )

        return cls(_tools=tools)

    def all_tools(self) -> list[DiscoveredTool]:
        """All tools — discovered but not necessarily allowed."""
        return list(self._tools.values())

    def allowed_tools(self) -> list[DiscoveredTool]:
        """Only tools that passed the allowlist check."""
        return [t for t in self._tools.values() if t.allowed]

    def get(self, normalized_name: str) -> DiscoveredTool | None:
        """Look up a single tool by normalized name."""
        return self._tools.get(normalized_name)

    def is_allowed(self, normalized_name: str) -> bool:
        tool = self._tools.get(normalized_name)
        return tool is not None and tool.allowed

    @classmethod
    def empty(cls) -> "ToolCatalog":
        """Return an empty catalog (federation disabled or no servers)."""
        return cls(_tools={})
