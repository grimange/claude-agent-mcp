# Troubleshooting — claude-agent-mcp v0.2

## Startup failures

### "startup configuration error" on launch

```
claude-agent-mcp startup configuration error(s):
  • CLAUDE_AGENT_MCP_TRANSPORT='sse' is not valid. Choose from: ['stdio', 'streamable-http']
```

**Cause**: An environment variable contains an invalid value.

**Fix**: Check your `.env` file or environment against `.env.example`.
Valid transports are `stdio` and `streamable-http`.

---

### "ANTHROPIC_API_KEY not set" or authentication errors

**Cause**: The API key is missing or invalid.

**Fix**:
```bash
export ANTHROPIC_API_KEY=sk-ant-...
# or add it to your .env file
```

---

### Port already in use (streamable-http)

```
[Errno 98] error while attempting to bind on address ('127.0.0.1', 8000)
```

**Fix**: Choose a different port:
```bash
claude-agent-mcp --transport streamable-http --port 8001
```

Or find and kill the process using port 8000:
```bash
lsof -i :8000
kill <PID>
```

---

### "Permission denied" creating state directory

**Cause**: The process cannot write to `CLAUDE_AGENT_MCP_STATE_DIR`.

**Fix**: Set the state dir to a writable location:
```bash
CLAUDE_AGENT_MCP_STATE_DIR=/tmp/claude-agent-state claude-agent-mcp
```

---

## Session issues

### Session shows as `interrupted` after restart

**Cause**: Expected behaviour. Sessions that were `running` when the server
stopped are recovered as `interrupted` on restart. This is the v0.1 crash
recovery guarantee.

**Fix**: Use `agent_continue_session` to resume work, or start a new session
with `agent_run_task`.

---

### `agent_continue_session` returns "session not found"

**Cause**: The session ID does not exist or the state directory changed.

**Fix**: Check the DB path is consistent:
```bash
CLAUDE_AGENT_MCP_STATE_DIR=/the/same/dir claude-agent-mcp
```

Verify the session exists:
```
agent_list_sessions {}
```

---

### Session stuck in `running`

**Cause**: A previous run crashed mid-execution and the lock did not release.

**Fix**: Locks expire automatically after `CLAUDE_AGENT_MCP_LOCK_TTL` seconds
(default: 300). After expiry, the session will be recoverable.

To reduce the wait, lower the TTL temporarily:
```bash
CLAUDE_AGENT_MCP_LOCK_TTL=30 claude-agent-mcp
```

---

## Network transport issues

### Requests to `/mcp` return 404 with "Session not found"

**Cause**: The client sent a request with an `Mcp-Session-Id` header that the
server does not recognise (e.g. after a server restart).

**Fix**: Drop the `Mcp-Session-Id` header to start a new MCP HTTP session.
The MCP session tracked by the HTTP transport is separate from the durable
agent session stored in SQLite.

---

### `streamable-http` server does not respond

**Cause**: The server may not have started, or the bind address differs from
what the client is connecting to.

**Fix**:
```bash
# Check the server is listening
ss -tlnp | grep 8000

# Confirm the bind address
curl -v http://127.0.0.1:8000/mcp
```

---

### Client reports connection refused

**Cause**: The server may be bound to `127.0.0.1` but the client is connecting
to a different address (e.g. the container or VM's external IP).

**Fix** (local): Ensure both sides use `127.0.0.1`.

**Fix** (cross-host): Only change the bind address if you understand the
security implications. See docs/transports.md for the trust boundary notes.

---

## Tool execution failures

### `agent_run_task` returns `ok: false` with policy error

**Cause**: The requested `working_directory` is not in `CLAUDE_AGENT_MCP_ALLOWED_DIRS`.

**Fix**: Add the directory:
```bash
CLAUDE_AGENT_MCP_ALLOWED_DIRS=/path/to/project,/other/path claude-agent-mcp
```

---

### Task fails with "provider runtime failure"

**Cause**: The Anthropic API returned an error (rate limit, invalid model, etc.).

**Fix**: Check the log output for the specific API error. Set
`CLAUDE_AGENT_MCP_LOG_LEVEL=DEBUG` for full detail:
```bash
CLAUDE_AGENT_MCP_LOG_LEVEL=DEBUG claude-agent-mcp 2>debug.log
```

---

## Diagnostics

### Enable debug logging

```bash
CLAUDE_AGENT_MCP_LOG_LEVEL=DEBUG claude-agent-mcp --transport streamable-http 2>&1 | tee server.log
```

### Inspect the database directly

```bash
sqlite3 .state/claude-agent-mcp.db "SELECT session_id, status, created_at FROM sessions ORDER BY created_at DESC LIMIT 10;"
```

### Run the test suite

```bash
pytest tests/ -q
```

All tests should pass. A failure here indicates an installation problem.
