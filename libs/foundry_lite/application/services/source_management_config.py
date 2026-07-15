"""Application service helpers for source management config workflows."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import cast

from foundry_lite.application.ports import (
    SourceAgentRecord,
    SourceCredentialRecord,
    SourceExplorationRunRecord,
    SourceNetworkPolicyRecord,
    SourceSyncRecord,
    SourceSyncRunRecord,
)
from foundry_lite.application.primitives import _dataset_ref_parts, _json_hash, _new_id, _now
from foundry_lite.application.services.source_onboarding_config import (
    reject_raw_secret_payload,
    require_identifier,
    require_text,
)
from foundry_lite.application.services.source_scheduler_config import (
    normalized_source_schedule as normalized_source_schedule,
)
from foundry_lite.application.services.source_scheduler_config import (
    resumed_source_schedule as resumed_source_schedule,
)
from foundry_lite.application.services.source_streaming_config import validate_streaming_sync_config
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

SOURCE_TEMPLATES: tuple[dict[str, object], ...] = (
    {
        "sourceType": "kafka",
        "displayName": "Apache Kafka",
        "category": "protocol",
        "description": "Kafka 토픽을 탐색하고 체크포인트 기반 Streaming Sync로 원본 이벤트를 보관합니다.",
        "isRecommended": True,
        "executionStatus": "active",
        "capabilities": ["streaming", "exploration"],
        "credentialModes": ["none", "credential_ref"],
        "networkModes": ["direct"],
        "supportsExploration": True,
        "managedRunModes": ["manual", "scheduled"],
    },
    {
        "sourceType": "rest_api",
        "displayName": "REST API",
        "category": "protocol",
        "description": "REST endpoint에 요청을 보내고 응답 데이터를 동기화하거나 코드에서 사용합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["batch", "exploration", "code_use"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct", "agent_proxy"],
        "supportsExploration": True,
        "managedRunModes": ["manual", "scheduled"],
    },
    {
        "sourceType": "postgres_jdbc",
        "displayName": "Postgres Database",
        "category": "protocol",
        "description": "PostgreSQL 테이블을 탐색하고 배치 동기화 또는 CDC로 가져옵니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["batch", "exploration", "cdc"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct", "agent_proxy"],
        "supportsExploration": True,
        "managedRunModes": ["manual", "scheduled"],
    },
    {
        "sourceType": "sap_odata",
        "displayName": "SAP OData",
        "category": "protocol",
        "description": "SAP OData 서비스의 엔터티를 탐색하고 Foundry로 동기화합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["batch", "exploration"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct", "agent_proxy"],
        "supportsExploration": True,
        "managedRunModes": ["manual", "scheduled"],
    },
    {
        "sourceType": "sharepoint_graph",
        "displayName": "SharePoint Graph",
        "category": "protocol",
        "description": "SharePoint Online의 파일과 미디어를 탐색해 안전하게 동기화합니다.",
        "isRecommended": False,
        "executionStatus": "definition_only",
        "capabilities": ["batch_file", "media", "exploration"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct", "agent_proxy"],
        "supportsExploration": True,
        "managedRunModes": ["definition_only"],
    },
    {
        "sourceType": "webhook_listener",
        "displayName": "Inbound Webhook",
        "category": "protocol",
        "description": "외부 시스템이 보내는 이벤트를 서명 검증 후 스트림으로 수신합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["streaming"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct"],
        "supportsExploration": False,
        "managedRunModes": ["manual", "scheduled_future"],
    },
    {
        "sourceType": "debezium_cdc",
        "displayName": "Debezium CDC",
        "category": "protocol",
        "description": "데이터베이스 변경 로그를 지속 수집해 CDC 스트림과 객체 색인을 갱신합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["cdc", "streaming"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["agent_proxy", "direct"],
        "supportsExploration": False,
        "managedRunModes": ["manual", "scheduled_future"],
    },
    {
        "sourceType": "media_upload",
        "displayName": "Media Upload",
        "category": "alternative",
        "description": "컴퓨터의 이미지, 문서, 영상을 미디어셋으로 업로드합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["media"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct"],
        "supportsExploration": False,
        "managedRunModes": ["manual", "scheduled_future"],
    },
    {
        "sourceType": "batch_file",
        "displayName": "Batch File Upload",
        "category": "alternative",
        "description": "여러 파일을 한 배치로 업로드해 데이터셋 버전으로 커밋합니다.",
        "isRecommended": False,
        "executionStatus": "active",
        "capabilities": ["batch"],
        "credentialModes": ["credential_ref", "oauth_future"],
        "networkModes": ["direct"],
        "supportsExploration": False,
        "managedRunModes": ["manual", "scheduled_future"],
    },
)


def credential_record(
    ctx: RequestContext,
    *,
    credential_name: str,
    display_name: str,
    kind: str,
    auth_scheme: str,
    secret_name: str,
    secret_version: str,
) -> SourceCredentialRecord:
    now = _now()
    config = _credential_config(credential_name, display_name, kind, auth_scheme, secret_name, secret_version)
    return SourceCredentialRecord(
        id=_new_id("source_credential"),
        tenant_id=ctx.tenant_id,
        credential_name=credential_name,
        display_name=display_name,
        kind=kind,
        auth_scheme=auth_scheme,
        secret_name=secret_name,
        secret_version=secret_version,
        status="active",
        config_summary=config,
        config_fingerprint=fingerprint(config),
        created_at=now,
        updated_at=now,
    )


def agent_record(
    ctx: RequestContext,
    *,
    agent_id: str,
    display_name: str,
    mode: str,
    capabilities: Mapping[str, object],
    network_summary: Mapping[str, object],
) -> SourceAgentRecord:
    now = _now()
    config = _agent_config(agent_id, display_name, mode, capabilities, network_summary)
    return SourceAgentRecord(
        id=_new_id("source_agent"),
        tenant_id=ctx.tenant_id,
        agent_id=agent_id,
        display_name=display_name,
        mode=mode,
        status="registered",
        capabilities=cast(Mapping[str, object], config["capabilities"]),
        network_summary=cast(Mapping[str, object], config["networkSummary"]),
        last_heartbeat_at=None,
        config_fingerprint=fingerprint(config),
        created_at=now,
        updated_at=now,
    )


def network_policy_record(
    ctx: RequestContext,
    *,
    policy_name: str,
    display_name: str,
    mode: str,
    agent_id: str | None,
    allowed_hosts: Sequence[str],
) -> SourceNetworkPolicyRecord:
    now = _now()
    config = _network_policy_config(policy_name, display_name, mode, agent_id, allowed_hosts)
    return SourceNetworkPolicyRecord(
        id=_new_id("source_network"),
        tenant_id=ctx.tenant_id,
        policy_name=policy_name,
        display_name=display_name,
        mode=mode,
        agent_id=agent_id,
        allowed_hosts=cast(Mapping[str, object], config["allowedHosts"]),
        status="active",
        config_fingerprint=fingerprint(config),
        created_at=now,
        updated_at=now,
    )


def exploration_run_record(
    ctx: RequestContext,
    *,
    source_name: str,
    source_type: str,
    request: Mapping[str, object],
    result_summary: Mapping[str, object],
    status: str,
    error: Mapping[str, object] | None = None,
) -> SourceExplorationRunRecord:
    run_id = _new_id("source_explore")
    return SourceExplorationRunRecord(
        id=run_id,
        tenant_id=ctx.tenant_id,
        source_name=source_name,
        source_type=source_type,
        request=dict(request),
        status=status,
        result_summary=dict(result_summary),
        error=error,
        operations_path=f"/operations/source-exploration/{run_id}",
        created_at=_now(),
    )


def source_sync_record(
    ctx: RequestContext,
    *,
    sync_name: str,
    source_name: str,
    display_name: str,
    source_type: str,
    capability: str,
    target_dataset_ref: str | None,
    target_media_set_id: str | None,
    mode: str,
    schedule: Mapping[str, object],
    config_summary: Mapping[str, object],
) -> SourceSyncRecord:
    now = _now()
    config = sync_config(sync_name, source_name, source_type, capability, mode, schedule, config_summary)
    _validate_sync_target(target_dataset_ref, target_media_set_id)
    return SourceSyncRecord(
        id=_new_id("source_sync"),
        tenant_id=ctx.tenant_id,
        sync_name=sync_name,
        source_name=source_name,
        display_name=display_name,
        source_type=source_type,
        capability=capability,
        target_dataset_ref=target_dataset_ref,
        target_media_set_id=target_media_set_id,
        mode=mode,
        schedule=dict(schedule),
        config_summary=cast(Mapping[str, object], config["configSummary"]),
        config_fingerprint=fingerprint(config),
        status="active",
        last_run_id=None,
        last_workflow_run_id=None,
        checkpoint={},
        created_at=now,
        updated_at=now,
    )


def source_sync_schedule_fingerprint(
    sync: Mapping[str, object], schedule: Mapping[str, object], *, status: str | None = None
) -> str:
    config = sync_config(
        str(sync["sync_name"]),
        str(sync["source_name"]),
        str(sync["source_type"]),
        str(sync["capability"]),
        str(sync["mode"]),
        schedule,
        cast(Mapping[str, object], sync["config_summary"]),
    )
    config["operationalStatus"] = status or str(sync.get("status") or "active")
    return fingerprint(config)


def source_sync_run_record(
    ctx: RequestContext,
    sync: Mapping[str, object],
    *,
    trigger_type: str,
    idempotency_key: str,
    batch_limit: int | None,
    checkpoint_start: Mapping[str, object],
) -> SourceSyncRunRecord:
    run_id = _new_id("source_sync_run")
    now = _now()
    return SourceSyncRunRecord(
        id=run_id,
        tenant_id=ctx.tenant_id,
        sync_name=str(sync["sync_name"]),
        source_name=str(sync["source_name"]),
        source_type=str(sync["source_type"]),
        capability=str(sync["capability"]),
        workflow_run_id=None,
        dataset_version_id=None,
        status="running",
        trigger_type=trigger_type,
        idempotency_key=idempotency_key,
        batch_limit=batch_limit,
        checkpoint_start=dict(checkpoint_start),
        checkpoint_end={},
        result_summary={},
        error=None,
        operations_path=f"/operations/source-sync-runs/{run_id}",
        started_at=now,
        completed_at=None,
        created_at=now,
    )


def sync_config(
    sync_name: str,
    source_name: str,
    source_type: str,
    capability: str,
    mode: str,
    schedule: Mapping[str, object],
    config_summary: Mapping[str, object],
) -> dict[str, object]:
    require_identifier(sync_name, "syncName")
    require_identifier(source_name, "sourceName")
    _source_type(source_type)
    _sync_capability(capability)
    _sync_mode(mode)
    reject_raw_secret_payload(config_summary)
    validate_streaming_sync_config(source_type, capability, config_summary)
    return {
        "syncName": sync_name,
        "sourceName": source_name,
        "sourceType": source_type,
        "capability": capability,
        "mode": mode,
        "schedule": dict(schedule),
        "configSummary": dict(config_summary),
    }


def fingerprint(value: Mapping[str, object]) -> str:
    return f"sha256:{_json_hash(value)}"


def require_idempotency_key(value: str) -> None:
    if value.strip():
        return
    raise ValidationFailed("Idempotency-Key is required")


def _credential_config(
    credential_name: str,
    display_name: str,
    kind: str,
    auth_scheme: str,
    secret_name: str,
    secret_version: str,
) -> dict[str, object]:
    require_identifier(credential_name, "credentialName")
    require_text(display_name, "displayName")
    require_text(secret_name, "secretName")
    return {
        "credentialName": credential_name,
        "displayName": display_name,
        "kind": _source_type(kind),
        "authScheme": auth_scheme,
        "secretRef": {"name": secret_name, "version": secret_version},
    }


def _agent_config(
    agent_id: str,
    display_name: str,
    mode: str,
    capabilities: Mapping[str, object],
    network_summary: Mapping[str, object],
) -> dict[str, object]:
    require_identifier(agent_id, "agentId")
    require_text(display_name, "displayName")
    if mode not in {"agent_proxy", "legacy_agent", "direct"}:
        raise ValidationFailed("unsupported source agent mode", details={"mode": mode})
    return {
        "agentId": agent_id,
        "displayName": display_name,
        "mode": mode,
        "capabilities": dict(capabilities),
        "networkSummary": dict(network_summary),
    }


def _network_policy_config(
    policy_name: str,
    display_name: str,
    mode: str,
    agent_id: str | None,
    allowed_hosts: Sequence[str],
) -> dict[str, object]:
    require_identifier(policy_name, "policyName")
    require_text(display_name, "displayName")
    if mode not in {"direct", "agent_proxy"}:
        raise ValidationFailed("unsupported source network policy mode", details={"mode": mode})
    if mode == "agent_proxy" and not agent_id:
        raise ValidationFailed("agent proxy network policy requires agentId")
    return {
        "policyName": policy_name,
        "displayName": display_name,
        "mode": mode,
        "agentId": agent_id,
        "allowedHosts": {"hosts": list(allowed_hosts)},
    }


def _validate_sync_target(target_dataset_ref: str | None, target_media_set_id: str | None) -> None:
    if target_dataset_ref is not None:
        _dataset_ref_parts(target_dataset_ref)
    if target_dataset_ref is None and target_media_set_id is None:
        raise ValidationFailed("managed sync requires a dataset or media set target")


def _source_type(value: str) -> str:
    if value in {
        "rest_api",
        "postgres_jdbc",
        "sap_odata",
        "sharepoint_graph",
        "webhook_listener",
        "debezium_cdc",
        "kafka",
        "media_upload",
        "batch_file",
    }:
        return value
    raise ValidationFailed("unsupported source type", details={"sourceType": value})


def _sync_capability(value: str) -> str:
    if value in {"batch", "streaming", "cdc", "media", "exploration", "code_use"}:
        return value
    raise ValidationFailed("unsupported source sync capability", details={"capability": value})


def _sync_mode(value: str) -> str:
    if value in {"SNAPSHOT", "APPEND", "UPDATE", "TRAILING_WINDOW"}:
        return value
    raise ValidationFailed("unsupported source sync mode", details={"mode": value})
