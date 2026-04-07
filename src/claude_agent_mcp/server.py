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

from claude_agent_mcp.config import get_config
from claude_agent_mcp.logging import configure_logging, get_logger
from claude_agent_mcp.runtime.agent_adapter import ClaudeAdapter
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.session_store import SessionStore
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.tools.continue_session import handle_continue_session
from claude_agent_mcp.tools.get_session import handle_get_session
from claude_agent_mcp.tools.list_sessions import handle_list_sessions
from claude_agent_mcp.tools.run_task import handle_run_task
from claude_agent_mcp.tools.verify_task import handle_verify_task

logger = get_logger(__name__)

VERSION = "0.2.0"

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
]


# ---------------------------------------------------------------------------
# Server wiring (transport-agnostic)
# ---------------------------------------------------------------------------

def build_server(
    session_store: SessionStore,
    artifact_store: ArtifactStore,
    executor: WorkflowExecutor,
) -> Server:
    """Build and return the MCP Server with all v0.1 tools registered.

    This function is transport-agnostic. Both stdio and streamable-http
    transports call this to obtain the same server instance.
    """
    server = Server("claude-agent-mcp")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return TOOL_DEFINITIONS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        logger.info("Tool called: %s", name)
        arguments = arguments or {}

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
    """Open stores and build executor. Returns (session_store, artifact_store, executor)."""
    config.ensure_dirs()

    session_store = SessionStore(config)
    await session_store.open()

    artifact_store = ArtifactStore(config, session_store.db)
    policy_engine = PolicyEngine(config)
    profile_registry = ProfileRegistry()
    agent_adapter = ClaudeAdapter(config)

    executor = WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=artifact_store,
        policy_engine=policy_engine,
        profile_registry=profile_registry,
        agent_adapter=agent_adapter,
    )

    return session_store, artifact_store, executor


# ---------------------------------------------------------------------------
# Transport runners
# ---------------------------------------------------------------------------

async def run_stdio(config) -> None:
    from claude_agent_mcp.transports.stdio import run_stdio as _run_stdio

    session_store, artifact_store, executor = await _setup_runtime(config)
    server = build_server(session_store, artifact_store, executor)
    try:
        await _run_stdio(server, session_store)
    finally:
        await session_store.close()
        logger.info("claude-agent-mcp (stdio) shut down cleanly")


async def run_streamable_http(config) -> None:
    from claude_agent_mcp.transports.streamable_http import run_streamable_http as _run_http

    session_store, artifact_store, executor = await _setup_runtime(config)
    server = build_server(session_store, artifact_store, executor)
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

    if args.transport is not None:
        config.transport = args.transport
    if args.host is not None:
        config.host = args.host
    if args.port is not None:
        config.port = args.port

    config.validate()
    configure_logging(config.log_level)

    logger.info(
        "Starting claude-agent-mcp v%s transport=%s model=%s",
        VERSION,
        config.transport,
        config.model,
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
