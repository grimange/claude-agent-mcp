"""Tests for federation catalog and visibility resolution.

Deterministic — no network or subprocess access required.
"""

from __future__ import annotations

import pytest

from claude_agent_mcp.federation.catalog import ToolCatalog
from claude_agent_mcp.federation.models import DiscoveredTool, DownstreamServerConfig
from claude_agent_mcp.federation.visibility import ToolVisibilityResolver
from claude_agent_mcp.types import ProfileName


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _server(
    name: str = "fs_tools",
    allowed_tools: list[str] | None = None,
    profiles_allowed: list[str] | None = None,
) -> DownstreamServerConfig:
    return DownstreamServerConfig(
        name=name,
        transport="stdio",
        command="python",
        allowed_tools=allowed_tools or ["read_file", "list_dir"],
        profiles_allowed=profiles_allowed or ["general"],
    )


def _discovered(
    server_name: str = "fs_tools",
    tool_name: str = "read_file",
) -> DiscoveredTool:
    normalized = f"{server_name}__{tool_name}"
    return DiscoveredTool(
        downstream_server_name=server_name,
        downstream_tool_name=tool_name,
        normalized_name=normalized,
        description=f"Read a file ({tool_name})",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    )


# ---------------------------------------------------------------------------
# ToolCatalog.build
# ---------------------------------------------------------------------------


class TestToolCatalogBuild:
    def test_allowlisted_tool_is_allowed(self):
        server = _server(allowed_tools=["read_file"], profiles_allowed=["general"])
        tool = _discovered(tool_name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        assert catalog.is_allowed("fs_tools__read_file")

    def test_non_allowlisted_tool_is_not_allowed(self):
        server = _server(allowed_tools=["list_dir"], profiles_allowed=["general"])
        tool = _discovered(tool_name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        # read_file is discovered but not in allowed_tools
        assert not catalog.is_allowed("fs_tools__read_file")

    def test_discovered_tool_without_matching_server_config_excluded(self):
        server = _server(name="other_server")
        tool = _discovered(server_name="fs_tools", tool_name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        # Tool's server has no config → not in catalog
        assert catalog.get("fs_tools__read_file") is None

    def test_collision_first_wins(self):
        server1 = _server(name="s1", allowed_tools=["read_file"])
        server2 = _server(name="s1", allowed_tools=["read_file"])  # same normalized name
        tool1 = _discovered(server_name="s1", tool_name="read_file")
        tool2 = _discovered(server_name="s1", tool_name="read_file")  # duplicate
        catalog = ToolCatalog.build([tool1, tool2], [server1, server2])
        # Only one entry should exist
        assert len(catalog.all_tools()) == 1

    def test_multiple_tools_from_same_server(self):
        server = _server(allowed_tools=["read_file", "list_dir"])
        tools = [
            _discovered(tool_name="read_file"),
            _discovered(tool_name="list_dir"),
        ]
        catalog = ToolCatalog.build(tools, [server])
        assert len(catalog.allowed_tools()) == 2

    def test_empty_catalog(self):
        catalog = ToolCatalog.empty()
        assert catalog.all_tools() == []
        assert catalog.allowed_tools() == []

    def test_profiles_allowed_propagated_from_server(self):
        server = _server(allowed_tools=["read_file"], profiles_allowed=["general", "verification"])
        tool = _discovered(tool_name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        catalogued = catalog.get("fs_tools__read_file")
        assert catalogued is not None
        assert "general" in catalogued.profiles_allowed
        assert "verification" in catalogued.profiles_allowed

    def test_tool_anthropic_dict_format(self):
        server = _server(allowed_tools=["read_file"])
        tool = _discovered(tool_name="read_file")
        catalog = ToolCatalog.build([tool], [server])
        catalogued = catalog.get("fs_tools__read_file")
        tool_dict = catalogued.to_anthropic_tool_dict()
        assert tool_dict["name"] == "fs_tools__read_file"
        assert "description" in tool_dict
        assert "input_schema" in tool_dict


# ---------------------------------------------------------------------------
# ToolVisibilityResolver
# ---------------------------------------------------------------------------


class TestToolVisibilityResolver:
    def _make_resolver(
        self,
        tool_name: str = "read_file",
        server_name: str = "fs_tools",
        allowed_tools: list[str] | None = None,
        profiles_allowed: list[str] | None = None,
    ) -> ToolVisibilityResolver:
        server = _server(
            name=server_name,
            allowed_tools=allowed_tools or [tool_name],
            profiles_allowed=profiles_allowed or ["general"],
        )
        tool = _discovered(server_name=server_name, tool_name=tool_name)
        catalog = ToolCatalog.build([tool], [server])
        return ToolVisibilityResolver(catalog)

    def test_general_sees_allowlisted_tool(self):
        resolver = self._make_resolver(profiles_allowed=["general"])
        visible = resolver.resolve(ProfileName.general)
        assert len(visible) == 1
        assert visible[0].normalized_name == "fs_tools__read_file"

    def test_verification_sees_nothing_by_default(self):
        resolver = self._make_resolver(profiles_allowed=["general"])
        visible = resolver.resolve(ProfileName.verification)
        assert visible == []

    def test_verification_can_see_if_explicitly_permitted(self):
        resolver = self._make_resolver(profiles_allowed=["general", "verification"])
        visible = resolver.resolve(ProfileName.verification)
        assert len(visible) == 1

    def test_non_allowlisted_tool_not_visible_to_general(self):
        server = _server(
            allowed_tools=["list_dir"],  # read_file NOT in allowlist
            profiles_allowed=["general"],
        )
        tool = _discovered(tool_name="read_file")  # discovered but not allowed
        catalog = ToolCatalog.build([tool], [server])
        resolver = ToolVisibilityResolver(catalog)
        visible = resolver.resolve(ProfileName.general)
        assert visible == []

    def test_empty_catalog_returns_nothing(self):
        resolver = ToolVisibilityResolver.from_empty()
        assert resolver.resolve(ProfileName.general) == []
        assert resolver.resolve(ProfileName.verification) == []

    def test_is_visible_true_for_permitted(self):
        resolver = self._make_resolver(profiles_allowed=["general"])
        assert resolver.is_visible("fs_tools__read_file", ProfileName.general)

    def test_is_visible_false_for_wrong_profile(self):
        resolver = self._make_resolver(profiles_allowed=["general"])
        assert not resolver.is_visible("fs_tools__read_file", ProfileName.verification)

    def test_is_visible_false_for_unknown_tool(self):
        resolver = self._make_resolver(profiles_allowed=["general"])
        assert not resolver.is_visible("no_such__tool", ProfileName.general)

    def test_multiple_tools_filtered_per_profile(self):
        server1 = _server(name="s1", allowed_tools=["t1"], profiles_allowed=["general"])
        server2 = _server(name="s2", allowed_tools=["t2"], profiles_allowed=["verification"])
        tool1 = _discovered(server_name="s1", tool_name="t1")
        tool2 = _discovered(server_name="s2", tool_name="t2")
        catalog = ToolCatalog.build([tool1, tool2], [server1, server2])
        resolver = ToolVisibilityResolver(catalog)

        general_visible = {t.normalized_name for t in resolver.resolve(ProfileName.general)}
        verification_visible = {t.normalized_name for t in resolver.resolve(ProfileName.verification)}

        assert "s1__t1" in general_visible
        assert "s2__t2" not in general_visible
        assert "s2__t2" in verification_visible
        assert "s1__t1" not in verification_visible
