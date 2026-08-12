"""Pure safety rules for provider-backed application rollback."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

from foundry_lite.application.ports.infrastructure_deployment_adapter import (
    InfrastructureDeploymentObservation,
)
from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.domain.errors import ConflictDetected

JsonObject = Mapping[str, object]
_APPLICATION_OPERATIONS = frozenset({"application_deploy", "application_rollback"})
_UNRESOLVED_STATUSES = frozenset({"prepared", "dispatching", "ambiguous"})
APPLICATION_ROLLBACK_TARGET_FIELDS = (
    "targetDeployId",
    "targetCommitId",
    "rolledBackFromDeployId",
)


def application_rollback_target(rows: Sequence[ReleaseDeliveryRecord]) -> dict[str, object] | None:
    """Return the immediately preceding provider state on the current receipt chain."""

    history = _application_history(rows)
    current = _current_landed_delivery(history)
    if current is None or not current.provider_resource_id:
        return None
    if _has_later_unresolved_delivery(history, current):
        return None
    previous_resource_id = _previous_resource_id(history, current)
    previous = _delivery_for_resource(history, current, previous_resource_id)
    target_commit_id = _delivery_commit_id(previous) if previous is not None else None
    if not previous_resource_id or not target_commit_id or previous_resource_id == current.provider_resource_id:
        return None
    return {
        "targetDeployId": previous_resource_id,
        "targetCommitId": target_commit_id,
        "rolledBackFromDeployId": current.provider_resource_id,
    }


def application_rollback_target_for_request(
    rows: Sequence[ReleaseDeliveryRecord],
    idempotency_key: str,
) -> dict[str, object] | None:
    """Return stored replay binding, otherwise the fresh current-chain target."""

    matching = [
        row for row in rows if row.operation == "application_rollback" and row.idempotency_key == idempotency_key
    ]
    if len(matching) > 1:
        return None
    return _stored_rollback_target(matching[0]) if matching else application_rollback_target(rows)


def verified_application_rollback_target(
    arguments: JsonObject,
    current_target: JsonObject,
) -> dict[str, object]:
    """Require the widget-confirmed external target to equal fresh server evidence."""

    verified: dict[str, object] = {}
    for field in APPLICATION_ROLLBACK_TARGET_FIELDS:
        expected = _mapping_text(current_target, field)
        actual = _mapping_text(arguments, field)
        if expected is None or actual != expected:
            raise ConflictDetected(
                "application rollback target does not match the current server evidence",
                details={"field": field, "expected": expected},
            )
        verified[field] = expected
    return verified


def strict_rollback_reconciliation_candidates(
    row: ReleaseDeliveryRecord,
    candidates: Sequence[InfrastructureDeploymentObservation],
    *,
    observed_at: datetime,
) -> tuple[InfrastructureDeploymentObservation, ...]:
    """Keep only a new, exact rollback receipt created inside this dispatch window."""

    dispatch_started_at = _aware_timestamp(row.dispatch_started_at)
    target_deploy_id = _candidate_text(row, "targetDeployId")
    target_commit_id = _candidate_text(row, "targetCommitId")
    service_id = _mapping_text(row.target_ref, "serviceId")
    if row.operation != "application_rollback" or not _is_valid_window(dispatch_started_at, observed_at):
        return ()
    if not target_deploy_id or not target_commit_id or not service_id or not row.prior_resource_id:
        return ()
    excluded_ids = {target_deploy_id, row.prior_resource_id}
    return tuple(
        candidate
        for candidate in candidates
        if _is_exact_new_rollback_candidate(
            candidate,
            row,
            service_id,
            target_commit_id,
            excluded_ids,
            dispatch_started_at,
            observed_at,
        )
    )


def _application_history(rows: Sequence[ReleaseDeliveryRecord]) -> list[ReleaseDeliveryRecord]:
    matching = [row for row in rows if row.operation in _APPLICATION_OPERATIONS]
    return sorted(matching, key=lambda row: (row.created_at, row.delivery_id))


def _stored_rollback_target(row: ReleaseDeliveryRecord) -> dict[str, object] | None:
    target_deploy_id = _candidate_text(row, "targetDeployId")
    target_commit_id = _candidate_text(row, "targetCommitId")
    if not target_deploy_id or not target_commit_id or not row.prior_resource_id:
        return None
    return {
        "targetDeployId": target_deploy_id,
        "targetCommitId": target_commit_id,
        "rolledBackFromDeployId": row.prior_resource_id,
    }


def _current_landed_delivery(history: Sequence[ReleaseDeliveryRecord]) -> ReleaseDeliveryRecord | None:
    return next(
        (row for row in reversed(history) if row.status == "landed" and row.provider_resource_id),
        None,
    )


def _has_later_unresolved_delivery(
    history: Sequence[ReleaseDeliveryRecord],
    current: ReleaseDeliveryRecord,
) -> bool:
    current_key = (current.created_at, current.delivery_id)
    return any(
        (row.created_at, row.delivery_id) > current_key
        and row.status in _UNRESOLVED_STATUSES
        and _same_provider_chain(row, current)
        for row in history
    )


def _previous_resource_id(
    history: Sequence[ReleaseDeliveryRecord],
    current: ReleaseDeliveryRecord,
) -> str | None:
    if current.prior_resource_id:
        return current.prior_resource_id
    current_key = (current.created_at, current.delivery_id)
    previous = [
        row
        for row in history
        if (row.created_at, row.delivery_id) < current_key
        and row.status == "landed"
        and row.provider_resource_id
        and _same_provider_chain(row, current)
    ]
    return previous[-1].provider_resource_id if previous else None


def _delivery_for_resource(
    history: Sequence[ReleaseDeliveryRecord],
    current: ReleaseDeliveryRecord,
    resource_id: str | None,
) -> ReleaseDeliveryRecord | None:
    if resource_id is None:
        return None
    current_key = (current.created_at, current.delivery_id)
    matching = [
        row
        for row in history
        if (row.created_at, row.delivery_id) < current_key
        and row.status == "landed"
        and row.provider_resource_id == resource_id
        and _same_provider_chain(row, current)
    ]
    return matching[0] if len(matching) == 1 else None


def _same_provider_chain(left: ReleaseDeliveryRecord, right: ReleaseDeliveryRecord) -> bool:
    return (
        left.provider == right.provider
        and left.environment == right.environment
        and left.target_ref.get("serviceId") == right.target_ref.get("serviceId")
    )


def _delivery_commit_id(row: ReleaseDeliveryRecord | None) -> str | None:
    if row is None:
        return None
    key = "commitId" if row.operation == "application_deploy" else "targetCommitId"
    return _candidate_text(row, key)


def _is_exact_new_rollback_candidate(
    candidate: InfrastructureDeploymentObservation,
    row: ReleaseDeliveryRecord,
    service_id: str,
    target_commit_id: str,
    excluded_ids: set[str],
    dispatch_started_at: datetime | None,
    observed_at: datetime,
) -> bool:
    created_at = candidate.created_at
    return (
        candidate.provider == row.provider
        and candidate.service_id == service_id
        and candidate.commit_id == target_commit_id
        and candidate.deploy_id not in excluded_ids
        and candidate.trigger == "rollback"
        and created_at is not None
        and dispatch_started_at is not None
        and dispatch_started_at <= created_at <= observed_at
    )


def _is_valid_window(started_at: datetime | None, observed_at: datetime) -> bool:
    return (
        started_at is not None
        and observed_at.tzinfo is not None
        and observed_at.utcoffset() is not None
        and started_at <= observed_at
    )


def _candidate_text(row: ReleaseDeliveryRecord, key: str) -> str | None:
    return _mapping_text(row.candidate_ref, key)


def _mapping_text(payload: Mapping[str, object] | None, key: str) -> str | None:
    value = payload.get(key) if payload is not None else None
    return value.strip() if isinstance(value, str) and value.strip() else None


def _aware_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


__all__ = [
    "APPLICATION_ROLLBACK_TARGET_FIELDS",
    "application_rollback_target",
    "application_rollback_target_for_request",
    "strict_rollback_reconciliation_candidates",
    "verified_application_rollback_target",
]
