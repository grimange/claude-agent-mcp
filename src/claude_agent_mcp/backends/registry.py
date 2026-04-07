"""Backend registry for claude-agent-mcp (v0.4).

Maintains a lookup of registered execution backends by name.
"""

from __future__ import annotations

from claude_agent_mcp.backends.base import ExecutionBackend
from claude_agent_mcp.errors import ExecutionBackendConfigError


class BackendRegistry:
    """Registry of available execution backends.

    Backends are registered by name. Selection is explicit — no magic fallback.
    """

    def __init__(self) -> None:
        self._backends: dict[str, ExecutionBackend] = {}

    def register(self, backend: ExecutionBackend) -> None:
        """Register a backend implementation."""
        self._backends[backend.name] = backend

    def get(self, name: str) -> ExecutionBackend:
        """Return backend by name, or raise ExecutionBackendConfigError."""
        if name not in self._backends:
            supported = sorted(self._backends)
            raise ExecutionBackendConfigError(
                f"Unknown execution backend: {name!r}. "
                f"Supported values: {supported}"
            )
        return self._backends[name]

    def names(self) -> list[str]:
        """Return names of all registered backends."""
        return list(self._backends)
