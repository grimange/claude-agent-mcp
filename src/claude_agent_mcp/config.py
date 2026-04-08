"""Configuration loading for claude-agent-mcp.

Reads from environment variables (with .env support).
All paths are resolved to absolute strings at load time.

Environment variable reference:
  ANTHROPIC_API_KEY               — Claude API key (required for api backend)
  CLAUDE_AGENT_MCP_TRANSPORT      — Transport mode: stdio | streamable-http (default: stdio)
  CLAUDE_AGENT_MCP_HOST           — Bind host for network transport (default: 127.0.0.1)
  CLAUDE_AGENT_MCP_PORT           — Bind port for network transport (default: 8000)
  CLAUDE_AGENT_MCP_STATE_DIR      — State storage directory (default: .state)
  CLAUDE_AGENT_MCP_DB_PATH        — SQLite path override (default: <state_dir>/claude-agent-mcp.db)
  CLAUDE_AGENT_MCP_ARTIFACT_DIR   — Artifact storage directory override
  CLAUDE_AGENT_MCP_LOG_LEVEL      — Log level (default: INFO)
  CLAUDE_AGENT_MCP_MODEL          — Claude model (default: claude-sonnet-4-6)
  CLAUDE_AGENT_MCP_LOCK_TTL       — Session lock TTL in seconds (default: 300)
  CLAUDE_AGENT_MCP_ALLOWED_DIRS   — Comma-separated allowed working directories

Operator profile preset (v1.0.0):
  CLAUDE_AGENT_MCP_OPERATOR_PROFILE — Named preset: safe_default | continuity_optimized |
                                       mediation_enabled | workflow_limited (default: none)
  Individual env vars always take precedence over preset defaults. The preset provides
  a clear starting point; operators can override specific fields on top of it.

Execution backend variables (v0.4):
  CLAUDE_AGENT_MCP_EXECUTION_BACKEND       — Backend: api | claude_code (default: api)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH    — Path to claude CLI binary (claude_code backend)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT     — CLI timeout in seconds (default: 300)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING  — Enable limited tool forwarding for claude_code backend (default: false)

Continuation window policy variables (v0.7.0):
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS             — Max recent turns in continuation context (default: 5)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_WARNINGS          — Max warnings carried forward (default: 3)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_FORWARDING_EVENTS — Max forwarding events summarized (default: 3)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_VERIFICATION_CONTEXT       — Include verification outcomes in continuation (default: true)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_TOOL_DOWNGRADE_CONTEXT     — Include tool downgrade warnings in continuation (default: true)

Execution mediation variables (v0.8.0):
  CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION              — Enable execution mediation (default: false)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_ACTIONS_PER_TURN           — Max mediated actions per turn (default: 1)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_ACTION_TYPES           — Comma-separated allowed types: read,lookup,inspect (default: all supported)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_MEDIATED_RESULTS_IN_CONTINUATION — Include mediated results in continuation context (default: false)

Bounded workflow mediation variables (v0.9.0):
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_WORKFLOW_STEPS             — Max steps per workflow request (default: 1)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_TOOLS                  — Comma-separated allowed tool names (default: all visible)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_DENIED_MEDIATED_TOOLS                   — Comma-separated denied tool names (default: none)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_SESSION_MEDIATED_APPROVALS          — Max total mediated approvals per session (default: 100)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_REJECTED_MEDIATION_IN_CONTINUATION — Include rejected step summaries in continuation (default: false)
  CLAUDE_AGENT_MCP_CLAUDE_CODE_MEDIATION_POLICY_PROFILE                — Named policy profile (default: conservative)

Federation variables (v0.3):
  CLAUDE_AGENT_MCP_FEDERATION_ENABLED   — Enable downstream federation (default: false)
  CLAUDE_AGENT_MCP_FEDERATION_CONFIG    — Path to JSON federation config file

Legacy variable names (still supported, lower priority than MCP-prefixed names):
  CLAUDE_AGENT_STATE_DIR, CLAUDE_AGENT_MODEL, CLAUDE_AGENT_LOCK_TTL_SECONDS,
  CLAUDE_AGENT_ALLOWED_DIRS, CLAUDE_AGENT_MAX_ARTIFACT_BYTES, CLAUDE_AGENT_LOG_LEVEL
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

VALID_TRANSPORTS = {"stdio", "streamable-http"}
VALID_EXECUTION_BACKENDS = {"api", "claude_code"}

# ---------------------------------------------------------------------------
# Operator profile preset defaults (v1.0.0)
# ---------------------------------------------------------------------------
# Each preset maps field keys to string values (matching env var encoding).
# Individual env vars always override preset defaults — see Config.__init__.
#
# Key names match the field suffix used in env var construction to keep the
# mapping readable. The Config initializer resolves these via _preset().

_OPERATOR_PRESET_DEFAULTS: dict[str, dict[str, str]] = {
    "safe_default": {
        # Conservative baseline: mediation off, short windows, no extra context.
        "enable_execution_mediation": "false",
        "max_continuation_turns": "5",
        "max_continuation_warnings": "3",
        "max_continuation_forwarding_events": "3",
        "include_verification_context": "true",
        "include_tool_downgrade_context": "true",
        "max_mediated_actions_per_turn": "1",
        "max_mediated_workflow_steps": "1",
        "max_session_mediated_approvals": "10",
        "include_mediated_results_in_continuation": "false",
        "include_rejected_mediation_in_continuation": "false",
        "mediation_policy_profile": "conservative",
    },
    "continuity_optimized": {
        # Longer continuation windows; mediation off; more context carried forward.
        "enable_execution_mediation": "false",
        "max_continuation_turns": "10",
        "max_continuation_warnings": "5",
        "max_continuation_forwarding_events": "5",
        "include_verification_context": "true",
        "include_tool_downgrade_context": "true",
        "max_mediated_actions_per_turn": "1",
        "max_mediated_workflow_steps": "1",
        "max_session_mediated_approvals": "10",
        "include_mediated_results_in_continuation": "true",
        "include_rejected_mediation_in_continuation": "false",
        "mediation_policy_profile": "conservative",
    },
    "mediation_enabled": {
        # Mediation on; conservative per-turn limit; results included in continuation.
        "enable_execution_mediation": "true",
        "max_continuation_turns": "5",
        "max_continuation_warnings": "3",
        "max_continuation_forwarding_events": "3",
        "include_verification_context": "true",
        "include_tool_downgrade_context": "true",
        "max_mediated_actions_per_turn": "3",
        "max_mediated_workflow_steps": "1",
        "max_session_mediated_approvals": "50",
        "include_mediated_results_in_continuation": "true",
        "include_rejected_mediation_in_continuation": "false",
        "mediation_policy_profile": "mediation_enabled",
    },
    "workflow_limited": {
        # Mediation on; bounded multi-step workflows; session approval cap.
        "enable_execution_mediation": "true",
        "max_continuation_turns": "5",
        "max_continuation_warnings": "3",
        "max_continuation_forwarding_events": "3",
        "include_verification_context": "true",
        "include_tool_downgrade_context": "true",
        "max_mediated_actions_per_turn": "5",
        "max_mediated_workflow_steps": "3",
        "max_session_mediated_approvals": "30",
        "include_mediated_results_in_continuation": "true",
        "include_rejected_mediation_in_continuation": "false",
        "mediation_policy_profile": "workflow_limited",
    },
}


def _env(primary: str, fallback: str | None = None, default: str = "") -> str:
    """Return primary env var, falling back to legacy name, then default."""
    v = os.environ.get(primary)
    if v is not None:
        return v
    if fallback:
        v = os.environ.get(fallback)
        if v is not None:
            return v
    return default


class Config:
    """Runtime configuration derived from environment."""

    def __init__(self) -> None:
        self.anthropic_api_key: str = os.environ.get("ANTHROPIC_API_KEY", "")

        # --- Operator profile preset (v1.0.0) ---
        # Load preset first so individual fields can use preset defaults as fallback.
        # Individual env vars always take precedence over preset defaults.
        operator_preset_raw = _env(
            "CLAUDE_AGENT_MCP_OPERATOR_PROFILE", default=""
        ).strip().lower()
        self.operator_profile_preset: str | None = operator_preset_raw or None
        _preset_defaults: dict[str, str] = _OPERATOR_PRESET_DEFAULTS.get(
            operator_preset_raw, {}
        )

        def _preset(field: str, hardcoded_default: str = "") -> str:
            """Return preset default for a field, or hardcoded_default if not in preset."""
            return _preset_defaults.get(field, hardcoded_default)

        # --- Transport ---
        self.transport: str = _env(
            "CLAUDE_AGENT_MCP_TRANSPORT", default="stdio"
        ).strip().lower()

        self.host: str = _env("CLAUDE_AGENT_MCP_HOST", default="127.0.0.1").strip()
        self.port: int = int(_env("CLAUDE_AGENT_MCP_PORT", default="8000").strip())

        # --- Storage ---
        state_dir_raw = _env(
            "CLAUDE_AGENT_MCP_STATE_DIR", "CLAUDE_AGENT_STATE_DIR", default=".state"
        )
        self.state_dir: Path = Path(state_dir_raw).resolve()

        db_override = _env("CLAUDE_AGENT_MCP_DB_PATH", default="")
        self.db_path: Path = (
            Path(db_override).resolve() if db_override else self.state_dir / "claude-agent-mcp.db"
        )

        artifact_override = _env("CLAUDE_AGENT_MCP_ARTIFACT_DIR", default="")
        self.artifacts_dir: Path = (
            Path(artifact_override).resolve() if artifact_override else self.state_dir / "artifacts"
        )

        # --- Runtime ---
        self.model: str = _env(
            "CLAUDE_AGENT_MCP_MODEL", "CLAUDE_AGENT_MODEL", default="claude-sonnet-4-6"
        )

        self.lock_ttl_seconds: int = int(
            _env("CLAUDE_AGENT_MCP_LOCK_TTL", "CLAUDE_AGENT_LOCK_TTL_SECONDS", default="300")
        )

        allowed_raw = _env(
            "CLAUDE_AGENT_MCP_ALLOWED_DIRS", "CLAUDE_AGENT_ALLOWED_DIRS", default=""
        )
        if allowed_raw:
            self.allowed_dirs: list[str] = [
                str(Path(d.strip()).resolve())
                for d in allowed_raw.split(",")
                if d.strip()
            ]
        else:
            self.allowed_dirs = [str(Path.cwd().resolve())]

        self.max_artifact_bytes: int = int(
            _env(
                "CLAUDE_AGENT_MCP_MAX_ARTIFACT_BYTES",
                "CLAUDE_AGENT_MAX_ARTIFACT_BYTES",
                default=str(10 * 1024 * 1024),
            )
        )

        self.log_level: str = _env(
            "CLAUDE_AGENT_MCP_LOG_LEVEL", "CLAUDE_AGENT_LOG_LEVEL", default="INFO"
        ).upper()

        # --- Execution backend (v0.4) ---
        self.execution_backend: str = _env(
            "CLAUDE_AGENT_MCP_EXECUTION_BACKEND", default="api"
        ).strip().lower()

        # Claude Code backend config
        self.claude_code_cli_path: str = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_CLI_PATH", default=""
        ).strip()
        self.claude_code_timeout_seconds: int = int(
            _env("CLAUDE_AGENT_MCP_CLAUDE_CODE_TIMEOUT", default="300").strip()
        )

        # Limited tool forwarding (v0.6) — opt-in, disabled by default
        claude_code_limited_tool_forwarding_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_LIMITED_TOOL_FORWARDING", default="false"
        ).strip().lower()
        self.claude_code_enable_limited_tool_forwarding: bool = (
            claude_code_limited_tool_forwarding_raw in {"true", "1", "yes"}
        )

        # Continuation window policy (v0.7.0) — conservative defaults; preset-aware (v1.0.0)
        self.claude_code_max_continuation_turns: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_TURNS",
                default=_preset("max_continuation_turns", "5"),
            ).strip()
        )
        self.claude_code_max_continuation_warnings: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_WARNINGS",
                default=_preset("max_continuation_warnings", "3"),
            ).strip()
        )
        self.claude_code_max_continuation_forwarding_events: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_CONTINUATION_FORWARDING_EVENTS",
                default=_preset("max_continuation_forwarding_events", "3"),
            ).strip()
        )
        claude_code_include_verification_context_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_VERIFICATION_CONTEXT",
            default=_preset("include_verification_context", "true"),
        ).strip().lower()
        self.claude_code_include_verification_context: bool = (
            claude_code_include_verification_context_raw in {"true", "1", "yes"}
        )
        claude_code_include_tool_downgrade_context_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_TOOL_DOWNGRADE_CONTEXT",
            default=_preset("include_tool_downgrade_context", "true"),
        ).strip().lower()
        self.claude_code_include_tool_downgrade_context: bool = (
            claude_code_include_tool_downgrade_context_raw in {"true", "1", "yes"}
        )

        # Execution mediation (v0.8.0) — disabled by default; preset-aware (v1.0.0)
        claude_code_enable_mediation_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_ENABLE_EXECUTION_MEDIATION",
            default=_preset("enable_execution_mediation", "false"),
        ).strip().lower()
        self.claude_code_enable_execution_mediation: bool = (
            claude_code_enable_mediation_raw in {"true", "1", "yes"}
        )

        self.claude_code_max_mediated_actions_per_turn: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_ACTIONS_PER_TURN",
                default=_preset("max_mediated_actions_per_turn", "1"),
            ).strip()
        )

        allowed_mediated_types_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_ACTION_TYPES", default=""
        ).strip()
        self.claude_code_allowed_mediated_action_types: list[str] = (
            [t.strip() for t in allowed_mediated_types_raw.split(",") if t.strip()]
            if allowed_mediated_types_raw else []
        )

        claude_code_include_mediated_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_MEDIATED_RESULTS_IN_CONTINUATION",
            default=_preset("include_mediated_results_in_continuation", "false"),
        ).strip().lower()
        self.claude_code_include_mediated_results_in_continuation: bool = (
            claude_code_include_mediated_raw in {"true", "1", "yes"}
        )

        # Bounded workflow mediation (v0.9.0) — additive; preset-aware (v1.0.0)
        self.claude_code_max_mediated_workflow_steps: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_MEDIATED_WORKFLOW_STEPS",
                default=_preset("max_mediated_workflow_steps", "1"),
            ).strip()
        )

        allowed_mediated_tools_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_ALLOWED_MEDIATED_TOOLS", default=""
        ).strip()
        self.claude_code_allowed_mediated_tools: list[str] = (
            [t.strip() for t in allowed_mediated_tools_raw.split(",") if t.strip()]
            if allowed_mediated_tools_raw else []
        )

        denied_mediated_tools_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_DENIED_MEDIATED_TOOLS", default=""
        ).strip()
        self.claude_code_denied_mediated_tools: list[str] = (
            [t.strip() for t in denied_mediated_tools_raw.split(",") if t.strip()]
            if denied_mediated_tools_raw else []
        )

        self.claude_code_max_session_mediated_approvals: int = int(
            _env(
                "CLAUDE_AGENT_MCP_CLAUDE_CODE_MAX_SESSION_MEDIATED_APPROVALS",
                default=_preset("max_session_mediated_approvals", "100"),
            ).strip()
        )

        claude_code_include_rejected_mediation_raw = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_INCLUDE_REJECTED_MEDIATION_IN_CONTINUATION",
            default=_preset("include_rejected_mediation_in_continuation", "false"),
        ).strip().lower()
        self.claude_code_include_rejected_mediation_in_continuation: bool = (
            claude_code_include_rejected_mediation_raw in {"true", "1", "yes"}
        )

        self.claude_code_mediation_policy_profile: str = _env(
            "CLAUDE_AGENT_MCP_CLAUDE_CODE_MEDIATION_POLICY_PROFILE",
            default=_preset("mediation_policy_profile", "conservative"),
        ).strip()

        # --- Federation (v0.3) ---
        federation_enabled_raw = _env(
            "CLAUDE_AGENT_MCP_FEDERATION_ENABLED", default="false"
        ).strip().lower()
        self.federation_enabled: bool = federation_enabled_raw in {"true", "1", "yes"}

        federation_config_raw = _env("CLAUDE_AGENT_MCP_FEDERATION_CONFIG", default="")
        self.federation_config_path: Path | None = (
            Path(federation_config_raw).resolve() if federation_config_raw else None
        )

    def validate(self) -> None:
        """Fail early and clearly on invalid startup configuration."""
        errors: list[str] = []

        if self.transport not in VALID_TRANSPORTS:
            errors.append(
                f"CLAUDE_AGENT_MCP_TRANSPORT={self.transport!r} is not valid. "
                f"Choose from: {sorted(VALID_TRANSPORTS)}"
            )

        if self.transport == "streamable-http":
            if not self.host:
                errors.append("CLAUDE_AGENT_MCP_HOST must not be empty for streamable-http transport")
            if not (1 <= self.port <= 65535):
                errors.append(
                    f"CLAUDE_AGENT_MCP_PORT={self.port} is out of range (1–65535)"
                )

        if self.log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            errors.append(
                f"CLAUDE_AGENT_MCP_LOG_LEVEL={self.log_level!r} is not a valid log level"
            )

        if self.execution_backend not in VALID_EXECUTION_BACKENDS:
            errors.append(
                f"CLAUDE_AGENT_MCP_EXECUTION_BACKEND={self.execution_backend!r} is not valid. "
                f"Choose from: {sorted(VALID_EXECUTION_BACKENDS)}"
            )

        if errors:
            raise SystemExit(
                "claude-agent-mcp startup configuration error(s):\n"
                + "\n".join(f"  • {e}" for e in errors)
            )

    def ensure_dirs(self) -> None:
        """Create state directories if they do not exist."""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_config() -> Config:
    return Config()
