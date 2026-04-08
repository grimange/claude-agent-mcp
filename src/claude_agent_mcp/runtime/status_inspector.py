"""Resolved runtime status inspector for claude-agent-mcp (v1.0.0).

Produces a RuntimeStatusSnapshot from the active config and optionally the
active backend capabilities. Operators use this to inspect what the runtime
believes is enabled and supported without inferring state from logs.

Exposed via:
  - agent_get_runtime_status MCP tool (additive, non-breaking)
  - Startup log message (always present)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING

from claude_agent_mcp.types import RuntimeStatusSnapshot

if TYPE_CHECKING:
    from claude_agent_mcp.backends.base import BackendCapabilities
    from claude_agent_mcp.config import Config

VERSION = "1.0.0"

# Intentional product boundaries preserved in v1.0.0.
PRESERVED_LIMITATIONS: list[str] = [
    "No native tool_use / tool_result loop in Claude Code backend — "
    "mediated execution is runtime-dispatched, not backend-native tool calling",
    "No streaming transport — stdio is the production transport; "
    "streamable-http is available but not the default",
    "No cross-backend session migration — sessions are bound to the backend "
    "active at creation time",
    "No broad autonomous execution chaining — per-turn and per-session "
    "mediation limits are enforced",
    "Mediated execution requires active federation — without federation, "
    "mediated action requests are rejected with federation_inactive",
]


class RuntimeStatusInspector:
    """Produces resolved runtime status snapshots for operator inspection.

    Instantiated once per server startup with the active config.
    The federation_active flag is set after federation initializes.
    """

    def __init__(self, config: "Config") -> None:
        self._config = config
        self._federation_active: bool = False

    def set_federation_active(self, active: bool) -> None:
        """Called after federation initializes to record actual active state."""
        self._federation_active = active

    def build_snapshot(
        self,
        backend_capabilities: "BackendCapabilities | None" = None,
    ) -> RuntimeStatusSnapshot:
        """Produce a RuntimeStatusSnapshot from the current config.

        Args:
            backend_capabilities: Optional capabilities from the active backend.
                When provided, capability flags are merged with config-driven flags
                for a more complete picture.
        """
        config = self._config

        capability_flags = self._resolve_capability_flags(config, backend_capabilities)
        continuation_settings = self._resolve_continuation_settings(config)
        mediation_settings = self._resolve_mediation_settings(config)
        workflow_settings = self._resolve_workflow_settings(config)

        return RuntimeStatusSnapshot(
            version=VERSION,
            operator_profile_preset=config.operator_profile_preset,
            backend=config.execution_backend,
            transport=config.transport,
            model=config.model,
            federation_enabled=config.federation_enabled,
            federation_active=self._federation_active,
            capability_flags=capability_flags,
            continuation_settings=continuation_settings,
            mediation_settings=mediation_settings,
            workflow_settings=workflow_settings,
            preserved_limitations=PRESERVED_LIMITATIONS,
            resolved_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    @staticmethod
    def _resolve_capability_flags(
        config: "Config",
        backend_capabilities: "BackendCapabilities | None",
    ) -> dict[str, bool]:
        """Merge config-driven and backend-declared capability flags."""
        flags: dict[str, bool] = {
            # Config-driven feature flags
            "limited_tool_forwarding_enabled": getattr(
                config, "claude_code_enable_limited_tool_forwarding", False
            ),
            "execution_mediation_enabled": getattr(
                config, "claude_code_enable_execution_mediation", False
            ),
            "federation_enabled": config.federation_enabled,
        }

        if backend_capabilities is not None:
            # Add backend-declared structural capabilities
            flags.update({
                "backend_supports_downstream_tools": backend_capabilities.supports_downstream_tools,
                "backend_supports_structured_tool_use": backend_capabilities.supports_structured_tool_use,
                "backend_supports_native_multiturn": backend_capabilities.supports_native_multiturn,
                "backend_supports_workspace_assumptions": backend_capabilities.supports_workspace_assumptions,
                "backend_supports_limited_downstream_tools": backend_capabilities.supports_limited_downstream_tools,
                "backend_supports_structured_continuation": backend_capabilities.supports_structured_continuation_context,
                "backend_supports_execution_mediation": backend_capabilities.supports_execution_mediation,
                "backend_supports_bounded_workflows": getattr(
                    backend_capabilities, "supports_bounded_mediated_workflows", False
                ),
            })

        return flags

    @staticmethod
    def _resolve_continuation_settings(config: "Config") -> dict[str, object]:
        return {
            "max_continuation_turns": getattr(config, "claude_code_max_continuation_turns", 5),
            "max_continuation_warnings": getattr(config, "claude_code_max_continuation_warnings", 3),
            "max_continuation_forwarding_events": getattr(
                config, "claude_code_max_continuation_forwarding_events", 3
            ),
            "include_verification_context": getattr(
                config, "claude_code_include_verification_context", True
            ),
            "include_tool_downgrade_context": getattr(
                config, "claude_code_include_tool_downgrade_context", True
            ),
        }

    @staticmethod
    def _resolve_mediation_settings(config: "Config") -> dict[str, object]:
        return {
            "enabled": getattr(config, "claude_code_enable_execution_mediation", False),
            "max_actions_per_turn": getattr(
                config, "claude_code_max_mediated_actions_per_turn", 1
            ),
            "allowed_action_types": getattr(
                config, "claude_code_allowed_mediated_action_types", []
            ),
            "include_results_in_continuation": getattr(
                config, "claude_code_include_mediated_results_in_continuation", False
            ),
        }

    @staticmethod
    def _resolve_workflow_settings(config: "Config") -> dict[str, object]:
        return {
            "max_workflow_steps": getattr(config, "claude_code_max_mediated_workflow_steps", 1),
            "allowed_tools": getattr(config, "claude_code_allowed_mediated_tools", []),
            "denied_tools": getattr(config, "claude_code_denied_mediated_tools", []),
            "max_session_approvals": getattr(
                config, "claude_code_max_session_mediated_approvals", 100
            ),
            "include_rejected_in_continuation": getattr(
                config, "claude_code_include_rejected_mediation_in_continuation", False
            ),
            "policy_profile": getattr(
                config, "claude_code_mediation_policy_profile", "conservative"
            ),
        }
