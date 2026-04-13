"""Tests for v1.1.2 dependability hardening.

Covers:
- VerificationFailureClass / VerificationFailureCode enum stability
- FailureClassificationResult structure
- classify_backend_failure — all exception branches
- classify_empty_response
- RETRYABLE_CLASSES / FALLBACK_RECOMMENDED_CLASSES policy
- No pseudo-verification leakage (unavailable != not_verified / inconclusive)
- retry/fallback signal correctness in response payloads
- Integration: ClaudeCodeUnavailableError → unavailable result
- Integration: ClaudeCodeInvocationError (timeout) → unavailable result
- Integration: ClaudeCodeInvocationError (auth) → unavailable result
- Integration: ClaudeCodeInvocationError (limit) → unavailable result
- Integration: NormalizationError → unavailable result
- Integration: empty response → unavailable result
- Integration: outcome_kind, failure_class, failure_code fields present in all results
- Integration: verification_performed=False when backend fails
- Integration: verification_performed=True for normal completion
- Regression: normal verification result fields still correct
- Regression: restricted-mode hard mismatch still blocks before backend
- Regression: fail-closed on missing evidence still works
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.backends.base import ExecutionBackend
from claude_agent_mcp.errors import (
    ClaudeCodeInvocationError,
    ClaudeCodeUnavailableError,
    NormalizationError,
)
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.verification_failure import (
    FALLBACK_RECOMMENDED_CLASSES,
    RETRYABLE_CLASSES,
    FailureClassificationResult,
    classify_backend_failure,
    classify_empty_response,
)
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import (
    NormalizedProviderResult,
    VerificationDecision,
    VerificationFailureClass,
    VerificationFailureCode,
    VerifyTaskRequest,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor_with_output(config, session_store, output_text: str) -> WorkflowExecutor:
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "claude_code"
    backend.execute = AsyncMock(
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
        execution_backend=backend,
    )


def _make_executor_raising(config, session_store, exc: BaseException) -> WorkflowExecutor:
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "claude_code"
    backend.execute = AsyncMock(side_effect=exc)
    return WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=ArtifactStore(config, session_store.db),
        policy_engine=PolicyEngine(config),
        profile_registry=ProfileRegistry(),
        execution_backend=backend,
    )


def _make_executor_restricted_raising(config, session_store, exc: BaseException) -> WorkflowExecutor:
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "claude_code"
    backend.execute = AsyncMock(side_effect=exc)
    config.mode = "apntalk_verification"
    return WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=ArtifactStore(config, session_store.db),
        policy_engine=PolicyEngine(config),
        profile_registry=ProfileRegistry(),
        execution_backend=backend,
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


# ---------------------------------------------------------------------------
# Enum stability tests
# ---------------------------------------------------------------------------


def test_verification_failure_class_values_stable():
    expected = {
        "backend_unavailable",
        "backend_limit_reached",
        "backend_timeout",
        "backend_auth_failure",
        "backend_invocation_error",
        "backend_unusable_response",
    }
    assert {c.value for c in VerificationFailureClass} == expected


def test_verification_failure_code_values_stable():
    expected = {
        "claude_code_not_installed",
        "claude_code_not_authenticated",
        "claude_code_limit_reached",
        "claude_code_timeout",
        "claude_code_process_error",
        "claude_code_empty_response",
        "claude_code_unparseable_response",
    }
    assert {c.value for c in VerificationFailureCode} == expected


def test_verification_failure_class_is_str_enum():
    for cls in VerificationFailureClass:
        assert isinstance(cls, str)


def test_verification_failure_code_is_str_enum():
    for code in VerificationFailureCode:
        assert isinstance(code, str)


def test_verification_decision_includes_unavailable():
    assert VerificationDecision.unavailable.value == "unavailable"
    assert "unavailable" in {d.value for d in VerificationDecision}


# ---------------------------------------------------------------------------
# FailureClassificationResult structure
# ---------------------------------------------------------------------------


def test_failure_classification_result_fields():
    result = FailureClassificationResult(
        failure_class=VerificationFailureClass.backend_unavailable,
        failure_code=VerificationFailureCode.claude_code_not_installed,
        retryable=False,
        fallback_recommended=True,
        summary="CLI not found.",
    )
    assert result.failure_class == VerificationFailureClass.backend_unavailable
    assert result.failure_code == VerificationFailureCode.claude_code_not_installed
    assert result.retryable is False
    assert result.fallback_recommended is True
    assert "CLI" in result.summary or "not" in result.summary.lower()


def test_failure_classification_result_is_frozen():
    result = FailureClassificationResult(
        failure_class=VerificationFailureClass.backend_unavailable,
        failure_code=VerificationFailureCode.claude_code_not_installed,
        retryable=False,
        fallback_recommended=True,
        summary="x",
    )
    with pytest.raises((AttributeError, TypeError)):
        result.retryable = True  # type: ignore[misc]


# ---------------------------------------------------------------------------
# RETRYABLE_CLASSES / FALLBACK_RECOMMENDED_CLASSES policy
# ---------------------------------------------------------------------------


def test_retryable_classes_includes_limit_and_timeout_and_unusable():
    assert VerificationFailureClass.backend_limit_reached in RETRYABLE_CLASSES
    assert VerificationFailureClass.backend_timeout in RETRYABLE_CLASSES
    assert VerificationFailureClass.backend_unusable_response in RETRYABLE_CLASSES


def test_retryable_classes_excludes_auth_and_unavailable():
    assert VerificationFailureClass.backend_auth_failure not in RETRYABLE_CLASSES
    assert VerificationFailureClass.backend_unavailable not in RETRYABLE_CLASSES


def test_fallback_recommended_classes_includes_all():
    for cls in VerificationFailureClass:
        assert cls in FALLBACK_RECOMMENDED_CLASSES


# ---------------------------------------------------------------------------
# classify_backend_failure — ClaudeCodeUnavailableError
# ---------------------------------------------------------------------------


def test_classify_claude_code_unavailable():
    exc = ClaudeCodeUnavailableError("claude binary not found")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_unavailable
    assert result.failure_code == VerificationFailureCode.claude_code_not_installed
    assert result.retryable is False
    assert result.fallback_recommended is True


def test_classify_claude_code_unavailable_summary_is_descriptive():
    exc = ClaudeCodeUnavailableError("not on PATH")
    result = classify_backend_failure(exc)
    assert len(result.summary) > 0
    assert "claude" in result.summary.lower() or "path" in result.summary.lower()


# ---------------------------------------------------------------------------
# classify_backend_failure — ClaudeCodeInvocationError (timeout)
# ---------------------------------------------------------------------------


def test_classify_invocation_error_timeout():
    exc = ClaudeCodeInvocationError("process timed out after 300s")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_timeout
    assert result.failure_code == VerificationFailureCode.claude_code_timeout
    assert result.retryable is True
    assert result.fallback_recommended is True


def test_classify_invocation_error_timeout_summary():
    exc = ClaudeCodeInvocationError("execution timed out")
    result = classify_backend_failure(exc)
    assert "time" in result.summary.lower() or "timeout" in result.summary.lower()


# ---------------------------------------------------------------------------
# classify_backend_failure — ClaudeCodeInvocationError (auth)
# ---------------------------------------------------------------------------


def test_classify_invocation_error_not_logged_in():
    exc = ClaudeCodeInvocationError("not logged in: please run claude login")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_auth_failure
    assert result.failure_code == VerificationFailureCode.claude_code_not_authenticated
    assert result.retryable is False


def test_classify_invocation_error_unauthenticated():
    exc = ClaudeCodeInvocationError("unauthenticated session")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_auth_failure


def test_classify_invocation_error_token_expired():
    exc = ClaudeCodeInvocationError("token expired, please sign in again")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_auth_failure


def test_classify_invocation_error_auth_fallback_recommended():
    exc = ClaudeCodeInvocationError("credentials missing")
    result = classify_backend_failure(exc)
    assert result.fallback_recommended is True


# ---------------------------------------------------------------------------
# classify_backend_failure — ClaudeCodeInvocationError (limit/quota)
# ---------------------------------------------------------------------------


def test_classify_invocation_error_rate_limit():
    exc = ClaudeCodeInvocationError("too many requests: rate limit exceeded")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_limit_reached
    assert result.failure_code == VerificationFailureCode.claude_code_limit_reached
    assert result.retryable is True


def test_classify_invocation_error_quota():
    exc = ClaudeCodeInvocationError("usage quota exceeded for today")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_limit_reached
    assert result.retryable is True


def test_classify_invocation_error_usage_cap():
    exc = ClaudeCodeInvocationError("monthly usage cap reached")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_limit_reached


def test_classify_invocation_error_limit_fallback_recommended():
    exc = ClaudeCodeInvocationError("daily limit reached")
    result = classify_backend_failure(exc)
    assert result.fallback_recommended is True


# ---------------------------------------------------------------------------
# classify_backend_failure — ClaudeCodeInvocationError (unclassified)
# ---------------------------------------------------------------------------


def test_classify_invocation_error_unknown_process_failure():
    exc = ClaudeCodeInvocationError("process exited with code 1")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_invocation_error
    assert result.failure_code == VerificationFailureCode.claude_code_process_error
    assert result.retryable is False


# ---------------------------------------------------------------------------
# classify_backend_failure — NormalizationError
# ---------------------------------------------------------------------------


def test_classify_normalization_error():
    exc = NormalizationError("could not parse backend output")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_unusable_response
    assert result.failure_code == VerificationFailureCode.claude_code_unparseable_response
    assert result.retryable is True
    assert result.fallback_recommended is True


# ---------------------------------------------------------------------------
# classify_backend_failure — unknown exception
# ---------------------------------------------------------------------------


def test_classify_unknown_exception():
    exc = RuntimeError("something totally unexpected")
    result = classify_backend_failure(exc)
    assert result.failure_class == VerificationFailureClass.backend_invocation_error
    assert result.failure_code == VerificationFailureCode.claude_code_process_error
    assert result.retryable is False


def test_classify_unknown_exception_summary_includes_message():
    exc = RuntimeError("network down")
    result = classify_backend_failure(exc)
    # Summary should contain up to 120 chars of the original message
    assert "network" in result.summary.lower() or "unexpected" in result.summary.lower()


# ---------------------------------------------------------------------------
# classify_empty_response
# ---------------------------------------------------------------------------


def test_classify_empty_response():
    result = classify_empty_response()
    assert result.failure_class == VerificationFailureClass.backend_unusable_response
    assert result.failure_code == VerificationFailureCode.claude_code_empty_response
    assert result.retryable is True
    assert result.fallback_recommended is True


def test_classify_empty_response_summary_is_descriptive():
    result = classify_empty_response()
    assert "empty" in result.summary.lower() or "blank" in result.summary.lower()


# ---------------------------------------------------------------------------
# No pseudo-verification leakage
# ---------------------------------------------------------------------------


def test_unavailable_decision_is_distinct_from_not_verified():
    assert VerificationDecision.unavailable != VerificationDecision.not_verified


def test_unavailable_decision_is_distinct_from_inconclusive():
    assert VerificationDecision.unavailable != VerificationDecision.inconclusive


def test_backend_unavailable_result_has_outcome_kind_unavailable_not_not_verified(
    config, session_store
):
    """ClaudeCodeUnavailableError must produce outcome_kind=unavailable, NOT not_verified."""
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.txt satisfies requirement A")
    import asyncio
    response = asyncio.get_event_loop().run_until_complete(executor.verify_task(req))
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["outcome_kind"] != "not_verified"
    assert response.result["outcome_kind"] != "inconclusive"


def test_empty_response_result_has_outcome_kind_unavailable(config, session_store):
    """Empty backend response must produce outcome_kind=unavailable, NOT inconclusive."""
    executor = _make_executor_with_output(config, session_store, "   ")
    req = VerifyTaskRequest(task="Verify /data/spec.txt satisfies requirement A")
    import asyncio
    response = asyncio.get_event_loop().run_until_complete(executor.verify_task(req))
    assert response.result["outcome_kind"] == "unavailable"


# ---------------------------------------------------------------------------
# Integration: ClaudeCodeUnavailableError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_claude_code_unavailable_produces_unavailable_response(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("claude not on PATH")
    )
    req = VerifyTaskRequest(task="Verify that /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["decision"] == "unavailable"
    assert response.result["failure_class"] == "backend_unavailable"
    assert response.result["failure_code"] == "claude_code_not_installed"
    assert response.result["verification_performed"] is False
    assert response.result["retryable"] is False
    assert response.result["fallback_recommended"] is True


@pytest.mark.asyncio
async def test_claude_code_unavailable_sets_verdict_fail_closed(config, session_store):
    """Backward compat: verdict must be fail_closed, not a new value."""
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)
    assert response.result["verdict"] == "fail_closed"


@pytest.mark.asyncio
async def test_claude_code_unavailable_has_error_in_errors(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)
    assert len(response.errors) > 0
    assert any(e.code == "claude_code_not_installed" for e in response.errors)


# ---------------------------------------------------------------------------
# Integration: ClaudeCodeInvocationError (timeout)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_timeout_produces_unavailable_response(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeInvocationError("process timed out")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_timeout"
    assert response.result["failure_code"] == "claude_code_timeout"
    assert response.result["retryable"] is True
    assert response.result["verification_performed"] is False


# ---------------------------------------------------------------------------
# Integration: ClaudeCodeInvocationError (auth)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_auth_failure_produces_unavailable_response(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeInvocationError("not logged in")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_auth_failure"
    assert response.result["failure_code"] == "claude_code_not_authenticated"
    assert response.result["retryable"] is False
    assert response.result["verification_performed"] is False


# ---------------------------------------------------------------------------
# Integration: ClaudeCodeInvocationError (limit)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_invocation_limit_produces_unavailable_response(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeInvocationError("rate limit exceeded")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_limit_reached"
    assert response.result["retryable"] is True
    assert response.result["fallback_recommended"] is True


# ---------------------------------------------------------------------------
# Integration: NormalizationError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normalization_error_produces_unavailable_response(config, session_store):
    executor = _make_executor_raising(
        config, session_store, NormalizationError("could not parse output")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_unusable_response"
    assert response.result["failure_code"] == "claude_code_unparseable_response"
    assert response.result["retryable"] is True
    assert response.result["verification_performed"] is False


# ---------------------------------------------------------------------------
# Integration: empty response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_response_produces_unavailable_result(config, session_store):
    executor = _make_executor_with_output(config, session_store, "")
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_unusable_response"
    assert response.result["failure_code"] == "claude_code_empty_response"
    assert response.result["retryable"] is True
    assert response.result["verification_performed"] is False


@pytest.mark.asyncio
async def test_whitespace_only_response_is_treated_as_empty(config, session_store):
    executor = _make_executor_with_output(config, session_store, "\n   \n\t")
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_code"] == "claude_code_empty_response"


# ---------------------------------------------------------------------------
# Integration: all results include outcome_kind, failure_class, failure_code
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_pass_result_has_dependability_fields(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)

    assert "outcome_kind" in response.result
    assert "failure_class" in response.result
    assert "failure_code" in response.result
    assert "retryable" in response.result
    assert "fallback_recommended" in response.result
    assert "verification_performed" in response.result


@pytest.mark.asyncio
async def test_normal_pass_result_verification_performed_true(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)
    assert response.result["verification_performed"] is True


@pytest.mark.asyncio
async def test_normal_pass_failure_class_is_none(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)
    assert response.result["failure_class"] is None
    assert response.result["failure_code"] is None


@pytest.mark.asyncio
async def test_normal_pass_retryable_false(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)
    assert response.result["retryable"] is False
    assert response.result["fallback_recommended"] is False


@pytest.mark.asyncio
async def test_normal_fail_closed_result_verification_performed_true(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies requirement B")
    response = await executor.verify_task(req)
    assert response.result["verification_performed"] is True
    assert response.result["failure_class"] is None


@pytest.mark.asyncio
async def test_unavailable_result_has_backward_compat_fields(config, session_store):
    """All backward-compat fields must be present in unavailable results."""
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)

    assert "verdict" in response.result
    assert "findings" in response.result
    assert "contradictions" in response.result
    assert "missing_evidence" in response.result
    assert "restrictions" in response.result
    # And v1.1.1 fields
    assert "decision" in response.result
    assert "primary_reason" in response.result
    assert "reason_codes" in response.result
    assert "operator_guidance" in response.result
    assert "evidence_sufficiency" in response.result
    assert "scope_assessment" in response.result
    assert "profile_alignment" in response.result


# ---------------------------------------------------------------------------
# Integration: retry/fallback signal correctness
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unavailable_result_retryable_true_for_timeout(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeInvocationError("process timed out")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)
    assert response.result["retryable"] is True


@pytest.mark.asyncio
async def test_unavailable_result_retryable_false_for_missing_cli(config, session_store):
    executor = _make_executor_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
    response = await executor.verify_task(req)
    assert response.result["retryable"] is False


@pytest.mark.asyncio
async def test_unavailable_result_fallback_recommended_always_true(config, session_store):
    for exc in [
        ClaudeCodeUnavailableError("not installed"),
        ClaudeCodeInvocationError("timed out"),
        ClaudeCodeInvocationError("not logged in"),
        ClaudeCodeInvocationError("rate limit exceeded"),
        NormalizationError("parse error"),
    ]:
        executor = _make_executor_raising(config, session_store, exc)
        req = VerifyTaskRequest(task="Verify /data/spec.md satisfies requirement A")
        response = await executor.verify_task(req)
        assert response.result["fallback_recommended"] is True, (
            f"fallback_recommended should be True for {type(exc).__name__}"
        )


# ---------------------------------------------------------------------------
# Regression: normal verification behavior preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_normal_pass_verdict_and_decision_preserved(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)

    assert response.ok is True
    assert response.result["verdict"] == "pass"
    assert response.result["decision"] == "verified"
    assert response.result["outcome_kind"] == "verified"


@pytest.mark.asyncio
async def test_normal_fail_closed_verdict_preserved(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies requirement B")
    response = await executor.verify_task(req)

    assert response.result["verdict"] == "fail_closed"
    assert response.result["outcome_kind"] == "not_verified"
    assert response.result["failure_class"] is None


@pytest.mark.asyncio
async def test_normal_result_backward_compat_fields_intact(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt satisfies expected content")
    response = await executor.verify_task(req)

    # Pre-v1.1.1 fields must still be present
    assert "findings" in response.result
    assert "contradictions" in response.result
    assert "missing_evidence" in response.result
    assert "restrictions" in response.result
    assert isinstance(response.result["findings"], list)


# ---------------------------------------------------------------------------
# Regression: restricted-mode boundary preserved
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_mode_execution_verb_still_blocked_before_backend(config, session_store):
    """Restricted-mode execution-verb mismatch must be rejected before the backend is called."""
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "claude_code"
    backend.execute = AsyncMock(
        return_value=NormalizedProviderResult(
            output_text=PASS_OUTPUT, turn_count=1, stop_reason="end_turn"
        )
    )
    config.mode = "apntalk_verification"
    executor = WorkflowExecutor(
        config=config,
        session_store=session_store,
        artifact_store=ArtifactStore(config, session_store.db),
        policy_engine=PolicyEngine(config),
        profile_registry=ProfileRegistry(),
        execution_backend=backend,
    )
    req = VerifyTaskRequest(task="Fix the authentication module and deploy it")
    response = await executor.verify_task(req)

    # Must be blocked — backend must not have been called
    backend.execute.assert_not_called()
    assert response.ok is False
    assert response.result["outcome_kind"] != "unavailable"
    assert response.result["verification_performed"] is False


@pytest.mark.asyncio
async def test_restricted_mode_unavailable_from_backend_still_returns_unavailable(
    config, session_store
):
    """If restricted mode request passes preflight but the backend fails, still unavailable."""
    executor = _make_executor_restricted_raising(
        config, session_store, ClaudeCodeUnavailableError("not installed")
    )
    req = VerifyTaskRequest(task="Verify /data/spec.md satisfies the APNTalk advisory requirement")
    response = await executor.verify_task(req)

    assert response.ok is False
    assert response.result["outcome_kind"] == "unavailable"
    assert response.result["failure_class"] == "backend_unavailable"
    assert response.result["verification_performed"] is False


# ---------------------------------------------------------------------------
# Regression: fail-closed on missing evidence still works
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fail_closed_missing_evidence_does_not_produce_unavailable(config, session_store):
    """Missing evidence with fail_closed=True is a policy block, NOT an availability failure."""
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify /nonexistent/path.txt satisfies requirement A",
        evidence_paths=["/nonexistent/does_not_exist.txt"],
        fail_closed=True,
    )
    response = await executor.verify_task(req)

    assert response.ok is False
    # Must NOT be an availability result
    assert response.result.get("outcome_kind") != "unavailable"
    assert response.result.get("failure_class") is None
