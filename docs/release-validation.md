# Release Validation Checklist (v1.0.0)

This document defines the steps required to validate a `claude-agent-mcp` release before publishing.

---

## Pre-release checks

### 1. Version consistency

- [ ] `pyproject.toml` `version` = `1.0.0`
- [ ] `server.py` `VERSION` = `"1.0.0"`
- [ ] `runtime/status_inspector.py` `VERSION` = `"1.0.0"`
- [ ] `CHANGELOG.md` has a `[1.0.0]` entry with today's date
- [ ] `README.md` states `v1.0.0` as current version

Verify:
```bash
grep 'version' pyproject.toml
grep 'VERSION' src/claude_agent_mcp/server.py
grep 'VERSION' src/claude_agent_mcp/runtime/status_inspector.py
```

---

### 2. Full test suite

```bash
pip install -e ".[dev]"
python -m pytest tests/ -q
```

Expected: all tests pass, zero failures.

---

### 3. Smoke test — basic startup (api backend)

```bash
ANTHROPIC_API_KEY=sk-ant-... python -c "
from claude_agent_mcp.config import Config
c = Config()
c.validate()
print('Config OK:', c.execution_backend, c.transport)
"
```

---

### 4. Smoke test — operator preset config resolution

```bash
python -c "
import os
os.environ['CLAUDE_AGENT_MCP_OPERATOR_PROFILE'] = 'mediation_enabled'
from claude_agent_mcp.config import Config
c = Config()
assert c.operator_profile_preset == 'mediation_enabled'
assert c.claude_code_enable_execution_mediation is True
print('Preset OK')
"
```

---

### 5. Smoke test — runtime status inspector

```bash
python -c "
from claude_agent_mcp.config import Config
from claude_agent_mcp.runtime.status_inspector import RuntimeStatusInspector
config = Config()
inspector = RuntimeStatusInspector(config)
snapshot = inspector.build_snapshot()
assert snapshot.version == '1.0.0'
assert isinstance(snapshot.preserved_limitations, list)
print('Status inspector OK:', snapshot.version, snapshot.backend)
"
```

---

### 6. Smoke test — audit presenter

```bash
python -c "
from claude_agent_mcp.runtime.audit_presenter import AuditPresenter
result = AuditPresenter.mediation_summary([])
assert result['single_action']['requested'] == 0
msg = AuditPresenter.format_tool_downgrade_warning(2, 'claude_code')
assert '[tool_downgrade]' in msg
print('AuditPresenter OK')
"
```

---

### 7. Smoke test — agent_get_runtime_status tool (without MCP transport)

```bash
python -c "
from claude_agent_mcp.server import TOOL_DEFINITIONS
names = {t.name for t in TOOL_DEFINITIONS}
assert 'agent_get_runtime_status' in names
v01 = {'agent_run_task','agent_continue_session','agent_get_session','agent_list_sessions','agent_verify_task'}
assert v01.issubset(names)
print('Tool definitions OK:', sorted(names))
"
```

---

### 8. Import validation

```bash
python -c "
from claude_agent_mcp.types import OperatorProfilePreset, WarningCode, RuntimeStatusSnapshot
from claude_agent_mcp.config import Config, _OPERATOR_PRESET_DEFAULTS
from claude_agent_mcp.runtime.status_inspector import RuntimeStatusInspector
from claude_agent_mcp.runtime.audit_presenter import AuditPresenter
from claude_agent_mcp.server import VERSION, TOOL_DEFINITIONS
print('All imports OK')
print('Presets:', list(_OPERATOR_PRESET_DEFAULTS.keys()))
print('Server version:', VERSION)
"
```

---

### 9. Backward compatibility check

```bash
python -c "
# Verify v0.1 response envelope shape is unchanged
from claude_agent_mcp.types import AgentResponse, SessionStatus, WorkflowName, ProfileName
r = AgentResponse(
    ok=True, session_id='sess_001', status=SessionStatus.completed,
    workflow=WorkflowName.run_task, profile=ProfileName.general,
    summary='OK',
)
d = r.model_dump()
assert set(d.keys()) >= {'ok','session_id','status','workflow','profile','summary','result','artifacts','warnings','errors'}
print('Response envelope OK')
"
```

---

### 10. Package build (optional)

```bash
pip install build
python -m build
ls dist/
```

Inspect the wheel contents:
```bash
python -m zipfile -l dist/claude_agent_mcp-1.0.0-*.whl | grep claude_agent_mcp
```

Verify `status_inspector.py` and `audit_presenter.py` are included.

---

## Post-release checks

After publishing the package:

- [ ] Install from the published source and verify `import claude_agent_mcp` works
- [ ] Verify `claude-agent-mcp --version` reports `claude-agent-mcp 1.0.0`
- [ ] Confirm `CHANGELOG.md` is committed and pushed with the release tag

---

## Compatibility statement

v1.0.0 preserves backward compatibility with all v0.6, v0.7, v0.8, and v0.9 deployments:

| Contract | Status |
|----------|--------|
| `agent_run_task` schema | **Unchanged** |
| `agent_continue_session` schema | **Unchanged** |
| `agent_get_session` schema | **Unchanged** |
| `agent_list_sessions` schema | **Unchanged** |
| `agent_verify_task` schema | **Unchanged** |
| Canonical response envelope shape | **Unchanged** |
| v0.8.0 single-action mediation format | **Unchanged** |
| v0.9.0 bounded workflow mediation format | **Unchanged** |
| All existing env vars | **Unchanged** |
| `agent_get_runtime_status` | **New (additive)** |
| Operator profile presets | **New (additive)** |
| `reconstruction_version` string | Updated: `"v0.9.0"` → `"v1.0.0"` |
