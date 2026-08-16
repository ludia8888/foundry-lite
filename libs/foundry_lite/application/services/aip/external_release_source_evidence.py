"""Safe source-control evidence for unavailable and unconfigured profiles."""

from __future__ import annotations

from collections.abc import Callable

from foundry_lite.application.ports.adapter_failure import AdapterError, adapter_failure_payload
from foundry_lite.application.ports.source_control_release import PullRequestSnapshot
from foundry_lite.application.services.aip.external_release_delivery_payloads import source_snapshot_evidence
from foundry_lite.domain.errors import ConflictDetected


def source_snapshot_evidence_or_failure(
    snapshot_reader: Callable[[], PullRequestSnapshot],
    is_required: bool,
    provider: str,
) -> dict[str, object]:
    """Project one fresh snapshot while containing provider read failures."""

    try:
        return source_snapshot_evidence(snapshot_reader(), is_required=is_required)
    except (AdapterError, ConflictDetected, ValueError) as exc:
        return source_read_failure(exc, is_required, provider)


def source_read_failure(exc: Exception, is_required: bool, provider: str) -> dict[str, object]:
    error = adapter_failure_payload(exc) if isinstance(exc, AdapterError) else {"type": type(exc).__name__}
    return {
        "provider": provider,
        "isConfigured": True,
        "isRequired": is_required,
        "status": "unavailable",
        "ciReceipt": {
            "id": "external-ci-receipt",
            "label": "Candidate-scoped external CI receipt",
            "status": "not_available",
            "proofKind": "external_ci",
            "details": {"reason": "source_control_evidence_unavailable"},
        },
        "error": error,
    }


def unconfigured_source_evidence(is_required: bool) -> dict[str, object]:
    return {
        "provider": None,
        "isConfigured": False,
        "isRequired": is_required,
        "status": "not_configured",
        "ciReceipt": {
            "id": "external-ci-receipt",
            "label": "Candidate-scoped external CI receipt",
            "status": "not_available",
            "proofKind": "external_ci",
            "details": {"reason": "source_control_not_configured"},
        },
    }


def unpublished_source_evidence(is_required: bool, provider: str) -> dict[str, object]:
    """Describe the explicit author action required before PR review can begin."""

    return {
        "provider": provider,
        "isConfigured": True,
        "isRequired": is_required,
        "status": "not_published",
        "ciReceipt": {
            "id": "external-ci-receipt",
            "label": "Candidate-scoped external CI receipt",
            "status": "not_available",
            "proofKind": "external_ci",
            "details": {"reason": "source_candidate_not_published"},
        },
    }


__all__ = [
    "source_read_failure",
    "source_snapshot_evidence_or_failure",
    "unconfigured_source_evidence",
    "unpublished_source_evidence",
]
