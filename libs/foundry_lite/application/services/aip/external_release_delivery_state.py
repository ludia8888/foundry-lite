"""Pure operational-state rules for external governed release delivery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Literal

from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentObservation,
)
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord

ApplicationDeliveryStatus = Literal[
    "not_configured",
    "not_started",
    "pending",
    "receipt_accepted",
    "deploying",
    "live",
    "failed",
    "not_delivered",
    "outcome_unknown",
    "observation_unavailable",
]
InfrastructureReceiptState = Literal["accepted", "live", "failed", "outcome_unknown"]


def delivery_receipt_status(row: ReleaseDeliveryRecord) -> str:
    """Describe what the durable row proves, without calling acceptance live."""

    if row.status != "landed":
        return row.status
    if row.operation == "source_publish":
        return "published"
    return "merged" if row.operation == "source_merge" else "receipt_accepted"


def infrastructure_receipt_state(observation: InfrastructureDeploymentObservation) -> InfrastructureReceiptState:
    """Classify one mutation receipt independently from later provider observation."""

    if observation.status == "live" and observation.is_terminal and observation.is_successful:
        return "live"
    if observation.is_terminal and not observation.is_successful:
        return "failed"
    if observation.status in {"queued", "building", "preparing", "deploying"} and not observation.is_terminal:
        return "accepted"
    return "outcome_unknown"


def application_delivery_summary(
    rows: Sequence[ReleaseDeliveryRecord],
    observations: Sequence[Mapping[str, object]],
    *,
    is_configured: bool,
    is_required: bool,
) -> dict[str, object]:
    """Project current application completion from an exact provider observation."""

    if not is_configured:
        return _summary("not_configured", is_required=is_required)
    latest = _latest_application_row(rows)
    if latest is None:
        return _summary("not_started", is_required=is_required)
    status, reason = _application_row_status(latest, observations)
    return _summary(
        status,
        is_required=is_required,
        row=latest,
        reason=reason,
    )


def _application_row_status(
    row: ReleaseDeliveryRecord,
    observations: Sequence[Mapping[str, object]],
) -> tuple[ApplicationDeliveryStatus, str | None]:
    if row.status in {"prepared", "dispatching"}:
        return "pending", None
    if row.status == "ambiguous":
        return "outcome_unknown", "provider_mutation_outcome_unknown"
    if row.status == "absent":
        return "not_delivered", "provider_resource_authoritatively_absent"
    if row.status == "failed":
        return "failed", "provider_delivery_failed"
    observation = _observation_for(row, observations)
    if observation is None or observation.get("status") == "observation_unavailable":
        return "observation_unavailable", "provider_observation_unavailable"
    if not _observation_identity_matches(row, observation):
        return "outcome_unknown", "provider_observation_identity_mismatch"
    return _observed_application_status(observation)


def _observed_application_status(observation: Mapping[str, object]) -> tuple[ApplicationDeliveryStatus, str | None]:
    status = observation.get("status")
    is_terminal = observation.get("isTerminal") is True
    is_successful = observation.get("isSuccessful") is True
    if status == "live" and is_terminal and is_successful:
        return "live", None
    if is_terminal and not is_successful:
        return "failed", "provider_deployment_terminal_failure"
    if status in {"queued", "building", "preparing", "deploying"} and not is_terminal:
        return "deploying", None
    return "outcome_unknown", "provider_observation_state_inconsistent"


def _observation_identity_matches(row: ReleaseDeliveryRecord, observation: Mapping[str, object]) -> bool:
    return (
        observation.get("provider") == row.provider
        and observation.get("deployId") == row.provider_resource_id
        and observation.get("serviceId") == row.target_ref.get("serviceId")
        and observation.get("commitId") == _delivery_commit_id(row)
    )


def _delivery_commit_id(row: ReleaseDeliveryRecord) -> object:
    candidate = row.candidate_ref or {}
    key = "commitId" if row.operation == "application_deploy" else "targetCommitId"
    return candidate.get(key)


def _observation_for(
    row: ReleaseDeliveryRecord,
    observations: Sequence[Mapping[str, object]],
) -> Mapping[str, object] | None:
    return next((item for item in observations if item.get("deliveryId") == row.delivery_id), None)


def _latest_application_row(rows: Sequence[ReleaseDeliveryRecord]) -> ReleaseDeliveryRecord | None:
    application_rows = [row for row in rows if row.operation in {"application_deploy", "application_rollback"}]
    return max(application_rows, key=lambda row: (row.created_at, row.delivery_id), default=None)


def _summary(
    status: ApplicationDeliveryStatus,
    *,
    is_required: bool,
    row: ReleaseDeliveryRecord | None = None,
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "isRequired": is_required,
        "isOperationallyComplete": status == "live",
        "deliveryId": row.delivery_id if row is not None else None,
        "operation": row.operation if row is not None else None,
        "providerResourceId": row.provider_resource_id if row is not None else None,
        "reason": reason,
    }


__all__ = [
    "ApplicationDeliveryStatus",
    "application_delivery_summary",
    "delivery_receipt_status",
    "infrastructure_receipt_state",
]
