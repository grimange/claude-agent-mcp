"""Federation package for claude-agent-mcp v0.3.

Provides governed downstream MCP interoperability:
- Static downstream server registration
- Startup-time tool discovery
- Explicit allowlist filtering
- Profile-based visibility gating
- Bounded invocation layer
- Session audit logging

Federation is disabled by default. Operators must explicitly enable it
and configure downstream servers. Tools are deny-by-default.
"""

from __future__ import annotations

import logging

from claude_agent_mcp.config import Config
from claude_agent_mcp.federation.catalog import ToolCatalog
from claude_agent_mcp.federation.connections import DownstreamConnectionManager
from claude_agent_mcp.federation.models import DownstreamServerConfig
from claude_agent_mcp.federation.registry import DownstreamRegistry
from claude_agent_mcp.federation.visibility import ToolVisibilityResolver

logger = logging.getLogger(__name__)

__all__ = [
    "FederationManager",
    "DownstreamRegistry",
    "ToolCatalog",
    "ToolVisibilityResolver",
    "DownstreamConnectionManager",
    "DownstreamServerConfig",
]


class FederationManager:
    """Top-level federation coordinator.

    Initialized at server startup. Owns the registry, catalog, and
    visibility resolver for the lifetime of the server process.

    If federation is disabled, the manager returns empty catalogs and
    resolvers that expose no downstream tools.
    """

    def __init__(
        self,
        registry: DownstreamRegistry,
        catalog: ToolCatalog,
        server_configs: list[DownstreamServerConfig],
    ) -> None:
        self._registry = registry
        self._catalog = catalog
        self._server_configs = server_configs
        self._visibility_resolver = ToolVisibilityResolver(catalog)

    @classmethod
    async def build(cls, config: Config) -> "FederationManager":
        """Build a FederationManager from the server config.

        If federation is disabled, returns a no-op manager with an empty catalog.
        If federation is enabled but no config file is given, logs a warning and
        returns a no-op manager.
        """
        if not config.federation_enabled:
            logger.debug("Federation is disabled — no downstream tools will be available")
            return cls._empty()

        if config.federation_config_path is None:
            logger.warning(
                "Federation is enabled but CLAUDE_AGENT_MCP_FEDERATION_CONFIG is not set — "
                "no downstream servers will be configured"
            )
            return cls._empty()

        if not config.federation_config_path.exists():
            logger.error(
                "Federation config file not found: %s — federation will be disabled",
                config.federation_config_path,
            )
            return cls._empty()

        # Load and validate downstream server configs
        from claude_agent_mcp.errors import DownstreamServerConfigError

        try:
            registry = DownstreamRegistry.from_config_file(config.federation_config_path)
        except DownstreamServerConfigError as exc:
            logger.error(
                "Federation config error: %s — federation will be disabled",
                exc.message,
            )
            return cls._empty()

        enabled_servers = registry.enabled_servers()
        if not enabled_servers:
            logger.info("No enabled downstream servers configured — federation active but empty")
            return cls(
                registry=registry,
                catalog=ToolCatalog.empty(),
                server_configs=[],
            )

        # Discover tools at startup
        conn_manager = DownstreamConnectionManager()
        discovered = await conn_manager.discover_all(enabled_servers)

        # Build catalog with allowlist applied
        catalog = ToolCatalog.build(discovered, enabled_servers)

        logger.info(
            "Federation initialized: %d server(s), %d tool(s) discovered, %d allowlisted",
            len(enabled_servers),
            len(catalog.all_tools()),
            len(catalog.allowed_tools()),
        )

        return cls(
            registry=registry,
            catalog=catalog,
            server_configs=enabled_servers,
        )

    @classmethod
    def _empty(cls) -> "FederationManager":
        return cls(
            registry=DownstreamRegistry(),
            catalog=ToolCatalog.empty(),
            server_configs=[],
        )

    @property
    def visibility_resolver(self) -> ToolVisibilityResolver:
        """Return the visibility resolver for injecting tools into sessions."""
        return self._visibility_resolver

    @property
    def server_configs(self) -> list[DownstreamServerConfig]:
        """Return all enabled server configs (for invocation routing)."""
        return list(self._server_configs)

    @property
    def catalog(self) -> ToolCatalog:
        return self._catalog

    def is_active(self) -> bool:
        """Return True if federation is active with at least one allowlisted tool."""
        return len(self._catalog.allowed_tools()) > 0
