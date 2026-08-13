from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentObservation,
)
from foundry_lite.application.ports.release_delivery_repository import (
    ReleaseDeliveryOperation,
    ReleaseDeliveryRecord,
)
from foundry_lite.application.services.aip.external_release_rollback import (
    application_rollback_target,
    application_rollback_target_for_request,
    strict_rollback_reconciliation_candidates,
    verified_application_rollback_target,
)
from foundry_lite.domain.errors import ConflictDetected

DISPATCHED_AT = datetime(2026, 8, 9, 3, 0, tzinfo=UTC)


def test_rollback_target_follows_deploy_and_rollback_receipt_chain() -> None:
    deploy_a = _delivery("delivery-a", "application_deploy", "deploy-a", "a" * 40, 1)
    deploy_b = _delivery("delivery-b", "application_deploy", "deploy-b", "b" * 40, 2)
    rollback_to_a = _delivery(
        "delivery-r1",
        "application_rollback",
        "deploy-r1",
        "a" * 40,
        3,
        prior_resource_id="deploy-b",
        target_deploy_id="deploy-a",
    )
    rollback_to_b = _delivery(
        "delivery-r2",
        "application_rollback",
        "deploy-r2",
        "b" * 40,
        4,
        prior_resource_id="deploy-r1",
        target_deploy_id="deploy-b",
    )

    assert application_rollback_target((deploy_a, deploy_b)) == {
        "targetDeployId": "deploy-a",
        "targetCommitId": "a" * 40,
        "rolledBackFromDeployId": "deploy-b",
    }
    assert application_rollback_target((deploy_a, deploy_b, rollback_to_a)) == {
        "targetDeployId": "deploy-b",
        "targetCommitId": "b" * 40,
        "rolledBackFromDeployId": "deploy-r1",
    }
    assert application_rollback_target((deploy_a, deploy_b, rollback_to_a, rollback_to_b)) == {
        "targetDeployId": "deploy-r1",
        "targetCommitId": "a" * 40,
        "rolledBackFromDeployId": "deploy-r2",
    }

    assert application_rollback_target_for_request(
        (deploy_a, deploy_b, rollback_to_a, rollback_to_b),
        rollback_to_a.idempotency_key,
    ) == {
        "targetDeployId": "deploy-a",
        "targetCommitId": "a" * 40,
        "rolledBackFromDeployId": "deploy-b",
    }


def test_rollback_target_fails_closed_while_a_later_provider_mutation_is_unresolved() -> None:
    deploy_a = _delivery("delivery-a", "application_deploy", "deploy-a", "a" * 40, 1)
    deploy_b = _delivery("delivery-b", "application_deploy", "deploy-b", "b" * 40, 2)
    unresolved = replace(
        _delivery("delivery-c", "application_deploy", None, "c" * 40, 3),
        status="ambiguous",
    )

    assert application_rollback_target((deploy_a, deploy_b, unresolved)) is None


@pytest.mark.parametrize(
    ("changed_field", "changed_value"),
    [
        ("targetDeployId", "deploy-attacker"),
        ("targetCommitId", "c" * 40),
        ("rolledBackFromDeployId", "deploy-stale"),
    ],
)
def test_widget_confirmed_application_target_must_equal_fresh_server_evidence(
    changed_field: str,
    changed_value: str,
) -> None:
    current = _target()
    arguments = {**current, changed_field: changed_value}

    with pytest.raises(ConflictDetected, match="current server evidence") as raised:
        verified_application_rollback_target(arguments, current)

    assert raised.value.details["field"] == changed_field
    assert verified_application_rollback_target(current, current) == current


def test_ambiguous_rollback_accepts_only_a_new_exact_provider_rollback_in_its_dispatch_window() -> None:
    row = replace(
        _delivery(
            "delivery-r1",
            "application_rollback",
            None,
            "a" * 40,
            1,
            prior_resource_id="deploy-current",
            target_deploy_id="deploy-target",
        ),
        status="ambiguous",
        dispatch_started_at=DISPATCHED_AT.isoformat(),
    )
    observed_at = DISPATCHED_AT + timedelta(minutes=1)
    valid = _observation("deploy-new", trigger="rollback", created_at=DISPATCHED_AT + timedelta(seconds=1))
    candidates = (
        _observation("deploy-target", trigger="rollback", created_at=DISPATCHED_AT + timedelta(seconds=1)),
        _observation("deploy-current", trigger="rollback", created_at=DISPATCHED_AT + timedelta(seconds=1)),
        _observation("deploy-unrelated-api", trigger="api", created_at=DISPATCHED_AT + timedelta(seconds=1)),
        _observation("deploy-before-dispatch", trigger="rollback", created_at=DISPATCHED_AT - timedelta(seconds=1)),
        _observation("deploy-after-window", trigger="rollback", created_at=observed_at + timedelta(seconds=1)),
        replace(valid, deploy_id="deploy-wrong-service", service_id="srv-other"),
        replace(valid, deploy_id="deploy-wrong-provider", provider="other"),
        replace(valid, deploy_id="deploy-wrong-commit", commit_id="b" * 40),
        replace(valid, deploy_id="deploy-no-time", created_at=None),
        valid,
    )

    filtered = strict_rollback_reconciliation_candidates(row, candidates, observed_at=observed_at)

    assert filtered == (valid,)


def test_ambiguous_rollback_fails_closed_without_dispatch_time_or_unique_new_candidate() -> None:
    row = replace(
        _delivery(
            "delivery-r1",
            "application_rollback",
            None,
            "a" * 40,
            1,
            prior_resource_id="deploy-current",
            target_deploy_id="deploy-target",
        ),
        status="ambiguous",
        dispatch_started_at=None,
    )
    candidate = _observation(
        "deploy-new",
        trigger="rollback",
        created_at=DISPATCHED_AT + timedelta(seconds=1),
    )

    assert strict_rollback_reconciliation_candidates(row, (candidate,), observed_at=DISPATCHED_AT) == ()


def _target() -> dict[str, object]:
    return {
        "targetDeployId": "deploy-target",
        "targetCommitId": "a" * 40,
        "rolledBackFromDeployId": "deploy-current",
    }


def _delivery(
    delivery_id: str,
    operation: ReleaseDeliveryOperation,
    provider_resource_id: str | None,
    commit_id: str,
    sequence: int,
    *,
    prior_resource_id: str | None = None,
    target_deploy_id: str | None = None,
) -> ReleaseDeliveryRecord:
    candidate: dict[str, object] = (
        {"commitId": commit_id}
        if operation == "application_deploy"
        else {"targetDeployId": target_deploy_id, "targetCommitId": commit_id}
    )
    created_at = f"2026-08-09T03:00:{sequence:02d}+00:00"
    return ReleaseDeliveryRecord(
        delivery_id=delivery_id,
        tenant_id="tenant-a",
        application_id="application-a",
        proposal_id="proposal-a",
        release_kind="pipeline",
        workflow_run_id="workflow-run-a",
        parent_delivery_id="delivery-root",
        provider="render",
        operation=operation,
        status="landed",
        target_ref={"serviceId": "srv-service"},
        candidate_ref=candidate,
        environment="production",
        idempotency_key=f"key-{delivery_id}",
        request_fingerprint=f"sha256:{sequence}",
        provider_operation_id=provider_resource_id,
        provider_resource_id=provider_resource_id,
        prior_resource_id=prior_resource_id,
        result_ref={"commitId": commit_id},
        error_ref=None,
        ai_run_id="release-run-a",
        binding_hash="sha256:binding",
        execution_attempt=1,
        request_id="request-a",
        created_by="reviewer-a",
        created_at=created_at,
        updated_at=created_at,
        dispatch_started_at=created_at,
        completed_at=created_at,
    )


def _observation(
    deploy_id: str,
    *,
    trigger: str,
    created_at: datetime | None,
) -> InfrastructureDeploymentObservation:
    return InfrastructureDeploymentObservation(
        provider="render",
        service_id="srv-service",
        deploy_id=deploy_id,
        status="queued",
        provider_status="created",
        commit_id="a" * 40,
        trigger=trigger,
        created_at=created_at,
        started_at=None,
        updated_at=created_at,
        finished_at=None,
        is_terminal=False,
        is_successful=False,
        provider_request_id=f"request-{deploy_id}",
    )
