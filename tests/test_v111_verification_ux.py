"""Tests for v1.1.1 verification UX improvements.

Covers:
- VerificationReasonCode enum stability
- VerificationDecision / EvidenceSufficiency / ScopeAssessment / ProfileAlignment enums
- VerificationRequestShape and VerificationPreflightResult models
- analyze_request_shape heuristics
- run_preflight behavior in standard and restricted modes
- map_verdict_to_assessment determinism
- collect_operator_guidance content
- OPERATOR_GUIDANCE coverage
- Integration: agent_verify_task result fields
- Integration: restricted-mode hard mismatch blocks execution
- Integration: broad request classification in results
- Backward compatibility: existing result fields still present
- Preserved: restricted-mode fail-closed behavior unchanged
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_agent_mcp.backends.base import ExecutionBackend
from claude_agent_mcp.runtime.artifact_store import ArtifactStore
from claude_agent_mcp.runtime.policy_engine import PolicyEngine
from claude_agent_mcp.runtime.profile_registry import ProfileRegistry
from claude_agent_mcp.runtime.verification_preflight import (
    OPERATOR_GUIDANCE,
    analyze_request_shape,
    collect_operator_guidance,
    map_verdict_to_assessment,
    run_preflight,
)
from claude_agent_mcp.runtime.workflow_executor import WorkflowExecutor
from claude_agent_mcp.types import (
    EvidenceSufficiency,
    NormalizedProviderResult,
    ProfileAlignment,
    ProfileName,
    ScopeAssessment,
    VerificationDecision,
    VerificationPreflightResult,
    VerificationReasonCode,
    VerificationRequestShape,
    VerificationVerdict,
    VerifyTaskRequest,
    WorkflowName,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executor_with_output(config, session_store, output_text: str) -> WorkflowExecutor:
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "api"
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


def _make_executor_restricted(config, session_store, output_text: str = "") -> WorkflowExecutor:
    """Build an executor whose config reports apntalk_verification mode."""
    backend = MagicMock(spec=ExecutionBackend)
    backend.name = "api"
    backend.execute = AsyncMock(
        return_value=NormalizedProviderResult(
            output_text=output_text,
            turn_count=1,
            stop_reason="end_turn",
        )
    )
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

INSUFFICIENT_OUTPUT = """
VERDICT: insufficient_evidence
FINDINGS:
CONTRADICTIONS:
MISSING_EVIDENCE:
- No evidence files provided
RESTRICTIONS:
"""


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


def test_verification_reason_code_values_stable():
    expected = {
        "sufficient_evidence",
        "insufficient_evidence",
        "scope_too_broad",
        "out_of_profile_request",
        "restricted_mode_mismatch",
        "missing_required_context",
        "non_verifiable_request",
        "ambiguous_request",
    }
    actual = {c.value for c in VerificationReasonCode}
    assert actual == expected


def test_verification_decision_values_stable():
    assert {d.value for d in VerificationDecision} == {"verified", "not_verified", "inconclusive"}


def test_evidence_sufficiency_values_stable():
    assert {s.value for s in EvidenceSufficiency} == {"sufficient", "partial", "insufficient"}


def test_scope_assessment_values_stable():
    assert {s.value for s in ScopeAssessment} == {"narrow", "acceptable", "broad", "too_broad"}


def test_profile_alignment_values_stable():
    assert {a.value for a in ProfileAlignment} == {
        "in_profile", "out_of_profile", "restricted_mode_mismatch"
    }


def test_all_reason_codes_are_str_enum():
    for code in VerificationReasonCode:
        assert isinstance(code, str)


# ---------------------------------------------------------------------------
# VerificationRequestShape and VerificationPreflightResult models
# ---------------------------------------------------------------------------


def test_verification_request_shape_fields():
    shape = VerificationRequestShape(
        is_narrow=True,
        breadth_score=0,
        detected_targets=["/path/to/file.py"],
        detected_risks=[],
    )
    assert shape.is_narrow is True
    assert shape.breadth_score == 0
    assert shape.detected_targets == ["/path/to/file.py"]
    assert shape.detected_risks == []


def test_verification_preflight_result_fields():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.scope_too_broad],
        hints=["Narrow the scope."],
        normalized_scope_summary="some task",
    )
    assert pf.ok is True
    assert pf.lint_codes == [VerificationReasonCode.scope_too_broad]
    assert pf.hints == ["Narrow the scope."]
    assert pf.normalized_scope_summary == "some task"


def test_verification_preflight_result_defaults():
    pf = VerificationPreflightResult(ok=True)
    assert pf.lint_codes == []
    assert pf.hints == []
    assert pf.normalized_scope_summary == ""


# ---------------------------------------------------------------------------
# analyze_request_shape — heuristics
# ---------------------------------------------------------------------------


def test_narrow_request_with_file_path():
    req = VerifyTaskRequest(task="Verify that /path/to/report.py satisfies the APNTalk profile")
    shape = analyze_request_shape(req)
    assert shape.is_narrow is True
    assert shape.breadth_score <= 1
    assert any("report.py" in t or "/path" in t for t in shape.detected_targets)


def test_narrow_request_with_evidence():
    req = VerifyTaskRequest(
        task="Verify the claim",
        evidence_paths=["/evidence/report.txt"],
    )
    shape = analyze_request_shape(req)
    assert shape.is_narrow is True
    assert "/evidence/report.txt" in shape.detected_targets


def test_broad_request_with_everything_language():
    req = VerifyTaskRequest(task="Check everything in the system")
    shape = analyze_request_shape(req)
    assert shape.is_narrow is False
    assert shape.breadth_score >= 2
    assert any("broad scope language" in r for r in shape.detected_risks)


def test_broad_request_with_no_target_no_evidence():
    req = VerifyTaskRequest(task="Is it correct?")
    shape = analyze_request_shape(req)
    assert "no named target artifact or evidence path detected" in shape.detected_risks


def test_execution_verb_increases_breadth():
    req = VerifyTaskRequest(task="Fix the authentication module")
    shape = analyze_request_shape(req)
    assert any("execution-oriented language" in r for r in shape.detected_risks)
    assert shape.breadth_score >= 1


def test_very_broad_request_hits_high_score():
    req = VerifyTaskRequest(task="Review everything and fix the whole system")
    shape = analyze_request_shape(req)
    assert shape.breadth_score >= 3
    assert shape.is_narrow is False


def test_scope_field_adds_context():
    req = VerifyTaskRequest(task="Verify the claim", scope="src/module.py")
    shape = analyze_request_shape(req)
    # scope field provides context
    assert any("module.py" in t for t in shape.detected_targets)


# ---------------------------------------------------------------------------
# run_preflight — standard mode
# ---------------------------------------------------------------------------


def test_preflight_ok_for_narrow_request_standard():
    req = VerifyTaskRequest(
        task="Verify that /data/report.txt satisfies the expected schema",
        evidence_paths=["/data/report.txt"],
    )
    result = run_preflight(req, is_restricted_mode=False)
    assert result.ok is True


def test_preflight_ok_true_always_in_standard_mode_even_with_broad_request():
    req = VerifyTaskRequest(task="Review everything and make sure it works")
    result = run_preflight(req, is_restricted_mode=False)
    assert result.ok is True  # warnings only in standard mode


def test_preflight_scope_too_broad_code_for_very_broad_request():
    req = VerifyTaskRequest(task="Check everything in the whole system")
    result = run_preflight(req, is_restricted_mode=False)
    assert VerificationReasonCode.scope_too_broad in result.lint_codes


def test_preflight_ambiguous_code_for_moderately_broad_request():
    req = VerifyTaskRequest(task="Check if everything is fine")
    result = run_preflight(req, is_restricted_mode=False)
    # breadth_score should be >= 2 due to broad language + no target
    assert (
        VerificationReasonCode.scope_too_broad in result.lint_codes
        or VerificationReasonCode.ambiguous_request in result.lint_codes
    )


def test_preflight_out_of_profile_code_for_execution_request_standard():
    req = VerifyTaskRequest(task="Fix the authentication module")
    result = run_preflight(req, is_restricted_mode=False)
    assert VerificationReasonCode.out_of_profile_request in result.lint_codes


def test_preflight_missing_context_code_for_no_target():
    req = VerifyTaskRequest(task="Is it correct?")
    result = run_preflight(req, is_restricted_mode=False)
    assert VerificationReasonCode.missing_required_context in result.lint_codes


def test_preflight_hints_present_for_scope_too_broad():
    req = VerifyTaskRequest(task="Check everything in the whole system")
    result = run_preflight(req, is_restricted_mode=False)
    assert len(result.hints) > 0


def test_preflight_normalized_scope_summary_not_empty():
    req = VerifyTaskRequest(task="Verify the report")
    result = run_preflight(req, is_restricted_mode=False)
    assert len(result.normalized_scope_summary) > 0


def test_preflight_clean_request_no_lint_codes():
    req = VerifyTaskRequest(
        task="Verify whether /data/output.json matches the expected schema",
        evidence_paths=["/data/output.json"],
    )
    result = run_preflight(req, is_restricted_mode=False)
    assert result.ok is True
    assert result.lint_codes == []


# ---------------------------------------------------------------------------
# run_preflight — restricted APNTalk mode
# ---------------------------------------------------------------------------


def test_preflight_restricted_mode_ok_for_narrow_request():
    req = VerifyTaskRequest(
        task="Verify whether the exposed tool list is exactly the admitted pair",
    )
    result = run_preflight(req, is_restricted_mode=True)
    assert result.ok is True


def test_preflight_restricted_mode_blocked_for_execution_verb():
    req = VerifyTaskRequest(task="Fix the authentication module")
    result = run_preflight(req, is_restricted_mode=True)
    assert result.ok is False
    assert VerificationReasonCode.restricted_mode_mismatch in result.lint_codes


def test_preflight_restricted_mode_mismatch_not_out_of_profile():
    req = VerifyTaskRequest(task="Create a new config file")
    result = run_preflight(req, is_restricted_mode=True)
    assert VerificationReasonCode.restricted_mode_mismatch in result.lint_codes
    assert VerificationReasonCode.out_of_profile_request not in result.lint_codes


def test_preflight_restricted_mode_blocked_has_hints():
    req = VerifyTaskRequest(task="Write a test file")
    result = run_preflight(req, is_restricted_mode=True)
    assert result.ok is False
    assert len(result.hints) > 0


def test_preflight_restricted_mode_broad_but_no_exec_verbs_is_ok():
    # A broad but non-execution request in restricted mode should still be ok
    # (preflight is advisory for scope; only execution verbs are hard blockers)
    req = VerifyTaskRequest(task="Is the system correct?")
    result = run_preflight(req, is_restricted_mode=True)
    assert result.ok is True  # no execution verbs → not a hard block


# ---------------------------------------------------------------------------
# map_verdict_to_assessment — determinism
# ---------------------------------------------------------------------------


def _empty_preflight() -> VerificationPreflightResult:
    return VerificationPreflightResult(ok=True, lint_codes=[], hints=[])


def test_pass_verdict_maps_to_verified():
    pf = _empty_preflight()
    decision, primary, codes, suff, scope, align = map_verdict_to_assessment(
        VerificationVerdict.pass_, pf
    )
    assert decision == VerificationDecision.verified
    assert suff == EvidenceSufficiency.sufficient
    assert VerificationReasonCode.sufficient_evidence in codes


def test_pass_with_restrictions_maps_to_verified_partial():
    pf = _empty_preflight()
    decision, primary, codes, suff, scope, align = map_verdict_to_assessment(
        VerificationVerdict.pass_with_restrictions, pf
    )
    assert decision == VerificationDecision.verified
    assert suff == EvidenceSufficiency.partial


def test_fail_closed_maps_to_not_verified():
    pf = _empty_preflight()
    decision, primary, codes, suff, scope, align = map_verdict_to_assessment(
        VerificationVerdict.fail_closed, pf
    )
    assert decision == VerificationDecision.not_verified
    assert suff == EvidenceSufficiency.insufficient


def test_insufficient_evidence_maps_to_inconclusive():
    pf = _empty_preflight()
    decision, primary, codes, suff, scope, align = map_verdict_to_assessment(
        VerificationVerdict.insufficient_evidence, pf
    )
    assert decision == VerificationDecision.inconclusive
    assert suff == EvidenceSufficiency.insufficient


def test_narrow_request_maps_to_narrow_scope():
    pf = _empty_preflight()
    _, _, _, _, scope, _ = map_verdict_to_assessment(VerificationVerdict.pass_, pf)
    assert scope == ScopeAssessment.narrow


def test_scope_too_broad_in_preflight_maps_to_too_broad_scope():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.scope_too_broad],
        hints=[],
    )
    _, _, _, _, scope, _ = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert scope == ScopeAssessment.too_broad


def test_ambiguous_in_preflight_maps_to_broad_scope():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.ambiguous_request],
        hints=[],
    )
    _, _, _, _, scope, _ = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert scope == ScopeAssessment.broad


def test_in_profile_when_no_preflight_codes():
    pf = _empty_preflight()
    _, _, _, _, _, align = map_verdict_to_assessment(VerificationVerdict.pass_, pf)
    assert align == ProfileAlignment.in_profile


def test_out_of_profile_when_preflight_has_out_of_profile():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.out_of_profile_request],
        hints=[],
    )
    _, _, _, _, _, align = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert align == ProfileAlignment.out_of_profile


def test_restricted_mode_mismatch_alignment():
    pf = VerificationPreflightResult(
        ok=False,
        lint_codes=[VerificationReasonCode.restricted_mode_mismatch],
        hints=[],
    )
    _, _, _, _, _, align = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert align == ProfileAlignment.restricted_mode_mismatch


def test_primary_reason_from_preflight_when_preflight_codes_present():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.scope_too_broad, VerificationReasonCode.missing_required_context],
        hints=[],
    )
    _, primary, _, _, _, _ = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert primary == VerificationReasonCode.scope_too_broad


def test_primary_reason_from_verdict_when_no_preflight_codes():
    pf = _empty_preflight()
    _, primary, _, _, _, _ = map_verdict_to_assessment(VerificationVerdict.pass_, pf)
    assert primary == VerificationReasonCode.sufficient_evidence


def test_reason_codes_include_both_preflight_and_verdict():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.scope_too_broad],
        hints=[],
    )
    _, _, codes, _, _, _ = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert VerificationReasonCode.scope_too_broad in codes
    assert VerificationReasonCode.insufficient_evidence in codes


def test_map_verdict_is_deterministic():
    pf = VerificationPreflightResult(
        ok=True,
        lint_codes=[VerificationReasonCode.ambiguous_request],
        hints=[],
    )
    result1 = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    result2 = map_verdict_to_assessment(VerificationVerdict.fail_closed, pf)
    assert result1 == result2


# ---------------------------------------------------------------------------
# collect_operator_guidance
# ---------------------------------------------------------------------------


def test_guidance_returned_for_insufficient_evidence():
    guidance = collect_operator_guidance([VerificationReasonCode.insufficient_evidence])
    assert len(guidance) > 0
    assert any("artifact" in g.lower() or "evidence" in g.lower() for g in guidance)


def test_guidance_returned_for_scope_too_broad():
    guidance = collect_operator_guidance([VerificationReasonCode.scope_too_broad])
    assert len(guidance) > 0
    assert any("narrow" in g.lower() for g in guidance)


def test_guidance_returned_for_out_of_profile():
    guidance = collect_operator_guidance([VerificationReasonCode.out_of_profile_request])
    assert len(guidance) > 0


def test_guidance_returned_for_restricted_mode_mismatch():
    guidance = collect_operator_guidance([VerificationReasonCode.restricted_mode_mismatch])
    assert len(guidance) > 0
    assert any("apntalk" in g.lower() or "advisory" in g.lower() or "bounded" in g.lower() for g in guidance)


def test_guidance_returned_for_missing_context():
    guidance = collect_operator_guidance([VerificationReasonCode.missing_required_context])
    assert len(guidance) > 0


def test_guidance_deduplication():
    codes = [VerificationReasonCode.scope_too_broad, VerificationReasonCode.scope_too_broad]
    guidance = collect_operator_guidance(codes)
    # Should not have duplicates
    assert len(guidance) == len(set(guidance))


def test_guidance_aggregates_multiple_codes():
    codes = [
        VerificationReasonCode.scope_too_broad,
        VerificationReasonCode.insufficient_evidence,
    ]
    guidance = collect_operator_guidance(codes)
    assert len(guidance) >= 2


# ---------------------------------------------------------------------------
# OPERATOR_GUIDANCE coverage
# ---------------------------------------------------------------------------


def test_operator_guidance_has_entry_for_all_major_reason_codes():
    major_codes = [
        VerificationReasonCode.insufficient_evidence,
        VerificationReasonCode.scope_too_broad,
        VerificationReasonCode.out_of_profile_request,
        VerificationReasonCode.restricted_mode_mismatch,
        VerificationReasonCode.missing_required_context,
        VerificationReasonCode.ambiguous_request,
        VerificationReasonCode.sufficient_evidence,
    ]
    for code in major_codes:
        assert code in OPERATOR_GUIDANCE, f"Missing OPERATOR_GUIDANCE entry for {code}"
        assert len(OPERATOR_GUIDANCE[code]) > 0, f"Empty guidance for {code}"


def test_all_guidance_strings_are_non_empty():
    for code, strings in OPERATOR_GUIDANCE.items():
        for s in strings:
            assert len(s.strip()) > 0, f"Empty guidance string for {code}"


# ---------------------------------------------------------------------------
# Integration: agent_verify_task richer result fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_task_result_has_decision_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt", evidence_paths=[])
    response = await executor.verify_task(req)
    assert "decision" in response.result


@pytest.mark.asyncio
async def test_verify_task_result_has_primary_reason_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "primary_reason" in response.result


@pytest.mark.asyncio
async def test_verify_task_result_has_reason_codes_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "reason_codes" in response.result
    assert isinstance(response.result["reason_codes"], list)


@pytest.mark.asyncio
async def test_verify_task_result_has_operator_guidance_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "operator_guidance" in response.result
    assert isinstance(response.result["operator_guidance"], list)


@pytest.mark.asyncio
async def test_verify_task_result_has_evidence_sufficiency_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "evidence_sufficiency" in response.result


@pytest.mark.asyncio
async def test_verify_task_result_has_scope_assessment_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "scope_assessment" in response.result


@pytest.mark.asyncio
async def test_verify_task_result_has_profile_alignment_field(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "profile_alignment" in response.result


@pytest.mark.asyncio
async def test_verify_task_pass_yields_verified_decision(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert response.result["decision"] == VerificationDecision.verified.value


@pytest.mark.asyncio
async def test_verify_task_fail_closed_yields_not_verified_decision(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt", fail_closed=True)
    response = await executor.verify_task(req)
    assert response.result["decision"] == VerificationDecision.not_verified.value


@pytest.mark.asyncio
async def test_verify_task_insufficient_yields_inconclusive_decision(config, session_store):
    executor = _make_executor_with_output(config, session_store, INSUFFICIENT_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt", fail_closed=False)
    response = await executor.verify_task(req)
    assert response.result["decision"] == VerificationDecision.inconclusive.value


@pytest.mark.asyncio
async def test_verify_task_pass_primary_reason_is_sufficient_evidence(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert response.result["primary_reason"] == VerificationReasonCode.sufficient_evidence.value


@pytest.mark.asyncio
async def test_verify_task_fail_has_operator_guidance(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt", fail_closed=True)
    response = await executor.verify_task(req)
    assert len(response.result["operator_guidance"]) > 0


@pytest.mark.asyncio
async def test_verify_task_broad_request_classified_correctly(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Check everything in the whole system")
    response = await executor.verify_task(req)
    result = response.result
    assert result["scope_assessment"] in {
        ScopeAssessment.too_broad.value,
        ScopeAssessment.broad.value,
    }
    assert (
        VerificationReasonCode.scope_too_broad.value in result["reason_codes"]
        or VerificationReasonCode.ambiguous_request.value in result["reason_codes"]
    )


@pytest.mark.asyncio
async def test_verify_task_execution_request_standard_mode_flagged(config, session_store):
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Fix the authentication module")
    response = await executor.verify_task(req)
    result = response.result
    assert result["profile_alignment"] == ProfileAlignment.out_of_profile.value
    assert VerificationReasonCode.out_of_profile_request.value in result["reason_codes"]


# ---------------------------------------------------------------------------
# Integration: backward compatibility
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_verify_task_backward_compat_verdict_still_present(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert "verdict" in response.result


@pytest.mark.asyncio
async def test_verify_task_backward_compat_findings_still_present(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    for key in ("findings", "contradictions", "missing_evidence", "restrictions"):
        assert key in response.result, f"Missing backward-compat field: {key}"


@pytest.mark.asyncio
async def test_verify_task_profile_is_still_verification(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert response.profile == ProfileName.verification


@pytest.mark.asyncio
async def test_verify_task_workflow_is_still_verify_task(config, session_store):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(task="Verify /data/report.txt")
    response = await executor.verify_task(req)
    assert response.workflow == WorkflowName.verify_task


# ---------------------------------------------------------------------------
# Integration: restricted-mode hard mismatch blocks execution
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_mode_execution_verb_blocks_before_session(config, session_store):
    executor = _make_executor_restricted(config, session_store)
    req = VerifyTaskRequest(task="Fix the authentication module")
    response = await executor.verify_task(req)
    assert response.ok is False
    assert response.session_id == ""  # no session was created


@pytest.mark.asyncio
async def test_restricted_mode_blocked_result_has_restricted_mode_mismatch(config, session_store):
    executor = _make_executor_restricted(config, session_store)
    req = VerifyTaskRequest(task="Create a new config file")
    response = await executor.verify_task(req)
    assert response.ok is False
    result = response.result
    assert result["primary_reason"] == VerificationReasonCode.restricted_mode_mismatch.value
    assert VerificationReasonCode.restricted_mode_mismatch.value in result["reason_codes"]


@pytest.mark.asyncio
async def test_restricted_mode_blocked_result_has_operator_guidance(config, session_store):
    executor = _make_executor_restricted(config, session_store)
    req = VerifyTaskRequest(task="Write a new test")
    response = await executor.verify_task(req)
    assert len(response.result["operator_guidance"]) > 0
    assert any("apntalk" in g.lower() or "advisory" in g.lower() or "bounded" in g.lower()
               for g in response.result["operator_guidance"])


@pytest.mark.asyncio
async def test_restricted_mode_blocked_result_has_required_fields(config, session_store):
    executor = _make_executor_restricted(config, session_store)
    req = VerifyTaskRequest(task="Delete the log files")
    response = await executor.verify_task(req)
    for key in (
        "verdict", "decision", "primary_reason", "reason_codes",
        "operator_guidance", "evidence_sufficiency", "scope_assessment", "profile_alignment",
        "findings", "contradictions", "missing_evidence", "restrictions",
    ):
        assert key in response.result, f"Missing field in blocked response: {key}"


@pytest.mark.asyncio
async def test_restricted_mode_valid_advisory_request_proceeds(config, session_store):
    executor = _make_executor_restricted(config, session_store, output_text=PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify whether the exposed tool list is exactly the admitted pair",
    )
    response = await executor.verify_task(req)
    assert response.ok is True
    assert response.session_id != ""  # session was created


# ---------------------------------------------------------------------------
# Integration: early fail_closed response has richer fields
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_early_fail_closed_response_has_richer_fields(config, session_store, tmp_path):
    executor = _make_executor_with_output(config, session_store, PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify with nonexistent evidence",
        evidence_paths=[str(tmp_path / "does_not_exist.txt")],
        fail_closed=True,
    )
    response = await executor.verify_task(req)
    assert response.ok is False
    result = response.result
    for key in (
        "verdict", "decision", "primary_reason", "reason_codes",
        "operator_guidance", "evidence_sufficiency", "scope_assessment", "profile_alignment",
    ):
        assert key in result, f"Missing field in early fail_closed response: {key}"


# ---------------------------------------------------------------------------
# Preserved: restricted-mode fail-closed behavior unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_restricted_mode_boundary_preserved_backend_mock(config, session_store):
    """Passing a valid advisory request in restricted mode still executes correctly."""
    executor = _make_executor_restricted(config, session_store, output_text=PASS_OUTPUT)
    req = VerifyTaskRequest(
        task="Verify whether the restriction contract proof fields are present",
    )
    response = await executor.verify_task(req)
    assert response.ok is True
    assert response.result["verdict"] == VerificationVerdict.pass_.value


@pytest.mark.asyncio
async def test_standard_mode_execution_request_proceeds_with_warning(config, session_store):
    """In standard mode, an execution-flavored request still executes (advisory warning only)."""
    executor = _make_executor_with_output(config, session_store, FAIL_OUTPUT)
    req = VerifyTaskRequest(task="Fix the authentication flow")
    response = await executor.verify_task(req)
    # Should proceed (ok=True from execution, even if verdict is fail_closed)
    assert response.ok is True
    assert response.session_id != ""
    # And the result should have the out-of-profile flag set
    assert response.result["profile_alignment"] == ProfileAlignment.out_of_profile.value
