"""Claude Code backend failure classification for verification workflows (v1.1.2).

Converts raw subprocess / provider exceptions and empty-response conditions into
stable, machine-readable operational failure classifications.

These classifications are distinct from verification-domain reason codes
(insufficient_evidence, scope_too_broad, etc.).  Operational failures indicate
that verification could not be performed at all, not that verification ran and
produced a negative or inconclusive outcome.

Public API:
    classify_backend_failure(exc)    — classify a raised backend exception
    classify_empty_response()        — classify an empty (but non-exception) response
    FailureClassificationResult      — classification result dataclass
    RETRYABLE_CLASSES                — set of VerificationFailureClass values that are retryable
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_agent_mcp.errors import (
    ClaudeCodeInvocationError,
    ClaudeCodeUnavailableError,
    NormalizationError,
)
from claude_agent_mcp.types import VerificationFailureClass, VerificationFailureCode

# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureClassificationResult:
    """Stable, machine-readable classification of a backend / provider failure.

    Produced by classify_backend_failure() or classify_empty_response().
    Consumed by WorkflowExecutor to build the unavailable result payload.
    """

    failure_class: VerificationFailureClass
    failure_code: VerificationFailureCode
    retryable: bool
    fallback_recommended: bool
    summary: str


# ---------------------------------------------------------------------------
# Retryability policy
# ---------------------------------------------------------------------------

# Failure classes where a retry (after a delay) may succeed.
RETRYABLE_CLASSES: frozenset[VerificationFailureClass] = frozenset({
    VerificationFailureClass.backend_limit_reached,
    VerificationFailureClass.backend_timeout,
    VerificationFailureClass.backend_unusable_response,
})

# Failure classes where external fallback to another verifier is appropriate.
FALLBACK_RECOMMENDED_CLASSES: frozenset[VerificationFailureClass] = frozenset(
    VerificationFailureClass
)  # All unavailable outcomes recommend fallback.

# ---------------------------------------------------------------------------
# Signal patterns for stderr / message matching
# ---------------------------------------------------------------------------

# Tokens that indicate a usage-quota / rate-limit failure in stderr or message text.
_LIMIT_TOKENS: tuple[str, ...] = (
    "limit",
    "quota",
    "rate limit",
    "usage limit",
    "exceeded",
    "too many requests",
    "cap reached",
    "daily limit",
    "monthly limit",
    "usage cap",
)

# Tokens that indicate an authentication / login failure.
_AUTH_TOKENS: tuple[str, ...] = (
    "not logged in",
    "not authenticated",
    "login required",
    "please log in",
    "please login",
    "sign in",
    "unauthenticated",
    "credentials",
    "auth",
    "token expired",
    "session expired",
    "claude login",
)

# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------


def classify_backend_failure(exc: BaseException) -> FailureClassificationResult:
    """Classify a backend exception into a stable FailureClassificationResult.

    Handles:
    - ClaudeCodeUnavailableError  → backend_unavailable / claude_code_not_installed
    - ClaudeCodeInvocationError   → timeout, auth, limit, or process error
    - NormalizationError          → backend_unusable_response / unparseable

    Falls back to backend_invocation_error / claude_code_process_error for any
    other exception type.
    """
    msg = str(exc).lower()

    if isinstance(exc, ClaudeCodeUnavailableError):
        return FailureClassificationResult(
            failure_class=VerificationFailureClass.backend_unavailable,
            failure_code=VerificationFailureCode.claude_code_not_installed,
            retryable=False,
            fallback_recommended=True,
            summary=(
                "Claude Code CLI is unavailable or not installed. "
                "Ensure the 'claude' binary is on PATH and accessible."
            ),
        )

    if isinstance(exc, ClaudeCodeInvocationError):
        return _classify_invocation_error(msg)

    if isinstance(exc, NormalizationError):
        return FailureClassificationResult(
            failure_class=VerificationFailureClass.backend_unusable_response,
            failure_code=VerificationFailureCode.claude_code_unparseable_response,
            retryable=True,
            fallback_recommended=True,
            summary=(
                "Claude Code returned a response that could not be normalized. "
                "The verification result is not reliable."
            ),
        )

    # Unknown exception type — conservative fallback.
    return FailureClassificationResult(
        failure_class=VerificationFailureClass.backend_invocation_error,
        failure_code=VerificationFailureCode.claude_code_process_error,
        retryable=False,
        fallback_recommended=True,
        summary=f"Verification backend failed with an unexpected error: {str(exc)[:120]}",
    )


def classify_empty_response() -> FailureClassificationResult:
    """Classify an empty-output response from the Claude Code backend.

    Empty output is treated as an operational failure: verification cannot be
    performed on a blank response, and the empty output is not a substantive
    verification finding.
    """
    return FailureClassificationResult(
        failure_class=VerificationFailureClass.backend_unusable_response,
        failure_code=VerificationFailureCode.claude_code_empty_response,
        retryable=True,
        fallback_recommended=True,
        summary=(
            "Claude Code returned an empty response. "
            "Verification cannot be performed on blank output."
        ),
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _classify_invocation_error(msg: str) -> FailureClassificationResult:
    """Classify a ClaudeCodeInvocationError by message content."""
    # Timeout (checked first — message is deterministic from the backend)
    if "timed out" in msg:
        return FailureClassificationResult(
            failure_class=VerificationFailureClass.backend_timeout,
            failure_code=VerificationFailureCode.claude_code_timeout,
            retryable=True,
            fallback_recommended=True,
            summary=(
                "Claude Code timed out before returning a response. "
                "The verification could not be completed in the allowed time."
            ),
        )

    # Authentication / login failures (checked before generic limit tokens)
    if any(tok in msg for tok in _AUTH_TOKENS):
        return FailureClassificationResult(
            failure_class=VerificationFailureClass.backend_auth_failure,
            failure_code=VerificationFailureCode.claude_code_not_authenticated,
            retryable=False,
            fallback_recommended=True,
            summary=(
                "Claude Code authentication failed. "
                "Run 'claude login' to re-authenticate before retrying."
            ),
        )

    # Usage / quota / rate-limit exhaustion
    if any(tok in msg for tok in _LIMIT_TOKENS):
        return FailureClassificationResult(
            failure_class=VerificationFailureClass.backend_limit_reached,
            failure_code=VerificationFailureCode.claude_code_limit_reached,
            retryable=True,
            fallback_recommended=True,
            summary=(
                "Claude Code verification backend is currently unavailable due to usage limits. "
                "Retry after the limit window resets or use an external fallback verifier."
            ),
        )

    # Unclassified process failure
    return FailureClassificationResult(
        failure_class=VerificationFailureClass.backend_invocation_error,
        failure_code=VerificationFailureCode.claude_code_process_error,
        retryable=False,
        fallback_recommended=True,
        summary="Claude Code process failed during verification.",
    )
