"""MCP server entry point for claude-agent-mcp.

Registers all v0.1 tools, wires dependencies, and dispatches to the
selected transport (stdio or streamable-http).

Transport selection:
  --transport stdio              (default)
  --transport streamable-http [--host HOST] [--port PORT]

Or via environment:
  CLAUDE_AGENT_MCP_TRANSPORT=streamable-http
  CLAUDE_AGENT_MCP_HOST=127.0.0.1
  CLAUDE_AGENT_MCP_PORT=8000
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

from mcp.server import Server
from mcp.types import (
    TextContent,
    Tool,
)

from claude_agent_mcp.backends import build_backend
from claude_agent_mcp.config import get_config
from claude_agent_mcp.federation import FederationManager
from claude_agent_mcp.logging import configure_logging, get_logger
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.runtime.status_inspector import RuntimeStatusInspector
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.tools.continue_session import handle_continue_session
from claude_agent_mcp.tools.get_session import handle_get_session
from claude_agent_mcp.tools.list_sessions import handle_list_sessions
from claude_agent_mcp.tools.run_task import handle_run_task
from claude_agent_mcp.tools.verify_task import handle_verify_task
from claude_agent_mcp.types import RuntimeRestrictionContract

logger = get_logger(__name__)

VERSION = "1.1.0"

# ---------------------------------------------------------------------------
# APNTalk restriction contract (v1.1.0)
# ---------------------------------------------------------------------------

# The exact admitted tool pair for APNTalk verification mode.
_APNTALK_ADMITTED_TOOLS: frozenset[str] = frozenset({
    "agent_get_runtime_status",
    "agent_verify_task",
})


def _build_apntalk_contract(allowed_dirs: list[str]) -> RuntimeRestrictionContract:
    """Return the resolved APNTalk restriction contract.

    allowed_dirs must be pre-normalized absolute paths from config.allowed_dirs.
    """
    return RuntimeRestrictionContract(
        mode="apntalk_verification",
        policy_mode="verification_only",
        authority_mode="advisory_only",
        tool_surface_mode="restricted",
        active_profile="apntalk_verification",
        required_backend="claude_code",
        required_transport="stdio",
        allowed_tools=sorted(_APNTALK_ADMITTED_TOOLS),
        allowed_directories=allowed_dirs,
        restriction_contract_id="apntalk_verification_v1",
        restriction_contract_version=1,
        fail_closed=True,
    )


def _apntalk_startup_check(
    config: Any,
    contract: RuntimeRestrictionContract,
) -> list[str]:
    """Validate all APNTalk contract requirements. Return non-compliance reasons.

    An empty list means the contract is fully satisfied and startup may proceed.
    A non-empty list means at least one requirement is not met.
    """
    reasons: list[str] = []

    if config.execution_backend != contract.required_backend:
        reasons.append(
            f"backend={config.execution_backend!r} does not match "
            f"required_backend={contract.required_backend!r}"
        )
    if config.transport != contract.required_transport:
        reasons.append(
            f"transport={config.transport!r} does not match "
            f"required_transport={contract.required_transport!r}"
        )
    if not contract.allowed_directories:
        reasons.append(
            "allowed_directories is empty — bounded filesystem scope cannot be proven"
        )
    # Ensure every allowed dir is an absolute path (basic sanity)
    for d in contract.allowed_directories:
        if not d.startswith("/") and not (len(d) > 1 and d[1] == ":"):
            reasons.append(
                f"allowed_directory {d!r} is not an absolute path — "
                "bounded directories must be normalized absolute paths"
            )

    return reasons

# ---------------------------------------------------------------------------
# Tool schemas (JSON Schema for each v0.1 tool)
# ---------------------------------------------------------------------------

TOOL_DEFINITIONS: list[Tool] = [
    Tool(
        name="agent_run_task",
        description=(
            "Run a bounded Claude-backed task in a new durable session. "
            "Returns a canonical response envelope with session_id, status, and result."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {"type": "string", "description": "The task to execute"},
                "system_profile": {
                    "type": "string",
                    "enum": ["general", "verification"],
                    "default": "general",
                    "description": "Execution profile (policy bundle)",
                },
                "working_directory": {
                    "type": "string",
                    "description": "Working directory for execution (defaults to server CWD)",
                },
                "attachments": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to files to attach as context",
                },
                "max_turns": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "Maximum conversation turns (capped by profile)",
                },
                "allow_tools": {
                    "type": "boolean",
                    "default": True,
                    "description": "Whether to allow tool use (subject to profile policy)",
                },
            },
            "required": ["task"],
        },
    ),
    Tool(
        name="agent_continue_session",
        description=(
            "Continue an existing durable session with a new message. "
            "Appends to the canonical internal transcript."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {
                    "type": "string",
                    "description": "Canonical session identifier (e.g. sess_abc123)",
                },
                "message": {"type": "string", "description": "The follow-up message"},
                "max_turns": {
                    "type": "integer",
                    "default": 10,
                    "minimum": 1,
                    "maximum": 100,
                },
            },
            "required": ["session_id", "message"],
        },
    ),
    Tool(
        name="agent_get_session",
        description="Get the full detail record for a single session by session_id.",
        inputSchema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session identifier"},
            },
            "required": ["session_id"],
        },
    ),
    Tool(
        name="agent_list_sessions",
        description="List recent sessions with optional status filter.",
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "default": 20,
                    "minimum": 1,
                    "maximum": 200,
                    "description": "Maximum number of sessions to return",
                },
                "status": {
                    "type": "string",
                    "enum": ["created", "running", "completed", "failed", "interrupted"],
                    "description": "Filter by session status",
                },
            },
        },
    ),
    Tool(
        name="agent_verify_task",
        description=(
            "Run a structured verification workflow against evidence. "
            "Uses the verification profile with read-only, fail-closed behavior. "
            "Returns a verdict: pass, pass_with_restrictions, fail_closed, or insufficient_evidence."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "The task or claim to verify",
                },
                "scope": {
                    "type": "string",
                    "description": "Verification scope description",
                },
                "evidence_paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Paths to evidence files",
                },
                "fail_closed": {
                    "type": "boolean",
                    "default": True,
                    "description": "If true, insufficient evidence resolves as fail_closed",
                },
                "system_profile": {
                    "type": "string",
                    "enum": ["verification"],
                    "default": "verification",
                    "description": "Must be verification",
                },
            },
            "required": ["task"],
        },
    ),
    Tool(
        name="agent_get_runtime_status",
        description=(
            "Get a resolved runtime status snapshot showing what the runtime believes "
            "is enabled and supported. Returns backend, transport, operator profile preset, "
            "effective capability flags, mediation settings, continuation settings, "
            "federation status, and known preserved limitations. "
            "Additive inspection tool — does not modify state."
        ),
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
]


# ---------------------------------------------------------------------------
# Server wiring (transport-agnostic)
# ---------------------------------------------------------------------------

def build_server(
    session_store: SessionStore,
    artifact_store: ArtifactStore,
    executor: WorkflowExecutor,
    status_inspector: RuntimeStatusInspector | None = None,
    restriction_contract: RuntimeRestrictionContract | None = None,
) -> Server:
    """Build and return the MCP Server with all tools registered.

    This function is transport-agnostic. Both stdio and streamable-http
    transports call this to obtain the same server instance.

    When restriction_contract is provided (APNTalk mode), the server publishes
    only the tools named in contract.allowed_tools.  No other tools are
    registered or callable — the restriction is enforced at this layer, not
    downstream.
    """
    server = Server("claude-agent-mcp")

    # Resolve the actual published tool set.
    if restriction_contract is not None:
        admitted = frozenset(restriction_contract.allowed_tools)
        active_tool_definitions = [t for t in TOOL_DEFINITIONS if t.name in admitted]
        active_tool_names = frozenset(t.name for t in active_tool_definitions)
    else:
        active_tool_definitions = list(TOOL_DEFINITIONS)
        active_tool_names = frozenset(t.name for t in TOOL_DEFINITIONS)

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return active_tool_definitions

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        logger.info("Tool called: %s", name)
        arguments = arguments or {}

        # Reject calls to tools outside the active surface.
        if name not in active_tool_names:
            result = {
                "error": {
                    "code": "tool_not_admitted",
                    "message": (
                        f"Tool '{name}' is not admitted in the current runtime mode. "
                        f"Admitted tools: {sorted(active_tool_names)}"
                    ),
                }
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        if name == "agent_run_task":
            result = await handle_run_task(executor, arguments)
        elif name == "agent_continue_session":
            result = await handle_continue_session(executor, arguments)
        elif name == "agent_get_session":
            result = await handle_get_session(session_store, arguments)
        elif name == "agent_list_sessions":
            result = await handle_list_sessions(session_store, arguments)
        elif name == "agent_verify_task":
            result = await handle_verify_task(executor, arguments)
        elif name == "agent_get_runtime_status":
            if status_inspector is not None:
                snapshot = status_inspector.build_snapshot(
                    restriction_contract=restriction_contract,
                    exposed_tool_names=sorted(active_tool_names),
                )
                result = snapshot.model_dump()
            else:
                result = {"error": {"code": "inspector_unavailable", "message": "Runtime status inspector not initialized"}}
        else:
            result = {
                "error": {
                    "code": "unknown_tool",
                    "message": f"Unknown tool: {name}",
                }
            }

        return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


# ---------------------------------------------------------------------------
# Runtime setup (shared by both transports)
# ---------------------------------------------------------------------------

async def _setup_runtime(config):
    """Open stores and build executor.

    Returns (session_store, artifact_store, executor, status_inspector, restriction_contract).
    restriction_contract is None in standard mode.
    """
    # --- APNTalk mode startup self-check (v1.1.0) ---
    restriction_contract: RuntimeRestrictionContract | None = None
    if getattr(config, "mode", "standard") == "apntalk_verification":
        restriction_contract = _build_apntalk_contract(config.allowed_dirs)
        non_compliance = _apntalk_startup_check(config, restriction_contract)
        if non_compliance:
            reasons_str = "\n".join(f"  • {r}" for r in non_compliance)
            raise SystemExit(
                "APNTalk verification mode startup contract violation(s):\n"
                + reasons_str
                + "\nStartup aborted (fail_closed=true). "
                "Fix the above before starting in apntalk_verification mode."
            )
        # Log operator-visible restriction summary.
        logger.info(
            "APNTalk verification mode ACTIVE — restriction_contract_id=%s "
            "backend=%s transport=%s profile=%s exposed_tools=%s "
            "allowed_dirs=%s compliance=PASS",
            restriction_contract.restriction_contract_id,
            restriction_contract.required_backend,
            restriction_contract.required_transport,
            restriction_contract.active_profile,
            sorted(restriction_contract.allowed_tools),
            restriction_contract.allowed_directories,
        )

    config.ensure_dirs()

    session_store = SessionStore(config)
    await session_store.open()

    artifact_store = ArtifactStore(config, session_store.db)
    policy_engine = PolicyEngine(config)
    profile_registry = ProfileRegistry()

    # Resolve and validate execution backend (v0.4)
    execution_backend = build_backend(config)
    logger.info(
        "Execution backend: %s",
        execution_backend.name,
    )

    # Initialize federation (v0.3) — disabled by default, no-op if not configured
    federation = await FederationManager.build(config)
    federation_active = federation.is_active()
    if federation_active:
        logger.info(
            "Federation active: %d allowlisted tool(s) available",
            len(federation.catalog.allowed_tools()),
        )

    executor = WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=artifact_store,
        policy_engine=policy_engine,
        profile_registry=profile_registry,
        execution_backend=execution_backend,
        visibility_resolver=federation.visibility_resolver if federation_active else None,
        federation_server_configs=federation.server_configs,
    )

    # Build runtime status inspector (v1.0.0)
    status_inspector = RuntimeStatusInspector(config)
    status_inspector.set_federation_active(federation_active)

    if config.operator_profile_preset:
        logger.info(
            "Operator profile preset: %s",
            config.operator_profile_preset,
        )

    logger.info(
        "claude-agent-mcp v%s ready — mode=%s backend=%s transport=%s preset=%s",
        VERSION,
        getattr(config, "mode", "standard"),
        config.execution_backend,
        config.transport,
        config.operator_profile_preset or "none",
    )

    return session_store, artifact_store, executor, status_inspector, restriction_contract


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------

async def run_stdio(config) -> None:
    from claude_agent_mcp.transports.stdio import run_stdio as _run_stdio

    session_store, artifact_store, executor, status_inspector, restriction_contract = (
        await _setup_runtime(config)
    )
    server = build_server(
        session_store, artifact_store, executor, status_inspector, restriction_contract
    )
    try:
        await _run_stdio(server, session_store)
    finally:
        await session_store.close()
        logger.info("claude-agent-mcp (stdio) shut down cleanly")


async def run_streamable_http(config) -> None:
    from claude_agent_mcp.transports.streamable_http import run_streamable_http as _run_http

    session_store, artifact_store, executor, status_inspector, restriction_contract = (
        await _setup_runtime(config)
    )
    server = build_server(
        session_store, artifact_store, executor, status_inspector, restriction_contract
    )
    try:
        await _run_http(server, host=config.host, port=config.port)
    finally:
        await session_store.close()
        logger.info("claude-agent-mcp (streamable-http) shut down cleanly")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claude-agent-mcp",
        description="Sessioned Claude-backed agent runtime over MCP",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"claude-agent-mcp {VERSION}",
    )
    parser.add_argument(
        "--mode",
        choices=["standard", "apntalk_verification"],
        default=None,
        help=(
            "Runtime mode (default: CLAUDE_AGENT_MCP_MODE env var, or 'standard'). "
            "'apntalk_verification' activates the restricted verification-only surface."
        ),
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http"],
        default=None,
        help="Transport to use (default: CLAUDE_AGENT_MCP_TRANSPORT env var, or stdio)",
    )
    parser.add_argument(
        "--host",
        default=None,
        help="Bind host for streamable-http transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Bind port for streamable-http transport (default: 8000)",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    # Config is loaded from env first; CLI flags override specific fields.
    config = get_config()

    if args.mode is not None:
        config.mode = args.mode
    if args.transport is not None:
        config.transport = args.transport
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port

    config.validate()
    configure_logging(config.log_level)

    logger.info(
        "Starting claude-agent-mcp v%s mode=%s transport=%s model=%s backend=%s",
        VERSION,
        getattr(config, "mode", "standard"),
        config.transport,
        config.model,
        config.execution_backend,
    )

    if config.transport == "stdio":
        asyncio.run(run_stdio(config))
    elif config.transport == "streamable-http":
        logger.info("Binding to %s:%d", config.host, config.port)
        asyncio.run(run_streamable_http(config))
    else:
        # Should be unreachable after validate()
        sys.exit(f"Unknown transport: {config.transport}")


if __name__ == "__main__":
    main()
