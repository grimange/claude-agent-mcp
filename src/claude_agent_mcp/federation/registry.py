"""Downstream server registry — loads and validates configured downstream MCP servers.

Federation is opt-in. Servers must be explicitly configured.
Invalid configs fail early and clearly before startup completes.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from claude_agent_mcp.errors import DownstreamServerConfigError
from claude_agent_mcp.federation.models import DownstreamServerConfig

logger = logging.getLogger(__name__)

VALID_TRANSPORTS = {"stdio"}


class DownstreamRegistry:
    """Loads and validates downstream server configurations.

    A registry instance is immutable after creation. The server list
    is set once at startup and never modified at runtime.
    """

    def __init__(self, servers: list[DownstreamServerConfig] | None = None) -> None:
        self._servers: list[DownstreamServerConfig] = servers or []

    @classmethod
    def from_config_file(cls, path: Path) -> "DownstreamRegistry":
        """Load registry from a JSON federation config file.

        Raises DownstreamServerConfigError on missing file, bad JSON, or invalid schema.
        """
        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise DownstreamServerConfigError(
                f"Cannot read federation config file {path}: {exc}"
            ) from exc

        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise DownstreamServerConfigError(
                f"Federation config file {path} is not valid JSON: {exc}"
            ) from exc

        if not isinstance(data, dict):
            raise DownstreamServerConfigError(
                f"Federation config file {path} must be a JSON object"
            )

        raw_servers = data.get("downstream_servers", [])
        if not isinstance(raw_servers, list):
            raise DownstreamServerConfigError(
                "Federation config 'downstream_servers' must be a list"
            )

        servers = [cls._parse_server(raw) for raw in raw_servers]
        logger.info("Loaded %d downstream server(s) from %s", len(servers), path)
        return cls(servers=servers)

    @classmethod
    def from_dict_list(cls, server_dicts: list[dict[str, Any]]) -> "DownstreamRegistry":
        """Build a registry from raw config dicts (useful for testing and injection)."""
        servers = [cls._parse_server(d) for d in server_dicts]
        return cls(servers=servers)

    @staticmethod
    def _parse_server(raw: Any) -> DownstreamServerConfig:
        """Parse and validate a single server config dict."""
        if not isinstance(raw, dict):
            raise DownstreamServerConfigError(
                f"Each downstream server config must be a JSON object, got {type(raw).__name__}"
            )

        name = raw.get("name")
        if not name or not isinstance(name, str) or not name.strip():
            raise DownstreamServerConfigError(
                "Downstream server 'name' is required and must be a non-empty string"
            )
        name = name.strip()

        # Validate name — used as prefix in normalized tool names
        if "__" in name:
            raise DownstreamServerConfigError(
                f"Downstream server name {name!r} must not contain '__' "
                "(reserved for normalized tool name separator)"
            )

        transport = raw.get("transport", "stdio")
        if transport not in VALID_TRANSPORTS:
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: unsupported transport {transport!r}. "
                f"Supported transports: {sorted(VALID_TRANSPORTS)}"
            )

        command = raw.get("command")
        if not command or not isinstance(command, str):
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: 'command' is required for stdio transport"
            )

        args = raw.get("args", [])
        if not isinstance(args, list):
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: 'args' must be a list"
            )

        env = raw.get("env", {})
        if not isinstance(env, dict):
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: 'env' must be a JSON object"
            )

        allowed_tools = raw.get("allowed_tools", [])
        if not isinstance(allowed_tools, list):
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: 'allowed_tools' must be a list"
            )
        # Warn but don't fail — an empty allowlist means no tools are exposed
        if not allowed_tools:
            logger.warning(
                "Downstream server %r has an empty 'allowed_tools' list — "
                "no tools from this server will be exposed",
                name,
            )

        profiles_allowed = raw.get("profiles_allowed", [])
        if not isinstance(profiles_allowed, list):
            raise DownstreamServerConfigError(
                f"Downstream server {name!r}: 'profiles_allowed' must be a list"
            )

        return DownstreamServerConfig(
            name=name,
            transport=transport,
            command=command,
            args=[str(a) for a in args],
            env={str(k): str(v) for k, v in env.items()},
            enabled=bool(raw.get("enabled", True)),
            discovery_timeout_seconds=float(raw.get("discovery_timeout_seconds", 10.0)),
            allowed_tools=[str(t) for t in allowed_tools],
            profiles_allowed=[str(p) for p in profiles_allowed],
        )

    def enabled_servers(self) -> list[DownstreamServerConfig]:
        """Return only servers where enabled=True. Disabled servers are silently ignored."""
        return [s for s in self._servers if s.enabled]

    def all_servers(self) -> list[DownstreamServerConfig]:
        """Return all servers regardless of enabled state."""
        return list(self._servers)

    def get_server(self, name: str) -> DownstreamServerConfig | None:
        """Look up a server by name. Returns None if not found."""
        for s in self._servers:
            if s.name == name:
                return s
        return None
