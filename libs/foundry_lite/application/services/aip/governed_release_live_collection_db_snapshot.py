"""Delivery claims and deterministic DB fingerprint for live collection."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Literal, cast

from foundry_lite.application.ports.release_delivery_repository import ReleaseDeliveryRecord
from foundry_lite.application.services.aip.fde_tool_result import hash_json
from foundry_lite.application.services.aip.governed_release_live_collection_contract import (
    DeliveryOperation,
    ReleaseKind,
    ReleaseTool,
    ServerActionClaim,
    ServerDeliveryClaim,
)
from foundry_lite.application.services.aip.governed_release_live_collection_db_types import (
    LoadedActionLedger,
    SelectedAuditEvidence,
    ServerActionAuditClaim,
    ServerActionResultClaim,
    ServerLoadedDatabaseSnapshot,
    conflict,
    invalid,
    parse_time,
)

DELIVERY_TOOL: dict[DeliveryOperation, ReleaseTool] = {
    "source_publish": "publish_release_candidate",
    "source_merge": "execute_approved_release",
    "application_deploy": "deploy_release",
    "application_rollback": "rollback_release",
}


def delivery_claims(
    chains: Mapping[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]],
    actions: Sequence[LoadedActionLedger],
) -> tuple[ServerDeliveryClaim, ...]:
    action_map = {(item.claim.release_kind, item.claim.tool_name): item for item in actions}
    claims: list[ServerDeliveryClaim] = []
    for kind in ("ontology", "pipeline"):
        for row in chains[kind]:
            action = action_map[(kind, DELIVERY_TOOL[row.operation])]
            _require_delivery_action(row, action)
            claims.append(_delivery_claim(kind, row))
    return tuple(claims)


def database_snapshot(
    tenant_id: str,
    application_id: str,
    workflow_run_ids: Mapping[ReleaseKind, str],
    proposals: Mapping[ReleaseKind, str],
    chains: Mapping[ReleaseKind, tuple[ReleaseDeliveryRecord, ...]],
    loaded: tuple[LoadedActionLedger, ...],
    deliveries: tuple[ServerDeliveryClaim, ...],
    audits: Mapping[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence],
) -> ServerLoadedDatabaseSnapshot:
    actions = tuple(item.claim for item in loaded)
    results = tuple(item.result for item in loaded)
    records = (*chains["ontology"], *chains["pipeline"])
    audit_values = tuple(sorted(audits.values(), key=lambda item: item.event_id))
    audit_ids = tuple(item.event_id for item in audit_values)
    action_audits = _action_audit_claims(audits)
    fingerprint = _database_fingerprint(tenant_id, application_id, actions, results, deliveries, records, audit_values)
    return _database_snapshot_record(
        tenant_id,
        application_id,
        workflow_run_ids,
        proposals,
        loaded,
        actions,
        results,
        action_audits,
        deliveries,
        records,
        audit_ids,
        fingerprint,
    )


def _action_audit_claims(
    audits: Mapping[tuple[ReleaseKind, ReleaseTool], SelectedAuditEvidence],
) -> tuple[ServerActionAuditClaim, ...]:
    return tuple(
        ServerActionAuditClaim(kind, tool, evidence.ai_run_id, evidence.event_id)
        for (kind, tool), evidence in sorted(audits.items())
    )


def _database_snapshot_record(
    tenant_id: str,
    application_id: str,
    workflow_run_ids: Mapping[ReleaseKind, str],
    proposals: Mapping[ReleaseKind, str],
    loaded: tuple[LoadedActionLedger, ...],
    actions: tuple[ServerActionClaim, ...],
    results: tuple[ServerActionResultClaim, ...],
    action_audits: tuple[ServerActionAuditClaim, ...],
    deliveries: tuple[ServerDeliveryClaim, ...],
    records: tuple[ReleaseDeliveryRecord, ...],
    audit_ids: tuple[str, ...],
    fingerprint: str,
) -> ServerLoadedDatabaseSnapshot:
    submitter = next(item.claim for item in loaded if item.claim.tool_name == "publish_release_candidate")
    reviewer = next(item.claim for item in loaded if item.claim.tool_name != "publish_release_candidate")
    return ServerLoadedDatabaseSnapshot(
        tenant_id,
        application_id,
        workflow_run_ids["ontology"],
        workflow_run_ids["pipeline"],
        proposals["ontology"],
        proposals["pipeline"],
        loaded[0].policy_fingerprint,
        submitter.actor_subject_hash,
        submitter.oauth_session_hash,
        reviewer.actor_subject_hash,
        reviewer.oauth_session_hash,
        True,
        loaded[0].authorization_policy,
        actions,
        results,
        action_audits,
        deliveries,
        records,
        audit_ids,
        fingerprint,
        datetime.now(UTC),
    )


def delivery_times(row: ReleaseDeliveryRecord) -> tuple[datetime, datetime]:
    dispatch = parse_time(row.dispatch_started_at, "workflow_delivery_timestamp_invalid")
    completed = parse_time(row.completed_at, "workflow_delivery_timestamp_invalid")
    if completed < dispatch:
        invalid("workflow_delivery_timestamp_invalid")
    return dispatch, completed


def _delivery_claim(kind: ReleaseKind, row: ReleaseDeliveryRecord) -> ServerDeliveryClaim:
    dispatch, completed = delivery_times(row)
    return ServerDeliveryClaim(
        kind,
        row.proposal_id,
        row.operation,
        row.delivery_id,
        row.workflow_run_id,
        row.parent_delivery_id,
        cast(Literal["github", "render"], row.provider),
        cast(str, row.provider_resource_id),
        row.ai_run_id,
        row.binding_hash,
        hash_json(row.result_ref),
        row.status,
        dispatch,
        completed,
    )


def _require_delivery_action(row: ReleaseDeliveryRecord, action: LoadedActionLedger) -> None:
    claim = action.claim
    if row.ai_run_id != claim.ai_run_id or row.binding_hash != claim.binding_hash:
        conflict("delivery_action_binding_mismatch")
    if row.created_by != action.actor_user_id or row.request_id != action.request_id:
        conflict("delivery_action_principal_mismatch")
    dispatch, completed = delivery_times(row)
    if not (claim.started_at <= dispatch <= completed <= claim.completed_at):
        invalid("delivery_action_timestamp_invalid")


def _database_fingerprint(
    tenant_id: str,
    application_id: str,
    actions: Sequence[ServerActionClaim],
    results: Sequence[ServerActionResultClaim],
    deliveries: Sequence[ServerDeliveryClaim],
    records: Sequence[ReleaseDeliveryRecord],
    audits: Sequence[SelectedAuditEvidence],
) -> str:
    payload = {
        "tenantId": tenant_id,
        "applicationId": application_id,
        "actions": [_dated_dataclass(item) for item in actions],
        "actionResults": [asdict(item) for item in results],
        "deliveries": [_dated_dataclass(item) for item in deliveries],
        "deliveryRecords": [asdict(item) for item in records],
        "selectedAuditEvents": [_dated_dataclass(item) for item in audits],
    }
    return hash_json(payload)


def _dated_dataclass(
    value: ServerActionClaim | ServerDeliveryClaim | SelectedAuditEvidence,
) -> dict[str, object]:
    payload = asdict(value)
    for key, item in tuple(payload.items()):
        if isinstance(item, datetime):
            payload[key] = item.isoformat()
    return payload


__all__ = ["DELIVERY_TOOL", "database_snapshot", "delivery_claims", "delivery_times"]
