"""Safe durable timeline, failure, and audit projections for release status."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

JsonObject = Mapping[str, object]

RELEASE_AUDIT_EVENT_TYPES = (
    "governed_release.action.started",
    "governed_release.action.succeeded",
    "governed_release.action.failed",
    "governed_release.action.outcome_unknown",
    "governed_release.action.recovery_started",
    "governed_release.action.retry_started",
    "governed_release.delivery.prepared",
    "governed_release.delivery.dispatching",
    "governed_release.delivery.landed",
    "governed_release.delivery.absent",
    "governed_release.delivery.ambiguous",
    "governed_release.delivery.failed",
    "pipeline.proposal.submitted",
    "pipeline.proposal.assigned",
    "pipeline.proposal.approved",
    "pipeline.proposal.rejected",
    "pipeline.proposal.executed",
    "pipeline.proposal.withdrawn",
    "pipeline.deployment.promoted",
    "ontology.proposal.submitted",
    "ontology.proposal.assigned",
    "ontology.proposal.decided",
    "ontology.proposal.applied",
    "ontology.proposal.apply_failed",
    "ontology.proposal.withdrawn",
    "ontology.version.activated",
    "ontology.rollback",
)


def release_audit_resource_refs(
    kind: str,
    proposal_id: str,
    proposal: JsonObject,
    evidence: JsonObject,
) -> list[tuple[str, str]]:
    refs = [
        ("governed_release_proposal", proposal_id),
        ("pipeline_proposal" if kind == "pipeline" else "ontology_proposal", proposal_id),
    ]
    if kind == "pipeline":
        refs.extend(_pipeline_resource_refs(evidence))
    else:
        refs.extend(_ontology_resource_refs(proposal, evidence))
    refs.extend(_external_delivery_resource_refs(evidence))
    return list(dict.fromkeys(refs))


def release_status_evidence(rows: Sequence[JsonObject]) -> dict[str, object]:
    ordered = sorted(rows, key=_audit_sort_key)
    audit = [_audit_projection(row) for row in ordered]
    return {
        "executionTimeline": [_timeline_projection(item) for item in audit],
        "failureDetails": _current_failure(ordered),
        "auditEvidence": audit,
    }


def _pipeline_resource_refs(evidence: JsonObject) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    deployment = evidence.get("candidateDeployment")
    deployment_id = deployment.get("id") if isinstance(deployment, Mapping) else None
    if isinstance(deployment_id, str):
        refs.append(("pipeline_deployment", deployment_id))
    return refs


def _ontology_resource_refs(proposal: JsonObject, evidence: JsonObject) -> list[tuple[str, str]]:
    del evidence
    refs: list[tuple[str, str]] = []
    applied = proposal.get("appliedOntologyVersion")
    version_id = applied.get("ontologyVersionId") if isinstance(applied, Mapping) else None
    if isinstance(version_id, str):
        refs.append(("ontology_version", version_id))
    return refs


def _external_delivery_resource_refs(evidence: JsonObject) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    source = evidence.get("externalSourceControl")
    delivery = source.get("delivery") if isinstance(source, Mapping) else None
    _append_delivery_ref(refs, delivery)
    external = evidence.get("externalDelivery")
    deliveries = external.get("deliveries") if isinstance(external, Mapping) else None
    if isinstance(deliveries, Sequence) and not isinstance(deliveries, (str, bytes)):
        for item in deliveries:
            _append_delivery_ref(refs, item)
    return refs


def _append_delivery_ref(refs: list[tuple[str, str]], value: object) -> None:
    delivery_id = value.get("deliveryId") if isinstance(value, Mapping) else None
    if isinstance(delivery_id, str):
        refs.append(("governed_release_delivery", delivery_id))


def _audit_projection(row: JsonObject) -> dict[str, object]:
    event_type = str(row.get("event_type") or "unknown")
    return {
        "auditEventId": row.get("id"),
        "eventType": event_type,
        "label": _event_label(event_type),
        "status": _event_status(event_type, row),
        "resourceType": row.get("resource_type"),
        "resourceId": row.get("resource_id"),
        "actorUserId": row.get("actor_user_id"),
        "action": row.get("action"),
        "requestId": row.get("request_id"),
        "correlationId": row.get("correlation_id"),
        "at": row.get("created_at"),
    }


def _timeline_projection(item: JsonObject) -> dict[str, object]:
    return {
        "event": item.get("eventType"),
        "label": item.get("label"),
        "status": item.get("status"),
        "at": item.get("at"),
        "actorDisplayName": item.get("actorUserId"),
        "requestId": item.get("requestId"),
    }


def _current_failure(rows: Sequence[JsonObject]) -> dict[str, object] | None:
    unresolved: dict[str, JsonObject] = {}
    for row in rows:
        event_type = str(row.get("event_type") or "")
        run_id = str(row.get("correlation_id") or row.get("id") or "unknown")
        if _is_failure_event(event_type):
            unresolved[run_id] = row
        elif _is_success_event(event_type):
            unresolved.pop(run_id, None)
            if event_type in {"pipeline.deployment.promoted", "ontology.proposal.applied"}:
                unresolved.clear()
    latest = max(unresolved.values(), key=_audit_sort_key, default=None)
    return _failure_projection(latest) if latest is not None else None


def _failure_projection(row: JsonObject) -> dict[str, object]:
    after_value = row.get("after_ref")
    after = after_value if isinstance(after_value, Mapping) else {}
    event_type = str(row.get("event_type") or "")
    error_type = str(after.get("type") or after.get("errorType") or "release_action_failed")
    is_unknown = event_type.endswith("outcome_unknown") or event_type == "governed_release.delivery.ambiguous"
    return {
        "code": error_type,
        "message": (
            "The release result could not be confirmed; inspect the audit timeline before retrying."
            if is_unknown
            else "The governed release action failed before a confirmed successful completion."
        ),
        "stage": event_type,
        "at": row.get("created_at"),
        "requestId": row.get("request_id"),
        "isRetryable": after.get("safeToRetry") is True,
        "knownNotCommitted": after.get("knownNotCommitted") is True,
        "retryEvidence": after.get("retryEvidence"),
    }


def _event_status(event_type: str, row: JsonObject) -> str:
    if event_type == "governed_release.delivery.landed" and _is_application_delivery(row):
        return "accepted"
    if _is_failure_event(event_type):
        is_unknown = event_type.endswith("outcome_unknown") or event_type == "governed_release.delivery.ambiguous"
        return "outcome_unknown" if is_unknown else "failed"
    if _is_success_event(event_type):
        return "succeeded"
    if event_type.endswith(("started", "assigned", "submitted")):
        return "running"
    if event_type.endswith(("rejected", "withdrawn")):
        return "stopped"
    return "recorded"


def _is_application_delivery(row: JsonObject) -> bool:
    after = row.get("after_ref")
    operation = after.get("operation") if isinstance(after, Mapping) else None
    return operation in {"application_deploy", "application_rollback"}


def _event_label(event_type: str) -> str:
    return event_type.replace("governed_release.action.", "release ").replace(".", " ")


def _is_failure_event(event_type: str) -> bool:
    return event_type in {
        "governed_release.action.failed",
        "governed_release.action.outcome_unknown",
        "governed_release.delivery.absent",
        "governed_release.delivery.ambiguous",
        "governed_release.delivery.failed",
        "ontology.proposal.apply_failed",
    }


def _is_success_event(event_type: str) -> bool:
    return event_type in {
        "governed_release.action.succeeded",
        "governed_release.delivery.landed",
        "pipeline.proposal.approved",
        "pipeline.proposal.executed",
        "pipeline.deployment.promoted",
        "ontology.proposal.applied",
        "ontology.version.activated",
        "ontology.rollback",
    }


def _audit_sort_key(row: JsonObject) -> tuple[str, str]:
    return str(row.get("created_at") or ""), str(row.get("id") or "")


__all__ = [
    "RELEASE_AUDIT_EVENT_TYPES",
    "release_audit_resource_refs",
    "release_status_evidence",
]
