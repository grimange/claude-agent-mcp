"""Federation data models for claude-agent-mcp v0.3.

These models represent the internal contracts for downstream MCP federation.
No provider-specific or transport-specific types should appear here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DownstreamServerConfig:
    """Configuration for a single downstream MCP server.

    Operators explicitly register downstream servers. A configured server
    may exist but be disabled — disabled servers are always ignored.
    """

    name: str
    transport: str  # "stdio" for v0.3
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    enabled: bool = True
    discovery_timeout_seconds: float = 10.0
    # Exact downstream tool names that may be exposed (allowlist — required)
    allowed_tools: list[str] = field(default_factory=list)
    # Profile names that may see tools from this server
    profiles_allowed: list[str] = field(default_factory=list)


@dataclass
class DiscoveredTool:
    """A tool discovered from a downstream MCP server.

    Discovery alone does not make a tool visible or usable.
    Tools are deny-by-default until explicitly allowlisted.
    """

    downstream_server_name: str
    downstream_tool_name: str
    # Collision-safe normalized name: {server_name}__{tool_name}
    normalized_name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    # Set by catalog after allowlist filtering
    allowed: bool = False
    profiles_allowed: list[str] = field(default_factory=list)

    def to_anthropic_tool_dict(self) -> dict[str, Any]:
        """Convert to Anthropic Messages API tool definition format."""
        schema = self.input_schema or {"type": "object", "properties": {}}
        return {
            "name": self.normalized_name,
            "description": self.description or f"Tool from {self.downstream_server_name}",
            "input_schema": schema,
        }


@dataclass
class DownstreamToolCallResult:
    """Result of a single downstream tool invocation."""

    tool_name: str  # normalized name
    success: bool
    content: Any = None  # raw result content from the downstream server
    error_message: str | None = None

    def to_content_string(self) -> str:
        """Serialize result content to a string for the Messages API tool_result block."""
        if not self.success:
            return f"Error: {self.error_message}"
        if isinstance(self.content, str):
            return self.content
        if isinstance(self.content, (list, dict)):
            return json.dumps(self.content, default=str)
        return str(self.content) if self.content is not None else ""
