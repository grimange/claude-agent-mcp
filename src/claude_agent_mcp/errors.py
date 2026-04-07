"""Internal error taxonomy for claude-agent-mcp.

All errors map to stable MCP-facing codes. Raw stack traces must not
appear in tool payloads.
"""

from __future__ import annotations

from typing import Any


class AgentMCPError(Exception):
    """Base class for all internal errors."""

    code: str = "internal_error"
    message: str = "An internal error occurred"

    def __init__(self, message: str | None = None, **kwargs: Any) -> None:
        self.message = message or self.__class__.message
        self.details = kwargs
        super().__init__(self.message)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            result["details"] = self.details
        return result


class ValidationError(AgentMCPError):
    """Input failed schema or semantic validation."""

    code = "validation_error"
    message = "Validation failed"


class PolicyDeniedError(AgentMCPError):
    """Execution was blocked by active profile policy."""

    code = "policy_denied"
    message = "Execution denied by policy"


class SessionNotFoundError(AgentMCPError):
    """Requested session does not exist."""

    code = "session_not_found"
    message = "Session not found"


class SessionConflictError(AgentMCPError):
    """Session is currently locked by another active execution."""

    code = "session_conflict"
    message = "Session is currently locked by another execution"


class SessionStatusError(AgentMCPError):
    """Session is in a state that does not allow the requested operation."""

    code = "session_status_error"
    message = "Session status does not allow this operation"


class ProviderRuntimeError(AgentMCPError):
    """The Claude provider raised an error during execution."""

    code = "provider_runtime_error"
    message = "Provider execution failed"


class ArtifactPersistenceError(AgentMCPError):
    """Failed to store or retrieve an artifact."""

    code = "artifact_persistence_error"
    message = "Artifact persistence failed"


class NormalizationError(AgentMCPError):
    """Failed to normalize a provider response into internal contracts."""

    code = "normalization_error"
    message = "Response normalization failed"


class ConfigurationError(AgentMCPError):
    """Server or profile configuration is invalid."""

    code = "configuration_error"
    message = "Configuration error"


# ---------------------------------------------------------------------------
# Federation errors (v0.3)
# ---------------------------------------------------------------------------


class DownstreamServerConfigError(AgentMCPError):
    """Downstream server configuration is invalid or missing."""

    code = "downstream_server_config_error"
    message = "Downstream server configuration error"


class DownstreamDiscoveryError(AgentMCPError):
    """Failed to discover tools from a downstream MCP server."""

    code = "downstream_discovery_error"
    message = "Downstream tool discovery failed"


class DownstreamToolNotAllowedError(AgentMCPError):
    """Requested downstream tool is not in the allowlist."""

    code = "downstream_tool_not_allowed"
    message = "Downstream tool is not allowlisted"


class DownstreamToolNotVisibleError(AgentMCPError):
    """Requested downstream tool is not visible for the active profile."""

    code = "downstream_tool_not_visible"
    message = "Downstream tool is not visible for this profile"


class DownstreamInvocationError(AgentMCPError):
    """Downstream tool invocation failed."""

    code = "downstream_invocation_error"
    message = "Downstream tool invocation failed"


class DownstreamSchemaValidationError(AgentMCPError):
    """Tool arguments failed downstream schema validation."""

    code = "downstream_schema_validation_error"
    message = "Downstream tool arguments failed schema validation"
