"""Application service helpers for connector onboarding views workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import (
    ConnectorConnectionRow,
    ConnectorResourceRow,
    DatasetRow,
)
from foundry_lite.application.primitives import CommitResult
from foundry_lite.application.services.connector_onboarding_config import (
    ConnectorBundle,
    _resolved_config_fingerprint,
)
from foundry_lite.domain.errors import ConflictDetected, FoundryLiteError


def _connection_view(
    connection: ConnectorConnectionRow,
    resources: Sequence[ConnectorResourceRow],
) -> dict[str, object]:
    return {
        "connectorName": connection["connector_name"],
        "displayName": connection["display_name"],
        "kind": connection["kind"],
        "baseUrl": connection["base_url"],
        "auth": dict(connection["auth"]),
        "rateLimitPerMinute": connection["rate_limit_per_minute"],
        "allowPrivateNetwork": connection["allow_private_network"],
        "status": connection["status"],
        "configFingerprint": connection["config_fingerprint"],
        "resources": [_resource_view(connection, resource) for resource in resources],
        "createdAt": connection["created_at"],
        "updatedAt": connection["updated_at"],
    }


def _resource_view(connection: ConnectorConnectionRow, resource: ConnectorResourceRow) -> dict[str, object]:
    return {
        "connectorName": resource["connector_name"],
        "resourceName": resource["resource_name"],
        "datasetRef": resource["dataset_ref"],
        "resourcePath": resource["resource_path"],
        "pagination": dict(resource["pagination"]),
        "schemaColumns": list(resource["schema_columns"]),
        "primaryKey": list(resource["primary_key"]),
        "configFingerprint": _resolved_config_fingerprint(connection, resource),
        "createdAt": resource["created_at"],
        "updatedAt": resource["updated_at"],
    }


def _test_result(
    bundle: ConnectorBundle,
    *,
    status: str,
    rows: Sequence[Mapping[str, object]] = (),
    schema: Mapping[str, object] | None = None,
    cursor: Mapping[str, object] | None = None,
    error: Mapping[str, object] | None = None,
) -> dict[str, object]:
    return {
        "status": status,
        "connectorName": bundle.connection["connector_name"],
        "resourceName": bundle.resource["resource_name"],
        "datasetRef": bundle.resource["dataset_ref"],
        "configFingerprint": bundle.config_fingerprint,
        "rowCount": len(rows),
        "sampleRows": [dict(row) for row in rows[:5]],
        "schema": dict(schema or {}),
        "cursor": dict(cursor or {}),
        "error": dict(error or {}),
    }


def _activity_result(
    result: CommitResult,
    connector_name: str,
    resource_name: str,
    config_fingerprint: str,
) -> dict[str, object]:
    return {
        "workflowKind": "connector_sync",
        "connectorName": connector_name,
        "resourceName": resource_name,
        "datasetRef": result.dataset_ref,
        "committedVersionId": result.version_id,
        "versionNumber": result.version_number,
        "rowCount": result.row_count,
        "configFingerprint": config_fingerprint,
    }


def _require_compatible_primary_key(dataset: DatasetRow, primary_key: Sequence[str]) -> None:
    requested = list(primary_key)
    existing = list(dataset["primary_key"])
    if requested and existing and requested != existing:
        raise ConflictDetected(
            "connector resource primary key does not match existing dataset",
            details={
                "dataset_ref": f"{dataset['namespace']}.{dataset['name']}",
                "existing": existing,
                "requested": requested,
            },
        )


def _require_same_config(existing: str, requested: str) -> None:
    if existing != requested:
        raise ConflictDetected("connector connection already exists with different config")


def _error_payload(exc: FoundryLiteError) -> dict[str, object]:
    return {"type": exc.code, "message": exc.message, "details": dict(exc.details)}


def _connection_audit_ref(row: ConnectorConnectionRow, idempotency_key: str) -> dict[str, object]:
    return {
        "connectorName": row["connector_name"],
        "kind": row["kind"],
        "baseUrl": row["base_url"],
        "auth": dict(row["auth"]),
        "configFingerprint": row["config_fingerprint"],
        "idempotencyKey": idempotency_key,
    }
