from __future__ import annotations

import json

from foundry_lite.application.services.aip.governed_release_status_evidence import (
    RELEASE_AUDIT_EVENT_TYPES,
    release_audit_resource_refs,
    release_status_evidence,
)


def test_failure_retry_success_timeline_is_ordered_and_clears_current_failure() -> None:
    rows = [
        _audit_row(
            "audit-success",
            "governed_release.action.succeeded",
            "2026-08-09T00:03:00+00:00",
        ),
        _audit_row(
            "audit-failed",
            "governed_release.action.failed",
            "2026-08-09T00:01:00+00:00",
            after_ref={
                "type": "ConflictDetected",
                "knownNotCommitted": True,
                "safeToRetry": True,
                "retryEvidence": "pre_mutation_foundry_error",
            },
        ),
        _audit_row(
            "audit-retry",
            "governed_release.action.retry_started",
            "2026-08-09T00:02:00+00:00",
        ),
    ]

    evidence = release_status_evidence(rows)

    assert [item["auditEventId"] for item in evidence["auditEvidence"]] == [
        "audit-failed",
        "audit-retry",
        "audit-success",
    ]
    assert [item["event"] for item in evidence["executionTimeline"]] == [
        "governed_release.action.failed",
        "governed_release.action.retry_started",
        "governed_release.action.succeeded",
    ]
    assert [item["status"] for item in evidence["executionTimeline"]] == [
        "failed",
        "running",
        "succeeded",
    ]
    assert evidence["failureDetails"] is None


def test_success_resolves_only_the_failure_from_the_same_release_run() -> None:
    first_failure = _audit_row(
        "audit-run-a-failed",
        "governed_release.action.failed",
        "2026-08-09T00:01:00+00:00",
        correlation_id="release-run-a",
        after_ref={"type": "ConflictDetected", "knownNotCommitted": True},
    )
    second_failure = _audit_row(
        "audit-run-b-failed",
        "governed_release.action.outcome_unknown",
        "2026-08-09T00:02:00+00:00",
        correlation_id="release-run-b",
        after_ref={"type": "InvariantViolation"},
    )
    first_success = _audit_row(
        "audit-run-a-succeeded",
        "governed_release.action.succeeded",
        "2026-08-09T00:03:00+00:00",
        correlation_id="release-run-a",
    )

    unresolved = release_status_evidence([first_success, second_failure, first_failure])

    assert unresolved["failureDetails"] == {
        "code": "InvariantViolation",
        "message": "The release result could not be confirmed; inspect the audit timeline before retrying.",
        "stage": "governed_release.action.outcome_unknown",
        "at": "2026-08-09T00:02:00+00:00",
        "requestId": "request-audit-run-b-failed",
        "isRetryable": False,
        "knownNotCommitted": False,
        "retryEvidence": None,
    }

    second_success = _audit_row(
        "audit-run-b-succeeded",
        "governed_release.action.succeeded",
        "2026-08-09T00:04:00+00:00",
        correlation_id="release-run-b",
    )
    resolved = release_status_evidence([first_failure, second_failure, first_success, second_success])

    assert resolved["failureDetails"] is None


def test_status_projection_is_tenant_safe_allowlisted_and_hides_raw_evidence() -> None:
    row = _audit_row(
        "audit-secret",
        "governed_release.action.failed",
        "2026-08-09T00:01:00+00:00",
        after_ref={
            "type": "ValidationFailed",
            "knownNotCommitted": True,
            "safeToRetry": True,
            "retryEvidence": "pre_mutation_foundry_error",
            "rawException": "stack-secret",
            "widgetConfirmationToken": "widget-secret",
        },
    )
    row.update(
        {
            "tenant_id": "tenant-super-secret",
            "before_ref": {"password": "database-password"},
            "policy_decision": {"internalRule": "private-policy"},
            "metadata": {"oauthToken": "oauth-secret"},
        }
    )

    evidence = release_status_evidence([row])
    audit = evidence["auditEvidence"][0]
    timeline = evidence["executionTimeline"][0]
    failure = evidence["failureDetails"]

    assert set(audit) == {
        "auditEventId",
        "eventType",
        "label",
        "status",
        "resourceType",
        "resourceId",
        "actorUserId",
        "action",
        "requestId",
        "correlationId",
        "at",
    }
    assert set(timeline) == {
        "event",
        "label",
        "status",
        "at",
        "actorDisplayName",
        "requestId",
    }
    assert failure == {
        "code": "ValidationFailed",
        "message": "The governed release action failed before a confirmed successful completion.",
        "stage": "governed_release.action.failed",
        "at": "2026-08-09T00:01:00+00:00",
        "requestId": "request-audit-secret",
        "isRetryable": True,
        "knownNotCommitted": True,
        "retryEvidence": "pre_mutation_foundry_error",
    }
    rendered = json.dumps(evidence, sort_keys=True)
    for secret in (
        "tenant-super-secret",
        "database-password",
        "private-policy",
        "oauth-secret",
        "stack-secret",
        "widget-secret",
    ):
        assert secret not in rendered
    for raw_field in ("tenant_id", "before_ref", "after_ref", "policy_decision", "metadata"):
        assert raw_field not in rendered


def test_audit_order_is_stable_by_timestamp_then_event_id() -> None:
    rows = [
        _audit_row(
            "audit-b",
            "governed_release.action.succeeded",
            "2026-08-09T00:02:00+00:00",
        ),
        _audit_row(
            "audit-z",
            "pipeline.proposal.submitted",
            "2026-08-09T00:01:00+00:00",
        ),
        _audit_row(
            "audit-a",
            "pipeline.proposal.assigned",
            "2026-08-09T00:02:00+00:00",
        ),
    ]

    evidence = release_status_evidence(rows)

    assert [item["auditEventId"] for item in evidence["auditEvidence"]] == [
        "audit-z",
        "audit-a",
        "audit-b",
    ]
    assert [item["event"] for item in evidence["executionTimeline"]] == [
        "pipeline.proposal.submitted",
        "pipeline.proposal.assigned",
        "governed_release.action.succeeded",
    ]


def test_external_delivery_receipts_are_included_in_status_resources_and_timeline() -> None:
    evidence = {
        "externalSourceControl": {"delivery": {"deliveryId": "delivery-source"}},
        "externalDelivery": {
            "deliveries": [
                {"deliveryId": "delivery-source"},
                {"deliveryId": "delivery-deploy"},
            ]
        },
    }

    refs = release_audit_resource_refs("pipeline", "proposal-1", {}, evidence)

    assert ("governed_release_delivery", "delivery-source") in refs
    assert ("governed_release_delivery", "delivery-deploy") in refs
    assert refs.count(("governed_release_delivery", "delivery-source")) == 1
    assert "governed_release.delivery.ambiguous" in RELEASE_AUDIT_EVENT_TYPES
    ambiguous = _audit_row(
        "audit-delivery-unknown",
        "governed_release.delivery.ambiguous",
        "2026-08-09T00:01:00+00:00",
        after_ref={"type": "provider_outcome_unknown"},
    )
    landed = _audit_row(
        "audit-delivery-landed",
        "governed_release.delivery.landed",
        "2026-08-09T00:02:00+00:00",
    )
    status = release_status_evidence([ambiguous, landed])
    assert [item["status"] for item in status["executionTimeline"]] == ["outcome_unknown", "succeeded"]
    assert status["failureDetails"] is None


def test_status_resources_exclude_unrelated_current_runtime_comparison_targets() -> None:
    pipeline_refs = release_audit_resource_refs(
        "pipeline",
        "proposal-1",
        {},
        {
            "candidateDeployment": {"id": "candidate-deployment"},
            "currentDeployment": {"id": "unrelated-current-deployment"},
        },
    )
    ontology_refs = release_audit_resource_refs(
        "ontology",
        "proposal-2",
        {"appliedOntologyVersion": {"ontologyVersionId": "applied-by-proposal"}},
        {"activeOntology": {"ontologyVersionId": "unrelated-current-ontology"}},
    )

    assert ("pipeline_deployment", "candidate-deployment") in pipeline_refs
    assert ("pipeline_deployment", "unrelated-current-deployment") not in pipeline_refs
    assert ("ontology_version", "applied-by-proposal") in ontology_refs
    assert ("ontology_version", "unrelated-current-ontology") not in ontology_refs


def _audit_row(
    audit_id: str,
    event_type: str,
    created_at: str,
    *,
    correlation_id: str = "release-run-1",
    after_ref: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": audit_id,
        "tenant_id": "tenant-a",
        "event_type": event_type,
        "resource_type": "governed_release_proposal",
        "resource_id": "proposal-1",
        "actor_user_id": "reviewer-1",
        "action": "deploy_release",
        "request_id": f"request-{audit_id}",
        "correlation_id": correlation_id,
        "created_at": created_at,
        "after_ref": dict(after_ref or {}),
    }
