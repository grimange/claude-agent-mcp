"""Tests for federation registry — config loading and validation.

These tests are deterministic and do not require network or subprocess access.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from claude_agent_mcp.errors import DownstreamServerConfigError
from claude_agent_mcp.federation.models import DownstreamServerConfig
from claude_agent_mcp.federation.registry import DownstreamRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _config_file(tmp_path: Path, data: dict) -> Path:
    p = tmp_path / "federation.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _minimal_server(**overrides) -> dict:
    base = {
        "name": "test_server",
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "my_server"],
        "enabled": True,
        "allowed_tools": ["read_file", "list_dir"],
        "profiles_allowed": ["general"],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# from_dict_list
# ---------------------------------------------------------------------------


class TestFromDictList:
    def test_valid_server_loaded(self):
        registry = DownstreamRegistry.from_dict_list([_minimal_server()])
        servers = registry.all_servers()
        assert len(servers) == 1
        s = servers[0]
        assert s.name == "test_server"
        assert s.transport == "stdio"
        assert s.command == "python"
        assert s.allowed_tools == ["read_file", "list_dir"]
        assert s.profiles_allowed == ["general"]

    def test_empty_list_gives_empty_registry(self):
        registry = DownstreamRegistry.from_dict_list([])
        assert registry.all_servers() == []
        assert registry.enabled_servers() == []

    def test_disabled_server_excluded_from_enabled(self):
        registry = DownstreamRegistry.from_dict_list([
            _minimal_server(enabled=True),
            _minimal_server(name="disabled_server", enabled=False),
        ])
        assert len(registry.all_servers()) == 2
        enabled = registry.enabled_servers()
        assert len(enabled) == 1
        assert enabled[0].name == "test_server"

    def test_missing_name_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="name"):
            DownstreamRegistry.from_dict_list([{"transport": "stdio", "command": "py"}])

    def test_empty_name_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="name"):
            DownstreamRegistry.from_dict_list([_minimal_server(name="")])

    def test_name_with_double_underscore_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="__"):
            DownstreamRegistry.from_dict_list([_minimal_server(name="bad__name")])

    def test_missing_command_raises(self):
        raw = _minimal_server()
        del raw["command"]
        with pytest.raises(DownstreamServerConfigError, match="command"):
            DownstreamRegistry.from_dict_list([raw])

    def test_invalid_transport_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="transport"):
            DownstreamRegistry.from_dict_list([_minimal_server(transport="http")])

    def test_allowed_tools_not_list_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="allowed_tools"):
            DownstreamRegistry.from_dict_list([_minimal_server(allowed_tools="*")])

    def test_profiles_allowed_not_list_raises(self):
        with pytest.raises(DownstreamServerConfigError, match="profiles_allowed"):
            DownstreamRegistry.from_dict_list([_minimal_server(profiles_allowed="general")])

    def test_env_and_args_defaults(self):
        raw = {
            "name": "bare",
            "transport": "stdio",
            "command": "cmd",
            "allowed_tools": ["t1"],
            "profiles_allowed": ["general"],
        }
        registry = DownstreamRegistry.from_dict_list([raw])
        s = registry.all_servers()[0]
        assert s.args == []
        assert s.env == {}
        assert s.enabled is True
        assert s.discovery_timeout_seconds == 10.0

    def test_multiple_servers_loaded(self):
        registry = DownstreamRegistry.from_dict_list([
            _minimal_server(name="s1"),
            _minimal_server(name="s2", command="node"),
        ])
        names = [s.name for s in registry.all_servers()]
        assert "s1" in names
        assert "s2" in names

    def test_non_dict_element_raises(self):
        with pytest.raises(DownstreamServerConfigError):
            DownstreamRegistry.from_dict_list(["not_a_dict"])  # type: ignore


# ---------------------------------------------------------------------------
# from_config_file
# ---------------------------------------------------------------------------


class TestFromConfigFile:
    def test_valid_file_loaded(self, tmp_path):
        path = _config_file(tmp_path, {
            "downstream_servers": [_minimal_server()]
        })
        registry = DownstreamRegistry.from_config_file(path)
        assert len(registry.all_servers()) == 1

    def test_missing_file_raises(self, tmp_path):
        path = tmp_path / "nonexistent.json"
        with pytest.raises(DownstreamServerConfigError, match="Cannot read"):
            DownstreamRegistry.from_config_file(path)

    def test_invalid_json_raises(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json {{{", encoding="utf-8")
        with pytest.raises(DownstreamServerConfigError, match="not valid JSON"):
            DownstreamRegistry.from_config_file(path)

    def test_file_not_object_raises(self, tmp_path):
        path = tmp_path / "list.json"
        path.write_text('["not", "an", "object"]', encoding="utf-8")
        with pytest.raises(DownstreamServerConfigError, match="JSON object"):
            DownstreamRegistry.from_config_file(path)

    def test_servers_not_list_raises(self, tmp_path):
        path = _config_file(tmp_path, {"downstream_servers": "wrong"})
        with pytest.raises(DownstreamServerConfigError, match="list"):
            DownstreamRegistry.from_config_file(path)

    def test_empty_servers_gives_empty_registry(self, tmp_path):
        path = _config_file(tmp_path, {"downstream_servers": []})
        registry = DownstreamRegistry.from_config_file(path)
        assert registry.all_servers() == []

    def test_missing_downstream_servers_key_gives_empty(self, tmp_path):
        path = _config_file(tmp_path, {})
        registry = DownstreamRegistry.from_config_file(path)
        assert registry.all_servers() == []


# ---------------------------------------------------------------------------
# get_server
# ---------------------------------------------------------------------------


class TestGetServer:
    def test_found_by_name(self):
        registry = DownstreamRegistry.from_dict_list([_minimal_server()])
        s = registry.get_server("test_server")
        assert s is not None
        assert s.name == "test_server"

    def test_not_found_returns_none(self):
        registry = DownstreamRegistry.from_dict_list([_minimal_server()])
        assert registry.get_server("nope") is None
