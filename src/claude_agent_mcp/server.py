"""MCP server entry point for claude-agent-mcp.

Registers all v0.1 tools, wires dependencies, and runs over stdio transport.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

import mcp.server.stdio
from mcp.server import Server
from mcp.server.models import InitializationOptions
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
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
# Server wiring
# ---------------------------------------------------------------------------

def build_server(
    session_store: SessionStore,
    artifact_store: ArtifactStore,
    executor: WorkflowExecutor,
) -> Server:
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


async def run() -> None:
    config = get_config()
    configure_logging(config.log_level)
    logger.info("Starting claude-agent-mcp (model=%s)", config.model)

    # Dependency setup
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

    server = build_server(session_store, artifact_store, executor)

    try:
        async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="claude-agent-mcp",
                    server_version="0.1.0",
                    capabilities=server.get_capabilities(
                        notification_options=None,
                        experimental_capabilities={},
                    ),
                ),
            )
    finally:
        await session_store.close()
        logger.info("claude-agent-mcp shut down cleanly")


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
