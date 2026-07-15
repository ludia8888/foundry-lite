"""Helper functions for Source management control-plane workflows."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Protocol, cast

from foundry_lite.application.ports import (
    RestAuthConfig,
    RestPaginationConfig,
    RestSourceConfig,
    SourceCredentialRecord,
    SourceNetworkPolicyRecord,
    SourceSyncRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.connector_adapter import RestPaginationStrategy
from foundry_lite.application.ports.secret_provider import SecretVault
from foundry_lite.application.ports.source_management_repository import SourceManagementRepository
from foundry_lite.application.services.source_management_views import (
    credential_view,
    network_policy_view,
    sync_view,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


class CommitResultLike(Protocol):
    @property
    def version_id(self) -> str: ...

    @property
    def row_count(self) -> int: ...

    @property
    def schema_hash(self) -> str: ...

    @property
    def manifest_uri(self) -> str: ...


class SourceBatchLike(Protocol):
    @property
    def checkpoint(self) -> Mapping[str, object]: ...

    @property
    def network_evidence(self) -> Mapping[str, object]: ...


class SourcePolicyBoundary(Protocol):
    def require(self, ctx: RequestContext, permission: str) -> None: ...


class SourceRuntimeBoundary(Protocol):
    def _require_write_traffic_open(
        self,
        ctx: RequestContext,
        *,
        operation: str,
        resource_type: str,
        resource_id: str,
    ) -> None: ...

    def _audit(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        *,
        event_type: str,
        resource_type: str,
        resource_id: str | None,
        action: str,
        decision: str = "allow",
        policy_decision: Mapping[str, object] | None = None,
        before_ref: Mapping[str, object] | None = None,
        after_ref: Mapping[str, object] | None = None,
        correlation_id: str | None = None,
    ) -> None: ...

    def _outbox(
        self,
        conn: TransactionContext,
        ctx: RequestContext,
        event_type: str,
        aggregate_type: str,
        aggregate_id: str,
        payload: Mapping[str, object],
        *,
        idempotency_key: str,
        correlation_id: str,
    ) -> str | None: ...


class SourceSyncStore(Protocol):
    engine: TransactionManager
    source_management_repository: SourceManagementRepository


class SourceManagementStore(SourceSyncStore, Protocol):
    secret_vault: SecretVault


def create_credential_row(
    service: SourceManagementStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    credential_name: str,
    secret_name: str,
    secret_value: str,
    record: SourceCredentialRecord,
) -> Mapping[str, object]:
    with service.engine.begin() as conn:
        existing = service.source_management_repository.credential_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, credential_name=credential_name
        )
        if existing is not None:
            require_same_fingerprint(existing, record.config_fingerprint)
            return existing
        service.secret_vault.put_secret(secret_name, secret_value, metadata={"credentialName": credential_name})
        service.source_management_repository.create_credential(transaction=conn, record=record)
        row = credential_row(service, conn, ctx, credential_name)
        audit(
            runtime_service,
            conn,
            ctx,
            "source.credential.created",
            "source_credential",
            credential_name,
            credential_view(row),
        )
        return row


def create_network_policy_row(
    service: SourceManagementStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    policy_name: str,
    mode: str,
    agent_id: str | None,
    record: SourceNetworkPolicyRecord,
) -> Mapping[str, object]:
    with service.engine.begin() as conn:
        require_agent_exists_for_proxy(service, conn, ctx, mode, agent_id)
        existing = service.source_management_repository.network_policy_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, policy_name=policy_name
        )
        if existing is not None:
            require_same_fingerprint(existing, record.config_fingerprint)
            return existing
        service.source_management_repository.create_network_policy(transaction=conn, record=record)
        row = network_policy_row(service, conn, ctx, policy_name)
        audit(
            runtime_service,
            conn,
            ctx,
            "source.network_policy.created",
            "source_network_policy",
            policy_name,
            network_policy_view(row),
        )
        return row


def create_sync_row(
    service: SourceManagementStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    sync_name: str,
    record: SourceSyncRecord,
) -> Mapping[str, object]:
    with service.engine.begin() as conn:
        existing = service.source_management_repository.sync_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, sync_name=sync_name
        )
        if existing is not None:
            require_same_fingerprint(existing, record.config_fingerprint)
            return existing
        service.source_management_repository.create_sync(transaction=conn, record=record)
        row = sync_row(service, conn, ctx, sync_name)
        audit(runtime_service, conn, ctx, "source.sync.created", "source_sync", sync_name, sync_view(row))
        return row


def credential_row(
    service: SourceManagementStore, conn: TransactionContext, ctx: RequestContext, credential_name: str
) -> Mapping[str, object]:
    row = service.source_management_repository.credential_by_name(
        transaction=conn, tenant_id=ctx.tenant_id, credential_name=credential_name
    )
    if row is None:
        raise NotFound("source credential not found", details={"credential_name": credential_name})
    return row


def agent_row(
    service: SourceManagementStore, conn: TransactionContext, ctx: RequestContext, agent_id: str
) -> Mapping[str, object]:
    row = service.source_management_repository.agent_by_id(transaction=conn, tenant_id=ctx.tenant_id, agent_id=agent_id)
    if row is None:
        raise NotFound("source agent not found", details={"agent_id": agent_id})
    return row


def network_policy_row(
    service: SourceManagementStore, conn: TransactionContext, ctx: RequestContext, policy_name: str
) -> Mapping[str, object]:
    row = service.source_management_repository.network_policy_by_name(
        transaction=conn, tenant_id=ctx.tenant_id, policy_name=policy_name
    )
    if row is None:
        raise NotFound("source network policy not found", details={"policy_name": policy_name})
    return row


def sync_row(
    service: SourceSyncStore, conn: TransactionContext, ctx: RequestContext, sync_name: str
) -> Mapping[str, object]:
    row = service.source_management_repository.sync_by_name(
        transaction=conn, tenant_id=ctx.tenant_id, sync_name=sync_name
    )
    if row is None:
        raise NotFound("source managed sync not found", details={"sync_name": sync_name})
    return row


def run_row(
    service: SourceManagementStore, conn: TransactionContext, ctx: RequestContext, run_id: str
) -> Mapping[str, object]:
    row = service.source_management_repository.sync_run_by_id(transaction=conn, tenant_id=ctx.tenant_id, run_id=run_id)
    if row is None:
        raise NotFound("source sync run not found", details={"run_id": run_id})
    return row


def require_write(
    policy: SourcePolicyBoundary,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    operation: str,
    resource_id: str,
) -> None:
    policy.require(ctx, "source:write")
    runtime_service._require_write_traffic_open(
        ctx, operation=operation, resource_type="source", resource_id=resource_id
    )


def require_same_fingerprint(existing: Mapping[str, object], expected: str) -> None:
    if existing["config_fingerprint"] != expected:
        raise ConflictDetected("source management record already exists with different config")


def require_agent_exists_for_proxy(
    service: SourceManagementStore,
    conn: TransactionContext,
    ctx: RequestContext,
    mode: str,
    agent_id: str | None,
) -> None:
    if mode != "agent_proxy" or agent_id is None:
        return
    agent_row(service, conn, ctx, agent_id)


def audit(
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    event_type: str,
    resource_type: str,
    resource_id: str,
    after_ref: Mapping[str, object],
) -> None:
    runtime_service._audit(
        conn,
        ctx,
        event_type=event_type,
        resource_type=resource_type,
        resource_id=resource_id,
        action="source_manage",
        after_ref=after_ref,
    )


def rest_source_config(request: Mapping[str, object]) -> RestSourceConfig:
    pagination = mapping(request.get("pagination"))
    return RestSourceConfig(
        base_url=required_text(request, "baseUrl"),
        resource_path=required_text(request, "resourcePath"),
        auth=rest_auth(mapping(request.get("auth"))),
        pagination=RestPaginationConfig(
            items_path=str(pagination.get("itemsPath", "items")),
            next_cursor_path=str(pagination.get("nextCursorPath", "nextCursor")),
            cursor_query_param=str(pagination.get("cursorQueryParam", "cursor")),
            cursor_key=str(pagination.get("cursorKey", "cursor")),
            strategy=cast(RestPaginationStrategy, pagination.get("strategy", "cursor")),
            max_pages_per_snapshot=int_value(pagination.get("maxPagesPerSnapshot"), 1),
        ),
        allow_private_network=bool(request.get("allowPrivateNetwork", False)),
    )


def rest_auth(auth: Mapping[str, object]) -> RestAuthConfig:
    mode = str(auth.get("mode", "none"))
    if mode == "bearer":
        return RestAuthConfig(mode="bearer", token_secret_ref=required_text(auth, "tokenSecretRef"))
    if mode == "basic":
        return RestAuthConfig(
            mode="basic",
            basic_credentials_secret_ref=required_text(auth, "basicCredentialsSecretRef"),
        )
    if mode == "header":
        return RestAuthConfig(
            mode="header",
            header_name=required_text(auth, "headerName"),
            header_value_secret_ref=required_text(auth, "headerValueSecretRef"),
        )
    return RestAuthConfig(mode="none")


def commit_result_payload(commit: CommitResultLike | None, batch: SourceBatchLike) -> dict[str, object]:
    network_evidence = dict(getattr(batch, "network_evidence", {}))
    if commit is None:
        return {
            "rowCount": 0,
            "checkpoint": dict(getattr(batch, "checkpoint", {})),
            "networkEvidence": network_evidence,
        }
    return {
        "datasetVersionId": commit.version_id,
        "rowCount": commit.row_count,
        "schemaHash": commit.schema_hash,
        "manifestUri": commit.manifest_uri,
        "checkpoint": dict(getattr(batch, "checkpoint", {})),
        "networkEvidence": network_evidence,
    }


def int_value(value: object, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value)
    raise ValidationFailed("expected integer value", details={"valueType": type(value).__name__})


def target_dataset_ref(sync: Mapping[str, object]) -> str:
    value = sync.get("target_dataset_ref")
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("database managed sync requires targetDatasetRef")


def fieldnames(rows: Sequence[Mapping[str, object]], schema: Mapping[str, object]) -> list[str]:
    columns = schema.get("columns")
    if isinstance(columns, Sequence) and not isinstance(columns, str):
        names = [str(column["name"]) for column in columns if isinstance(column, Mapping) and "name" in column]
        if names:
            return names
    return [str(key) for key in rows[0]] if rows else []


def secret_version(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"sha256:{digest[:12]}"


def mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def required_text(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if isinstance(value, str) and value:
        return value
    raise ValidationFailed("source request is missing required field", details={"field": field})


def text(payload: Mapping[str, object], field: str, default: str) -> str:
    value = payload.get(field)
    return value if isinstance(value, str) and value else default


def optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def now() -> str:
    return datetime.now().astimezone().isoformat()
