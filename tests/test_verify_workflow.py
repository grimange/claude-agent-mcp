"""Tests for the verification workflow."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.runtime.agent_adapter import ClaudeAdapter
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import (
    NormalizedProviderResult,
    ProfileName,
    SessionStatus,
    VerificationVerdict,
    VerifyTaskRequest,
    WorkflowName,
)


def _make_executor_with_output(config, session_store, output_text: str) -> WorkflowExecutor:
    adapter = MagicMock(spec=ClaudeAdapter)
    adapter.run = AsyncMock(
        return_value=NormalizedProviderResult(
            output_text=output_text,
            turn_count=1,
            stop_reason="end_turn",
        )
    )
    return WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=ArtifactStore(config, session_store.db),
        policy_engine=PolicyEngine(config),
        profile_registry=ProfileRegistry(),
        agent_adapter=adapter,
    )


PASS_OUTPUT = """
VERDICT: pass
FINDINGS:
- Evidence file exists and matches expected content
CONTRADICTIONS:
MISSING_EVIDENCE:
RESTRICTIONS:
"""

FAIL_OUTPUT = """
VERDICT: fail_closed
FINDINGS:
- Evidence is inconsistent with the claim
CONTRADICTIONS:
- Claim says X but evidence shows Y
MISSING_EVIDENCE:
- signature file
RESTRICTIONS:
"""

PASS_WITH_RESTRICTIONS_OUTPUT = """
VERDICT: pass_with_restrictions
FINDINGS:
- Core functionality verified
CONTRADICTIONS:
MISSING_EVIDENCE:
RESTRICTIONS:
- Only verified against test environment
"""

INSUFFICIENT_OUTPUT = """
VERDICT: insufficient_evidence
FINDINGS:
CONTRADICTIONS:
MISSING_EVIDENCE:
- No evidence files provided
RESTRICTIONS:
"""


@pytest.mark.asyncio
async def test_verify_task_pass_verdict(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify the report")
    response = await executor.verify_task(req)

    assert response.ok is True
    assert response.workflow == WorkflowName.verify_task
    assert response.profile == ProfileName.verification
    result = response.result
    assert result["verdict"] == VerificationVerdict.pass_.value
    assert len(result["findings"]) > 0


@pytest.mark.asyncio
async def test_verify_task_fail_closed_verdict(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Verify the claim", fail_closed=True)
    response = await executor.verify_task(req)

    assert response.ok is True
    result = response.result
    assert result["verdict"] == VerificationVerdict.fail_closed.value
    assert len(result["contradictions"]) > 0
    assert len(result["missing_evidence"]) > 0


@pytest.mark.asyncio
async def test_verify_task_pass_with_restrictions(config, session_store):
    executor = _make_executor_with_output(
        config, session_store, PASS_WITH_RESTRICTIONS_OUTPUT
    )
    req = VerifyTaskRequest(task="Verify partial coverage")
    response = await executor.verify_task(req)

    assert response.result["verdict"] == VerificationVerdict.pass_with_restrictions.value
    assert len(response.result["restrictions"]) > 0


@pytest.mark.asyncio
async def test_verify_task_fail_closed_on_missing_evidence_paths(config, session_store, tmp_path):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify with missing evidence",
        evidence_paths=[str(tmp_path / "nonexistent.txt")],
        fail_closed=True,
    )
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["verdict"] == VerificationVerdict.fail_closed.value


@pytest.mark.asyncio
async def test_verify_task_creates_verification_session(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify something")
    response = await executor.verify_task(req)

    detail = await session_store.get_session_detail(response.session_id)
    assert detail.profile == ProfileName.verification
    assert detail.workflow == WorkflowName.verify_task
    assert detail.status == SessionStatus.completed


@pytest.mark.asyncio
async def test_verify_task_with_existing_evidence(config, session_store, tmp_path):
    """Verification should proceed when evidence files exist."""
    evidence_file = tmp_path / "evidence.txt"
    evidence_file.write_text("Evidence content here.")

    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify with real evidence",
        evidence_paths=[str(evidence_file)],
    )
    response = await executor.verify_task(req)
    assert response.ok is True


@pytest.mark.asyncio
async def test_verify_task_result_fields_present(config, session_store):
    """Result must always include all five verification fields."""
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Check fields")
    response = await executor.verify_task(req)

    result = response.result
    for key in ("verdict", "findings", "contradictions", "missing_evidence", "restrictions"):
        assert key in result, f"Missing result field: {key}"


@pytest.mark.asyncio
async def test_verify_task_insufficient_without_fail_closed(config, session_store):
    executor = _make_executor_with_output(config, session_store, INSUFFICIENT_OUTPUT)
    req = VerifyTaskRequest(task="Verify with no evidence", fail_closed=False)
    response = await executor.verify_task(req)

    assert response.result["verdict"] == VerificationVerdict.insufficient_evidence.value
