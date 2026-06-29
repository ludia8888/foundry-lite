from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports import ConnectorConnectionRow, ConnectorResourceRow, SourceConnectionRow
from foundry_lite.application.services.connector_onboarding_config import _resolved_config_fingerprint
from foundry_lite.application.services.media.transactions import MediaCommitResult
from foundry_lite.application.services.media.uploads import StagedUpload


class CommitPayloadResult(Protocol):
    @property
    def dataset_id(self) -> str: ...

    @property
    def dataset_ref(self) -> str: ...

    @property
    def transaction_id(self) -> str: ...

    @property
    def version_id(self) -> str: ...

    @property
    def version_number(self) -> int: ...

    @property
    def row_count(self) -> int: ...

    @property
    def manifest_uri(self) -> str: ...

    @property
    def schema_hash(self) -> str: ...


def source_view(row: SourceConnectionRow) -> dict[str, object]:
    return {
        "sourceName": row["source_name"],
        "displayName": row["display_name"],
        "kind": row["kind"],
        "targetDatasetRef": row["target_dataset_ref"],
        "targetMediaSetId": row["target_media_set_id"],
        "status": row["status"],
        "configSummary": dict(row["config_summary"]),
        "configFingerprint": row["config_fingerprint"],
        "lastRunId": row["last_run_id"],
        "lastWorkflowRunId": row["last_workflow_run_id"],
        "lastCommitRef": dict(row["last_commit_ref"] or {}),
        "operationsPath": operations_path(row["last_run_id"]),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def rest_source_view(
    connection: ConnectorConnectionRow,
    resources: Sequence[ConnectorResourceRow],
) -> dict[str, object]:
    resource_views = [_rest_resource_summary(connection, resource) for resource in resources]
    return {
        "sourceName": connection["connector_name"],
        "displayName": connection["display_name"],
        "kind": "rest",
        "targetDatasetRef": _first_dataset_ref(resources),
        "targetMediaSetId": None,
        "status": connection["status"],
        "configSummary": {
            "connectorName": connection["connector_name"],
            "baseUrl": connection["base_url"],
            "auth": dict(connection["auth"]),
            "rateLimitPerMinute": connection["rate_limit_per_minute"],
            "allowPrivateNetwork": connection["allow_private_network"],
            "resources": resource_views,
        },
        "configFingerprint": connection["config_fingerprint"],
        "lastRunId": None,
        "lastWorkflowRunId": None,
        "lastCommitRef": {},
        "operationsPath": None,
        "createdAt": connection["created_at"],
        "updatedAt": connection["updated_at"],
    }


def commit_payload(result: CommitPayloadResult, run_id: str | None) -> dict[str, object]:
    return {
        "datasetId": result.dataset_id,
        "datasetRef": result.dataset_ref,
        "transactionId": result.transaction_id,
        "versionId": result.version_id,
        "versionNumber": result.version_number,
        "rowCount": result.row_count,
        "manifestUri": result.manifest_uri,
        "schemaHash": result.schema_hash,
        "runId": run_id,
        "operationsPath": operations_path(run_id),
    }


def media_upload_payload(staged: StagedUpload, commit: MediaCommitResult) -> dict[str, object]:
    committed_ids = list(commit.committed_version_ids)
    return {
        "mediaTransactionId": commit.media_transaction_id,
        "mediaItemId": staged.media_item_id,
        "mediaItemVersionId": staged.media_item_version_id,
        "committedVersionIds": committed_ids,
        "headVersionIdByItem": dict(commit.head_version_id_by_item),
        "operationsPath": None,
    }


def operations_path(run_id: str | None) -> str | None:
    if run_id is None:
        return None
    return f"/api/operations/runs/sync/{run_id}"


def source_result(
    row: SourceConnectionRow,
    *,
    commit: Mapping[str, object] | None = None,
    commits: Sequence[Mapping[str, object]] = (),
    media_commit: Mapping[str, object] | None = None,
    workflow_run: Mapping[str, object] | None = None,
    test_result: Mapping[str, object] | None = None,
    is_replayed: bool = False,
) -> dict[str, object]:
    return {
        "source": source_view(row),
        "commitResult": dict(commit or {}),
        "commitResults": [dict(item) for item in commits],
        "mediaCommitResult": dict(media_commit or {}),
        "workflowRun": dict(workflow_run or {}),
        "testResult": dict(test_result or {}),
        "operationsPath": source_view(row)["operationsPath"],
        "isIdempotentReplay": is_replayed,
    }


def _rest_resource_summary(connection: ConnectorConnectionRow, resource: ConnectorResourceRow) -> dict[str, object]:
    return {
        "resourceName": resource["resource_name"],
        "datasetRef": resource["dataset_ref"],
        "resourcePath": resource["resource_path"],
        "configFingerprint": _resolved_config_fingerprint(connection, resource),
    }


def _first_dataset_ref(resources: Sequence[ConnectorResourceRow]) -> str | None:
    return resources[0]["dataset_ref"] if resources else None
