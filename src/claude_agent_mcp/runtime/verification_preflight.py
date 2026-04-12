"""Verification preflight and request-shape analysis (v1.1.1).

This module provides deterministic, heuristic-based analysis of
verification requests before deeper execution. It does not execute
agents, query external systems, or widen the tool surface.

Public API:
    analyze_request_shape   — breadth/specificity heuristics
    run_preflight           — lint pass for request quality and profile alignment
    map_verdict_to_assessment — maps VerificationVerdict to richer result fields
    collect_operator_guidance — returns actionable hint strings for reason codes
    OPERATOR_GUIDANCE       — mapping from VerificationReasonCode to guidance text
"""

from __future__ import annotations

import re

from claude_agent_mcp.types import (
    EvidenceSufficiency,
    ProfileAlignment,
    ScopeAssessment,
    VerificationDecision,
    VerificationPreflightResult,
    VerificationReasonCode,
    VerificationRequestShape,
    VerificationVerdict,
    VerifyTaskRequest,
)

# ---------------------------------------------------------------------------
# Heuristic signals
# ---------------------------------------------------------------------------

# Execution-oriented verbs that indicate an execution (not verification) request.
_EXECUTION_VERBS: frozenset[str] = frozenset({
    "create", "write", "modify", "update", "fix", "implement",
    "delete", "remove", "add", "install", "run", "execute",
    "deploy", "build", "compile", "generate", "edit", "patch",
    "refactor", "rewrite",
})

# Broad/vague language patterns that indicate an overly wide scope.
_BROAD_LANGUAGE_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"\beverything\b", re.IGNORECASE),
    re.compile(r"\bthe whole\b", re.IGNORECASE),
    re.compile(r"\bthe entire\b", re.IGNORECASE),
    re.compile(r"\ball of it\b", re.IGNORECASE),
    re.compile(r"\bthe system\b", re.IGNORECASE),
    re.compile(r"\ball files\b", re.IGNORECASE),
    re.compile(r"\bthe whole repo(?:sitory)?\b", re.IGNORECASE),
    re.compile(r"\bthe entire codebase\b", re.IGNORECASE),
    re.compile(r"\breview everything\b", re.IGNORECASE),
    re.compile(r"\bcheck everything\b", re.IGNORECASE),
    re.compile(r"\bvalidate everything\b", re.IGNORECASE),
)

# File or path reference (catches .ext patterns and path separators)
_FILE_PATH_PATTERN = re.compile(
    r"(?:[./][^\s,;\"']+|\b\w[\w\-]*\.\w{1,6}\b)", re.IGNORECASE
)

# Quoted claim (at least 4 chars inside quotes)
_CLAIM_PATTERN = re.compile(r'"[^"]{4,}"', re.IGNORECASE)

# Multiple verification objectives joined by "and" or "also"
_MULTIPLE_ASKS_PATTERN = re.compile(
    r"\b(?:verify|check|validate|confirm|ensure|test)\b.{5,60}"
    r"\b(?:and|also)\b.{5,60}"
    r"\b(?:verify|check|validate|confirm|ensure|test)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Operator guidance strings indexed by reason code
# ---------------------------------------------------------------------------

OPERATOR_GUIDANCE: dict[VerificationReasonCode, list[str]] = {
    VerificationReasonCode.sufficient_evidence: [
        "Evidence supports the claim. Review findings for any noted restrictions.",
    ],
    VerificationReasonCode.insufficient_evidence: [
        "Provide the target artifact, expected outcome, or a bounded evidence source.",
        "Narrow the scope to a specific file, function, or observable claim.",
    ],
    VerificationReasonCode.scope_too_broad: [
        "Narrow the request to one artifact, one claim, or one verification objective.",
        'Example: "Verify whether <specific file> satisfies <specific condition>".',
    ],
    VerificationReasonCode.out_of_profile_request: [
        "This request exceeds the active verification profile.",
        "Handle execution-oriented requests outside restricted verification mode.",
    ],
    VerificationReasonCode.restricted_mode_mismatch: [
        "The active APNTalk verification mode only permits bounded advisory verification tasks.",
        "Reframe the request as a specific, observable claim to verify against existing evidence.",
    ],
    VerificationReasonCode.missing_required_context: [
        "Specify the target artifact, evidence path, or expected state to verify against.",
        "Include at least one concrete named subject for the verification claim.",
    ],
    VerificationReasonCode.non_verifiable_request: [
        "This request cannot be answered through passive evidence review.",
        "Restrict the request to observable properties of existing artifacts.",
    ],
    VerificationReasonCode.ambiguous_request: [
        "Clarify the verification objective with a single, specific claim.",
        "Specify what passing looks like and which artifact is the subject.",
    ],
}


# ---------------------------------------------------------------------------
# Request-shape analysis
# ---------------------------------------------------------------------------


def analyze_request_shape(req: VerifyTaskRequest) -> VerificationRequestShape:
    """Deterministic heuristic analysis of request breadth and specificity.

    Each of four signals contributes 1 point to breadth_score:
      1. Multiple verification objectives in one request
      2. Broad/vague language (e.g., "everything", "the whole system")
      3. Execution-oriented verbs (e.g., "fix", "create", "write")
      4. No named target artifact and no evidence_paths provided

    breadth_score 0–1 → is_narrow=True
    breadth_score 2+  → is_narrow=False
    """
    task = req.task
    scope = req.scope or ""
    combined = f"{task} {scope}".strip()
    combined_lower = combined.lower()
    combined_words = re.findall(r"\b\w+\b", combined_lower)

    breadth_score = 0
    detected_risks: list[str] = []

    # Signal 1: multiple verification objectives
    if _MULTIPLE_ASKS_PATTERN.search(combined):
        breadth_score += 1
        detected_risks.append("multiple verification objectives detected")

    # Signal 2: broad/vague language
    for pattern in _BROAD_LANGUAGE_PATTERNS:
        if pattern.search(combined):
            breadth_score += 1
            detected_risks.append("broad scope language detected")
            break

    # Signal 3: execution-oriented verbs
    exec_verbs_found = [v for v in combined_words if v in _EXECUTION_VERBS]
    if exec_verbs_found:
        breadth_score += 1
        detected_risks.append(
            f"execution-oriented language detected: {exec_verbs_found[:3]}"
        )

    # Detect named targets
    detected_targets: list[str] = []
    path_matches = _FILE_PATH_PATTERN.findall(combined)
    detected_targets.extend(m for m in path_matches if len(m) > 2)
    claim_matches = _CLAIM_PATTERN.findall(combined)
    detected_targets.extend(claim_matches)
    if req.evidence_paths:
        detected_targets.extend(req.evidence_paths[:5])

    # Deduplicate while preserving order
    seen: set[str] = set()
    deduped_targets: list[str] = []
    for t in detected_targets:
        if t not in seen:
            seen.add(t)
            deduped_targets.append(t)

    # Signal 4: no named target artifact and no evidence paths
    if not deduped_targets:
        breadth_score += 1
        detected_risks.append("no named target artifact or evidence path detected")

    return VerificationRequestShape(
        is_narrow=breadth_score <= 1,
        breadth_score=breadth_score,
        detected_targets=deduped_targets[:8],
        detected_risks=detected_risks,
    )


# ---------------------------------------------------------------------------
# Preflight lint pass
# ---------------------------------------------------------------------------


def run_preflight(
    req: VerifyTaskRequest, *, is_restricted_mode: bool = False
) -> VerificationPreflightResult:
    """Lightweight preflight lint pass for a verification request.

    Evaluates request shape and profile alignment before deeper verification.

    In standard mode:
      - Produces lint codes and hints; ok is always True (warnings only).

    In restricted APNTalk verification mode:
      - restricted_mode_mismatch is a hard blocker (ok=False).
      - All other codes are warnings that accompany a blocked result.

    Returns a VerificationPreflightResult.
    """
    shape = analyze_request_shape(req)
    lint_codes: list[VerificationReasonCode] = []
    hints: list[str] = []

    # Check for execution-oriented verbs
    task_lower = req.task.lower()
    task_words = re.findall(r"\b\w+\b", task_lower)
    exec_verbs_found = [v for v in task_words if v in _EXECUTION_VERBS]

    if exec_verbs_found:
        if is_restricted_mode:
            _add_code(lint_codes, hints, VerificationReasonCode.restricted_mode_mismatch)
        else:
            _add_code(lint_codes, hints, VerificationReasonCode.out_of_profile_request)

    # Check for scope breadth
    # breadth_score 3+: clearly too broad (explicit code)
    # breadth_score 2: broad with multiple signals (scope_too_broad as well)
    # breadth_score 1: one weak signal (ambiguous only)
    if shape.breadth_score >= 2:
        _add_code(lint_codes, hints, VerificationReasonCode.scope_too_broad)
    elif shape.breadth_score == 1 and not shape.detected_targets:
        _add_code(lint_codes, hints, VerificationReasonCode.ambiguous_request)

    # Check for missing context
    if "no named target artifact or evidence path detected" in shape.detected_risks:
        _add_code(lint_codes, hints, VerificationReasonCode.missing_required_context)

    # Normalize scope summary
    suffix = ""
    if shape.detected_targets:
        first_two = ", ".join(shape.detected_targets[:2])
        suffix = f" — targeting: {first_two}"
    task_snippet = req.task[:80] + ("..." if len(req.task) > 80 else "")
    normalized_scope_summary = f"{task_snippet}{suffix}"

    # ok=False only when restricted mode has a hard mismatch
    ok = not (
        is_restricted_mode
        and VerificationReasonCode.restricted_mode_mismatch in lint_codes
    )

    return VerificationPreflightResult(
        ok=ok,
        lint_codes=lint_codes,
        hints=hints,
        normalized_scope_summary=normalized_scope_summary,
    )


# ---------------------------------------------------------------------------
# Verdict → assessment mapping
# ---------------------------------------------------------------------------


def map_verdict_to_assessment(
    verdict: VerificationVerdict,
    preflight: VerificationPreflightResult,
) -> tuple[
    VerificationDecision,
    VerificationReasonCode,
    list[VerificationReasonCode],
    EvidenceSufficiency,
    ScopeAssessment,
    ProfileAlignment,
]:
    """Map a verification verdict and preflight result to structured assessment fields.

    Returns:
        (decision, primary_reason, reason_codes,
         evidence_sufficiency, scope_assessment, profile_alignment)

    Preflight codes take precedence over verdict-derived codes for primary_reason,
    because policy/request-quality failures are more operator-actionable than
    evidence-level failures.
    """
    # Scope assessment from preflight
    if VerificationReasonCode.scope_too_broad in preflight.lint_codes:
        scope_assessment = ScopeAssessment.too_broad
    elif VerificationReasonCode.ambiguous_request in preflight.lint_codes:
        scope_assessment = ScopeAssessment.broad
    elif preflight.lint_codes:
        scope_assessment = ScopeAssessment.acceptable
    else:
        scope_assessment = ScopeAssessment.narrow

    # Profile alignment from preflight
    if VerificationReasonCode.restricted_mode_mismatch in preflight.lint_codes:
        profile_alignment = ProfileAlignment.restricted_mode_mismatch
    elif VerificationReasonCode.out_of_profile_request in preflight.lint_codes:
        profile_alignment = ProfileAlignment.out_of_profile
    else:
        profile_alignment = ProfileAlignment.in_profile

    # Verdict → (decision, evidence reason, evidence sufficiency)
    _verdict_map: dict[
        VerificationVerdict,
        tuple[VerificationDecision, VerificationReasonCode, EvidenceSufficiency],
    ] = {
        VerificationVerdict.pass_: (
            VerificationDecision.verified,
            VerificationReasonCode.sufficient_evidence,
            EvidenceSufficiency.sufficient,
        ),
        VerificationVerdict.pass_with_restrictions: (
            VerificationDecision.verified,
            VerificationReasonCode.sufficient_evidence,
            EvidenceSufficiency.partial,
        ),
        VerificationVerdict.fail_closed: (
            VerificationDecision.not_verified,
            VerificationReasonCode.insufficient_evidence,
            EvidenceSufficiency.insufficient,
        ),
        VerificationVerdict.insufficient_evidence: (
            VerificationDecision.inconclusive,
            VerificationReasonCode.insufficient_evidence,
            EvidenceSufficiency.insufficient,
        ),
    }

    decision, evidence_reason, evidence_sufficiency = _verdict_map[verdict]

    # Build ordered reason_codes list (policy/request-quality codes first)
    reason_codes: list[VerificationReasonCode] = list(preflight.lint_codes)
    if evidence_reason not in reason_codes:
        reason_codes.append(evidence_reason)

    # Primary reason: preflight codes take precedence
    primary_reason = preflight.lint_codes[0] if preflight.lint_codes else evidence_reason

    return (
        decision,
        primary_reason,
        reason_codes,
        evidence_sufficiency,
        scope_assessment,
        profile_alignment,
    )


# ---------------------------------------------------------------------------
# Operator guidance aggregation
# ---------------------------------------------------------------------------


def collect_operator_guidance(
    reason_codes: list[VerificationReasonCode],
) -> list[str]:
    """Return deduplicated actionable guidance strings for the given reason codes.

    Guidance strings are returned in reason_code order, deduplicated.
    """
    seen: set[str] = set()
    result: list[str] = []
    for code in reason_codes:
        for hint in OPERATOR_GUIDANCE.get(code, []):
            if hint not in seen:
                seen.add(hint)
                result.append(hint)
    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _add_code(
    lint_codes: list[VerificationReasonCode],
    hints: list[str],
    code: VerificationReasonCode,
) -> None:
    """Add a code and its guidance to the lint lists (no-op if already present)."""
    if code not in lint_codes:
        lint_codes.append(code)
        hints.extend(OPERATOR_GUIDANCE.get(code, []))
