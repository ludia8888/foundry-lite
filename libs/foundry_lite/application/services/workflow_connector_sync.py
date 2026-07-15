"""Application service helpers for workflow connector sync workflows."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.primitives import CommitResult, _json_hash

CONNECTOR_SYNC_OUTBOX_EVENT_TYPE = "dataset.version.committed"


def connector_sync_run_id(
    *,
    tenant_id: str,
    idempotency_key: str,
    dataset_ref: str,
    connector_name: str,
    resource_name: str,
    sync_name: str | None,
    source_name: str | None,
    config_fingerprint: str | None,
    transaction_type: str,
) -> str:
    identity = {
        "tenantId": tenant_id,
        "idempotencyKey": idempotency_key,
        "datasetRef": dataset_ref,
        "connectorName": connector_name,
        "resourceName": resource_name,
        "syncName": sync_name,
        "configFingerprint": config_fingerprint,
        "transactionType": transaction_type,
    }
    if source_name is not None:
        identity["sourceName"] = source_name
    digest = _json_hash(identity)
    return f"workflow_sync_{digest[:32]}"


def connector_sync_workflow_output(
    result: CommitResult,
    *,
    payload: Mapping[str, object],
    transaction_metadata: Mapping[str, object],
) -> Mapping[str, object]:
    sync_run_id = _optional_text(payload, "syncRunId")
    cursor = connector_cursor_evidence(transaction_metadata)
    network_evidence = connector_network_evidence(transaction_metadata)
    return {
        "workflowKind": "connector_sync",
        "workflowDefinitionVersion": _optional_text(payload, "workflowDefinitionVersion"),
        "datasetRef": result.dataset_ref,
        "connectorName": payload.get("connectorName"),
        "resourceName": payload.get("resourceName"),
        "configFingerprint": payload.get("configFingerprint"),
        "syncRunId": sync_run_id,
        "transactionId": result.transaction_id,
        "committedVersionId": result.version_id,
        "versionNumber": result.version_number,
        "rowCount": result.row_count,
        "networkEvidence": network_evidence,
        "dataPlane": _data_plane_payload(result, sync_run_id, cursor, network_evidence),
    }


def connector_cursor_evidence(transaction_metadata: Mapping[str, object]) -> Mapping[str, object]:
    cursor = transaction_metadata.get("connectorCursor")
    if not isinstance(cursor, Mapping):
        return {"source": "dataset_transaction.metadata", "requestCursor": None, "nextCursor": None}
    return {
        "source": "dataset_transaction.metadata",
        "connectorName": cursor.get("connectorName"),
        "resourceName": cursor.get("resourceName"),
        "requestCursor": _optional_mapping(cursor.get("requestCursor")),
        "nextCursor": _optional_mapping(cursor.get("nextCursor")),
    }


def connector_network_evidence(transaction_metadata: Mapping[str, object]) -> Mapping[str, object]:
    value = transaction_metadata.get("connectorNetworkEvidence")
    if not isinstance(value, Mapping) or not value:
        return {}
    return {"source": "dataset_transaction.metadata", **dict(value)}


def _data_plane_payload(
    result: CommitResult,
    sync_run_id: str | None,
    cursor: Mapping[str, object],
    network_evidence: Mapping[str, object],
) -> Mapping[str, object]:
    return {
        "stages": [
            _stage("fetchPage", connectorCursor=cursor.get("requestCursor")),
            _stage("rawStagingWrite", syncRunId=sync_run_id, transactionId=result.transaction_id),
            _stage("qualityCheck", schemaHash=result.schema_hash),
            _stage("datasetCommit", datasetVersionId=result.version_id, rowCount=result.row_count),
            _stage("cursorAdvance", nextCursor=cursor.get("nextCursor")),
            _stage("eventEmit", eventType=CONNECTOR_SYNC_OUTBOX_EVENT_TYPE),
        ],
        "cursor": cursor,
        "networkEvidence": network_evidence,
        "outboxEventType": CONNECTOR_SYNC_OUTBOX_EVENT_TYPE,
    }


def _stage(name: str, **evidence: object) -> Mapping[str, object]:
    return {"name": name, "status": "SUCCEEDED", **evidence}


def _optional_mapping(value: object) -> Mapping[str, object] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _optional_text(payload: Mapping[str, object], key: str) -> str | None:
    value = payload.get(key)
    return value if isinstance(value, str) and value else None
