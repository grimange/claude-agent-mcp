# Upgrade Guide — v1.0.0

This guide covers what changed in v1.0.0 and how to migrate from v0.6, v0.7, v0.8, and v0.9.

---

## What v1.0.0 is

v1.0.0 is a **stabilization, operator UX, and production-hardening release**.

It is not a capability expansion. No breaking changes were made to MCP tool contracts, response envelopes, or v0.8/v0.9 mediation formats.

---

## Breaking changes

**None.** v1.0.0 is fully backward-compatible with v0.6 through v0.9.

- All five v0.1 MCP tool contracts are unchanged.
- The canonical response envelope shape is unchanged.
- The v0.8.0 single-action mediation format is preserved.
- The v0.9.0 bounded workflow request format is preserved.
- All existing env vars remain valid.

---

## What changed

### New: operator profile presets

```bash
export CLAUDE_AGENT_MCP_OPERATOR_PROFILE=safe_default
```

Four presets are available: `safe_default`, `continuity_optimized`, `mediation_enabled`, `workflow_limited`.

If not set, behavior is identical to prior versions (hardcoded defaults apply). **No action required** unless you want to adopt a preset.

### New: `agent_get_runtime_status` tool

An additive MCP tool that returns a resolved status snapshot. No action required — the tool is available automatically and does not affect existing behavior.

### New: `AuditPresenter` helpers

`src/claude_agent_mcp/runtime/audit_presenter.py` — programmatic summaries from session event logs. Optional; no integration changes required.

### New: `RuntimeStatusInspector`

`src/claude_agent_mcp/runtime/status_inspector.py` — builds `RuntimeStatusSnapshot` from config. Used internally; exposed via `agent_get_runtime_status`.

### Changed: reconstruction version

The continuation context `reconstruction_version` field changed from `"v0.9.0"` to `"v1.0.0"`. This is recorded in session events. Downstream logic that parsed this string for version comparisons should be updated.

### Changed: startup log

A new INFO log line is emitted at startup:
```
claude-agent-mcp v1.0.0 ready — backend=... transport=... preset=...
```

### Changed: package version

`pyproject.toml` version is now `1.0.0`.

### Changed: server VERSION constant

`server.py` `VERSION` constant is now `"1.0.0"`.

---

## Migration by starting version

### Upgrading from v0.9.0

- No config changes required.
- No code changes required.
- Optionally: set `CLAUDE_AGENT_MCP_OPERATOR_PROFILE` to adopt a preset.
- Optionally: call `agent_get_runtime_status` to inspect resolved config.

### Upgrading from v0.8.0

Same as from v0.9.0. No breaking changes from v0.8 to v1.0.0.

### Upgrading from v0.7.0

Same as above. The continuation context reconstruction pipeline is unchanged in behavior (version string updated only).

### Upgrading from v0.6.0

- All v0.6 capability flags (`supports_limited_downstream_tools`, etc.) remain valid.
- No changes to the limited tool forwarding feature.
- No changes to the MCP tool surface.

---

## Adoption recommendations for v1.0.0

### If you run the api backend

- No changes required.
- Optionally: set `CLAUDE_AGENT_MCP_OPERATOR_PROFILE=safe_default` to document your deployment config explicitly.

### If you run the claude_code backend without mediation

- No changes required.
- Optionally: set `CLAUDE_AGENT_MCP_OPERATOR_PROFILE=continuity_optimized` if continuation context depth matters.

### If you run the claude_code backend with mediation

- No changes required.
- Optionally: set `CLAUDE_AGENT_MCP_OPERATOR_PROFILE=mediation_enabled` or `workflow_limited` to align with your usage pattern.
- Review `agent_get_runtime_status` output to verify resolved policy settings.

---

## Verifying your upgrade

After upgrading:

1. Check startup logs for `claude-agent-mcp v1.0.0 ready`.
2. Call `agent_get_runtime_status` and verify `version` is `"1.0.0"`.
3. Run a test session with `agent_run_task` and verify the response envelope is unchanged.
4. If using mediation, verify `agent_get_runtime_status` shows the expected mediation settings.
5. Run your test suite — full regression suite should pass without changes (unless you parsed the `reconstruction_version` string directly, which should now be `"v1.0.0"`).

---

## Preserved limitations (unchanged from v0.9.0)

- No native `tool_use` / `tool_result` in Claude Code backend
- No streaming transport
- No cross-backend session migration
- No broad autonomous execution chaining
- Mediated execution requires active federation
