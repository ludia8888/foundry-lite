from __future__ import annotations

from typing import Any, cast

import pytest
from foundry_lite.application.ports.action_run_orchestrator import ActionRunRetryableFailure
from foundry_lite.application.ports.adapter_failure import AdapterError, AdapterFailure, AdapterFailureKind
from foundry_lite.application.services.action_distributed_run_evidence import (
    action_error_kind,
    is_action_error_retryable,
)
from foundry_lite.application.services.action_distributed_run_support import require_stored_plan_hash
from foundry_lite.domain.action_runtime.action_execution_plan import seal_action_execution_plan
from foundry_lite.domain.errors import (
    ConflictDetected,
    ExternalCompensationRequired,
    ExternalOutcomeUnknown,
    ExternalRetryableWriteback,
    InvariantViolation,
    PermissionDenied,
    RateLimited,
    ValidationFailed,
)


@pytest.mark.parametrize(
    ("kind", "is_adapter_retryable", "expected"),
    [
        ("timeout", True, True),
        ("unavailable", True, True),
        ("rate_limited", True, True),
        ("timeout", False, False),
        ("validation", True, False),
        ("authentication", True, False),
        ("authorization", True, False),
        ("conflict", True, False),
        ("unsupported", True, False),
        ("not_found", True, False),
        ("unknown", True, False),
    ],
)
def test_action_adapter_retry_policy_is_fail_closed(
    kind: AdapterFailureKind, is_adapter_retryable: bool, expected: bool
) -> None:
    error = _adapter_error(kind, is_adapter_retryable)

    assert is_action_error_retryable(error) is expected
    assert action_error_kind(error) == f"adapter_{kind}"


@pytest.mark.parametrize(
    ("error", "kind", "is_retryable"),
    [
        (TimeoutError(), "transient_adapter", True),
        (ConnectionError(), "transient_adapter", True),
        (ActionRunRetryableFailure(), "transient_adapter", True),
        (ConflictDetected("conflict"), "conflict", False),
        (PermissionDenied("denied"), "authorization", False),
        (ValidationFailed("invalid"), "validation", False),
        (InvariantViolation("broken"), "invariant", False),
        (RateLimited("limited"), "rate_limited", False),
        (MemoryError(), "resource_oom", False),
        (ExternalOutcomeUnknown("unknown"), "outcome_unknown", False),
        (ExternalCompensationRequired("reconcile"), "reconciliation_required", False),
        (ExternalRetryableWriteback("external"), "external_retryable", False),
        (RuntimeError("unexpected"), "permanent", False),
    ],
)
def test_action_failure_taxonomy_covers_terminal_and_retryable_classes(
    error: Exception, kind: str, is_retryable: bool
) -> None:
    assert action_error_kind(error) == kind
    assert is_action_error_retryable(error) is is_retryable


def test_external_mcp_approval_authority_does_not_change_stored_plan_hash() -> None:
    sealed = seal_action_execution_plan(
        {
            "actionApiName": "BookReservation",
            "target": {"objectType": "Restaurant", "objectId": "restaurant-1"},
            "parameters": {"partySize": 2},
            "editManifest": {"objectCreates": [], "readSetVersions": {}},
        }
    )
    snapshot = {
        **sealed,
        "contract": {"contractVersion": 3},
        "principal": {"actorUserId": "reviewer-1"},
        "externalMcpApproval": {
            "source": "ontology_mcp",
            "reviewId": "review-1",
            "servicePrincipalId": "service-principal:client-1",
        },
    }
    row = cast(Any, {"execution_plan": snapshot, "plan_hash": sealed["planHash"]})

    require_stored_plan_hash(row)


def _adapter_error(kind: AdapterFailureKind, is_retryable: bool) -> AdapterError:
    return AdapterError(
        AdapterFailure(
            adapter_profile="taxonomy-test",
            operation="execute_action",
            kind=kind,
            is_retryable=is_retryable,
            operator_message=f"{kind} failure",
        )
    )
