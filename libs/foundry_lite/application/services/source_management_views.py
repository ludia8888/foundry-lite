from __future__ import annotations

from collections.abc import Mapping, Sequence


def credential_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "credentialName": row["credential_name"],
        "displayName": row["display_name"],
        "kind": row["kind"],
        "authScheme": row["auth_scheme"],
        "secretRef": {"name": row["secret_name"], "version": row["secret_version"], "value": "***REDACTED***"},
        "status": row["status"],
        "configFingerprint": row["config_fingerprint"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def agent_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "agentId": row["agent_id"],
        "displayName": row["display_name"],
        "mode": row["mode"],
        "status": row["status"],
        "capabilities": dict(_mapping(row["capabilities"])),
        "networkSummary": dict(_mapping(row["network_summary"])),
        "lastHeartbeatAt": row["last_heartbeat_at"],
        "configFingerprint": row["config_fingerprint"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def network_policy_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "policyName": row["policy_name"],
        "displayName": row["display_name"],
        "mode": row["mode"],
        "agentId": row["agent_id"],
        "allowedHosts": dict(_mapping(row["allowed_hosts"])),
        "status": row["status"],
        "configFingerprint": row["config_fingerprint"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def exploration_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "explorationRunId": row["id"],
        "sourceName": row["source_name"],
        "sourceType": row["source_type"],
        "status": row["status"],
        "resultSummary": dict(_mapping(row["result_summary"])),
        "error": dict(_mapping(row["error"])) if isinstance(row.get("error"), Mapping) else None,
        "operationsPath": row["operations_path"],
        "createdAt": row["created_at"],
    }


def sync_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "syncName": row["sync_name"],
        "sourceName": row["source_name"],
        "displayName": row["display_name"],
        "sourceType": row["source_type"],
        "capability": row["capability"],
        "targetDatasetRef": row["target_dataset_ref"],
        "targetMediaSetId": row["target_media_set_id"],
        "mode": row["mode"],
        "schedule": dict(_mapping(row["schedule"])),
        "configSummary": dict(_mapping(row["config_summary"])),
        "configFingerprint": row["config_fingerprint"],
        "status": row["status"],
        "lastRunId": row["last_run_id"],
        "lastWorkflowRunId": row["last_workflow_run_id"],
        "checkpoint": dict(_mapping(row["checkpoint"])),
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def sync_run_view(row: Mapping[str, object]) -> dict[str, object]:
    return {
        "runId": row["id"],
        "syncName": row["sync_name"],
        "sourceName": row["source_name"],
        "sourceType": row["source_type"],
        "capability": row["capability"],
        "workflowRunId": row["workflow_run_id"],
        "datasetVersionId": row["dataset_version_id"],
        "status": row["status"],
        "triggerType": row["trigger_type"],
        "batchLimit": row["batch_limit"],
        "checkpointStart": dict(_mapping(row["checkpoint_start"])),
        "checkpointEnd": dict(_mapping(row["checkpoint_end"])),
        "resultSummary": dict(_mapping(row["result_summary"])),
        "error": dict(_mapping(row["error"])) if isinstance(row.get("error"), Mapping) else None,
        "operationsPath": row["operations_path"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "createdAt": row["created_at"],
    }


def sync_run_list_view(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [sync_run_view(row) for row in rows]


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}
