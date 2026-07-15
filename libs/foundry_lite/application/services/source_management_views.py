"""Application service helpers for source management views workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime

_AGENT_HEARTBEAT_MAX_AGE_SECONDS = 90


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
        "status": _agent_status(row),
        "capabilities": dict(_mapping(row["capabilities"])),
        "networkSummary": dict(_mapping(row["network_summary"])),
        "lastHeartbeatAt": row["last_heartbeat_at"],
        "configFingerprint": row["config_fingerprint"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _agent_status(row: Mapping[str, object]) -> object:
    if row.get("status") != "online":
        return row.get("status")
    heartbeat = row.get("last_heartbeat_at")
    if not isinstance(heartbeat, str):
        return "offline"
    try:
        timestamp = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
    except ValueError:
        return "offline"
    age_seconds = (datetime.now(UTC) - timestamp.astimezone(UTC)).total_seconds()
    return "online" if age_seconds <= _AGENT_HEARTBEAT_MAX_AGE_SECONDS else "offline"


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
    result_summary = dict(_mapping(row["result_summary"]))
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
        "resultSummary": result_summary,
        "networkEvidence": _sync_run_network_evidence(result_summary),
        "error": dict(_mapping(row["error"])) if isinstance(row.get("error"), Mapping) else None,
        "operationsPath": _sync_run_operations_path(row, result_summary),
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "createdAt": row["created_at"],
    }


def sync_run_list_view(rows: Sequence[Mapping[str, object]]) -> list[dict[str, object]]:
    return [sync_run_view(row) for row in rows]


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _sync_run_network_evidence(result_summary: Mapping[str, object]) -> dict[str, object] | None:
    direct_evidence = _mapping(result_summary.get("networkEvidence"))
    if direct_evidence:
        return dict(direct_evidence)
    workflow_run = _mapping(result_summary.get("workflowRun"))
    output = _mapping(workflow_run.get("output"))
    evidence = _mapping(output.get("networkEvidence"))
    return dict(evidence) if evidence else None


def _sync_run_operations_path(row: Mapping[str, object], result_summary: Mapping[str, object]) -> str | None:
    workflow = _mapping(result_summary.get("workflowRun"))
    workflow_path = workflow.get("operationPath")
    if isinstance(workflow_path, str) and workflow_path:
        return workflow_path
    stored_path = row.get("operations_path")
    if isinstance(stored_path, str) and stored_path.startswith("/operations/source-sync-runs/"):
        return None
    return stored_path if isinstance(stored_path, str) else None
