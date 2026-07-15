"""Durable Source registration for managed database and REST syncs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.ports import (
    SourceConnectionAlreadyExistsError,
    SourceConnectionRecord,
    SourceConnectionRow,
    SourceRegistryRepository,
    SourceSyncRecord,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.resource_catalog_repository import ResourceCatalogRepository
from foundry_lite.application.ports.source_registry_repository import SourceConnectionKind
from foundry_lite.application.services.dataset.registry import DatasetRegistryService
from foundry_lite.application.services.resource_catalog_auto_registration import upsert_source_resource
from foundry_lite.application.services.source_management_config import source_sync_record
from foundry_lite.application.services.source_management_helpers import SourceRuntimeBoundary
from foundry_lite.application.services.source_onboarding_config import source_record
from foundry_lite.application.services.source_onboarding_helpers import source_row_in_tx
from foundry_lite.application.services.source_onboarding_views import source_view
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed

_CONNECTION_CONFIG_FIELDS = frozenset(
    {
        "agentId",
        "allowPrivateNetwork",
        "authScheme",
        "baseUrl",
        "bootstrapServers",
        "connectorName",
        "connectionMode",
        "credentialName",
        "credentialRef",
        "databaseUrlSecretRef",
        "isTlsEnabled",
        "networkPolicyName",
        "rateLimitPerMinute",
    }
)


class ManagedSourceRegistryStore(Protocol):
    @property
    def engine(self) -> TransactionManager: ...

    @property
    def source_registry_repository(self) -> SourceRegistryRepository: ...

    @property
    def resource_catalog_repository(self) -> ResourceCatalogRepository: ...


@dataclass(frozen=True)
class ManagedSyncPreparationDependencies:
    engine: TransactionManager
    source_registry_repository: SourceRegistryRepository
    resource_catalog_repository: ResourceCatalogRepository
    dataset_registry_service: DatasetRegistryService


def ensure_managed_source(
    service: ManagedSourceRegistryStore,
    runtime_service: SourceRuntimeBoundary,
    ctx: RequestContext,
    *,
    source_name: str,
    display_name: str,
    source_type: str,
    config_summary: Mapping[str, object],
) -> SourceConnectionRow:
    record = source_record(
        ctx,
        source_name=source_name,
        display_name=display_name,
        kind=_managed_source_kind(source_type),
        target_dataset_ref=None,
        target_media_set_id=None,
        config_summary=_connection_config(config_summary),
    )
    with service.engine.begin() as conn:
        existing = service.source_registry_repository.source_by_name(
            transaction=conn, tenant_id=ctx.tenant_id, source_name=source_name
        )
        if existing is not None:
            _require_compatible_source(existing, record.kind, record.config_summary)
            return existing
        return _create_managed_source(service, runtime_service, conn, ctx, record)


def _create_managed_source(
    service: ManagedSourceRegistryStore,
    runtime_service: SourceRuntimeBoundary,
    conn: TransactionContext,
    ctx: RequestContext,
    record: SourceConnectionRecord,
) -> SourceConnectionRow:
    try:
        service.source_registry_repository.create_source(transaction=conn, record=record)
    except SourceConnectionAlreadyExistsError as exc:
        raise ConflictDetected("source already exists", details={"source_name": record.source_name}) from exc
    row = source_row_in_tx(service.source_registry_repository, conn, ctx, record.source_name)
    upsert_source_resource(conn, ctx, service.resource_catalog_repository, runtime_service, row)
    runtime_service._audit(
        conn,
        ctx,
        event_type="source.created",
        resource_type="source",
        resource_id=record.source_name,
        action="create",
        after_ref=source_view(row),
    )
    return row


def managed_sync_record(
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
    schedule: Mapping[str, object] | None,
    config_summary: Mapping[str, object],
) -> SourceSyncRecord:
    return source_sync_record(
        ctx,
        sync_name=sync_name,
        source_name=source_name,
        display_name=display_name,
        source_type=source_type,
        capability=capability,
        target_dataset_ref=target_dataset_ref,
        target_media_set_id=target_media_set_id,
        mode=mode,
        schedule=schedule or {},
        config_summary=config_summary,
    )


def prepare_managed_sync_record(
    service: ManagedSyncPreparationDependencies,
    runtime_service: SourceRuntimeBoundary,
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
    schedule: Mapping[str, object] | None,
    config_summary: Mapping[str, object],
) -> SourceSyncRecord:
    ensure_managed_source(
        service,
        runtime_service,
        ctx,
        source_name=source_name,
        display_name=display_name,
        source_type=source_type,
        config_summary=config_summary,
    )
    if target_dataset_ref is not None:
        service.dataset_registry_service.ensure_dataset(target_dataset_ref, ctx=ctx)
    return managed_sync_record(
        ctx,
        sync_name=sync_name,
        source_name=source_name,
        display_name=display_name,
        source_type=source_type,
        capability=capability,
        target_dataset_ref=target_dataset_ref,
        target_media_set_id=target_media_set_id,
        mode=mode,
        schedule=schedule,
        config_summary=config_summary,
    )


def _managed_source_kind(source_type: str) -> SourceConnectionKind:
    if source_type not in {"kafka", "postgres_jdbc", "rest_api", "sap_odata", "sharepoint_graph"}:
        raise ValidationFailed(
            "managed source registry does not support this source type",
            details={"sourceType": source_type},
        )
    return cast(SourceConnectionKind, source_type)


def _connection_config(config_summary: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in config_summary.items() if key in _CONNECTION_CONFIG_FIELDS}


def _require_compatible_source(
    row: SourceConnectionRow,
    kind: SourceConnectionKind,
    config_summary: Mapping[str, object],
) -> None:
    compatible_kinds = {kind, "rest"} if kind == "rest_api" else {kind}
    existing_config = row["config_summary"]
    has_same_connection = all(existing_config.get(key) == value for key, value in config_summary.items())
    if row["kind"] not in compatible_kinds or not has_same_connection:
        raise ConflictDetected(
            "source already exists with different connection config",
            details={"source_name": row["source_name"]},
        )
