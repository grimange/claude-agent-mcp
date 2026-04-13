---
name: project_v112_state
description: v1.1.2 dependability hardening — backend failure classification, unavailable result, retry/fallback signals, 700 tests pass
type: project
---
v1.1.2 dependability hardening is fully implemented and all 700 tests pass.

**Why:** APNTalk/Codex orchestrators need machine-stable failure codes when the Claude Code backend is unavailable, so they can route (retry, fallback) without text-parsing the summary field.

**What was added:**
- `VerificationFailureClass` enum (6 values) in `types.py`
- `VerificationFailureCode` enum (7 values) in `types.py`
- `VerificationDecision.unavailable` value added to existing enum in `types.py`
- `src/claude_agent_mcp/runtime/verification_failure.py` — operational failure classification module: `FailureClassificationResult`, `classify_backend_failure()`, `classify_empty_response()`, `RETRYABLE_CLASSES`, `FALLBACK_RECOMMENDED_CLASSES`
- New `agent_verify_task` result fields: `outcome_kind`, `failure_class`, `failure_code`, `retryable`, `fallback_recommended`, `verification_performed`
- Short-circuit in `workflow_executor.py::verify_task()`: backend exceptions caught BEFORE `AgentMCPError`; empty response checked BEFORE parse — both produce `outcome_kind="unavailable"` without pseudo-verification
- `tests/test_v112_dependability.py` — 56 new tests
- Version bumped to 1.1.2 in `server.py` and `pyproject.toml`
- `CHANGELOG.md` updated with v1.1.2 entry
- `docs/operator-guide.md` title updated to v1.1.2; section 14 added (backend availability failures, failure taxonomy, routing semantics, example payloads)
- `README.md` updated with v1.1.2 badge, new feature bullet, updated verification example, updated test count (700)

**Key invariants:**
- `outcome_kind = "unavailable"` is strictly for operational failures; `not_verified`/`inconclusive` are never used for backend failures
- `verdict = "fail_closed"` is always set in unavailable results for backward compat with v1.0 consumers
- `verification_performed = True` only when backend ran and returned parseable output
- All pre-v1.1.1 fields preserved unchanged

**How to apply:** When adding new failure modes, extend `VerificationFailureCode` and `VerificationFailureClass` and update `classify_backend_failure()`. The taxonomy separation (domain codes vs operational codes) must be preserved.
