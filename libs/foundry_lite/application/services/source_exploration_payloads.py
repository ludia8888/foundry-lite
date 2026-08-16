"""Read-only connector payloads used by Source Explorer execution evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorNetworkRoute,
    ConnectorSnapshotRequest,
    ProductWorkflowRun,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    SourceRegistryRepository,
    TransactionManager,
)
from foundry_lite.application.ports.secret_provider import SecretVault
from foundry_lite.application.ports.source_stream_adapter import SourceStreamAdapter
from foundry_lite.application.private_network_policy import require_private_network_bypass_allowed
from foundry_lite.application.services.source_management_helpers import (
    int_value,
    mapping,
    optional_text,
    required_text,
    rest_source_config,
    text,
)
from foundry_lite.application.services.source_management_streaming import explore_stream_source
from foundry_lite.application.services.source_network_routing import resolve_source_network_route
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


class ConnectorOnboardingBoundary(Protocol):
    def test_resource(
        self,
        connector_name: str,
        resource_name: str,
        *,
        ctx: RequestContext | None = None,
        network_route: ConnectorNetworkRoute | None = None,
    ) -> Mapping[str, object]: ...

    def start_resource_sync(
        self,
        connector_name: str,
        resource_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        source_name: str | None = None,
        transaction_type: str = "APPEND",
    ) -> ProductWorkflowRun: ...

    def get_connection(self, connector_name: str, *, ctx: RequestContext | None = None) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class SourceExplorationDependencies:
    engine: TransactionManager
    connector_adapter: ConnectorAdapter
    connector_onboarding_service: ConnectorOnboardingBoundary
    source_database_adapter: SourceDatabaseAdapter
    source_stream_adapter: SourceStreamAdapter
    source_management_repository: SourceManagementRepository
    source_registry_repository: SourceRegistryRepository
    secret_vault: SecretVault


def explore_source_payload(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    source_name: str,
    source_type: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    require_private_network_bypass_allowed(bool(request.get("allowPrivateNetwork", False)), resource=source_name)
    if source_type in {"rest_api", "sap_odata"}:
        return _explore_rest(dependencies, ctx, source_name, request)
    if source_type == "postgres_jdbc":
        return _explore_database(dependencies, ctx, source_name, request)
    if source_type == "kafka":
        return explore_stream_source(dependencies, ctx, source_name, request)
    raise ValidationFailed("source type does not support exploration", details={"sourceType": source_type})


def _explore_rest(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    source_name: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    connector_name = optional_text(request.get("connectorName"))
    resource_name = optional_text(request.get("resourceName"))
    if connector_name is not None and resource_name is not None:
        return _explore_registered_rest(dependencies, ctx, source_name, connector_name, resource_name)
    snapshot = dependencies.connector_adapter.snapshot(
        ConnectorSnapshotRequest(
            connector_name=source_name,
            resource_name=text(request, "resourceName", "preview"),
            tenant_id=ctx.tenant_id,
            request_id=ctx.request_id,
            rest=rest_source_config(request),
        )
    )
    return {"sample": list(snapshot.rows), "schema": dict(snapshot.schema), "cursor": dict(snapshot.cursor or {})}


def _explore_registered_rest(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    source_name: str,
    connector_name: str,
    resource_name: str,
) -> dict[str, object]:
    route = _source_network_route(dependencies, ctx, source_name)
    result = dependencies.connector_onboarding_service.test_resource(
        connector_name,
        resource_name,
        ctx=ctx,
        network_route=route,
    )
    if result.get("status") != "succeeded":
        raise ValidationFailed(
            "source exploration request failed",
            details={"connectorError": dict(mapping(result.get("error")))},
        )
    return {
        "connectorName": connector_name,
        "resourceName": resource_name,
        "datasetRef": result.get("datasetRef"),
        "configFingerprint": result.get("configFingerprint"),
        "rowCount": result.get("rowCount", 0),
        "sampleRows": list(cast(Sequence[Mapping[str, object]], result.get("sampleRows", []))),
        "schema": dict(mapping(result.get("schema"))),
        "cursor": dict(mapping(result.get("cursor"))),
        "networkEvidence": dict(mapping(result.get("networkEvidence"))),
        "datasetCommitCreated": False,
    }


def _explore_database(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    source_name: str,
    request: Mapping[str, object],
) -> dict[str, object]:
    database_url = dependencies.secret_vault.get_secret(required_text(request, "databaseUrlSecretRef")).value
    sample_limit = int_value(request.get("sampleLimit"), 20)
    route = _source_network_route(dependencies, ctx, source_name, allow_unregistered=True)
    table_name = optional_text(request.get("tableName"))
    if table_name is None:
        tables = dependencies.source_database_adapter.list_tables(
            database_url,
            sample_limit=sample_limit,
            network_route=route,
            connection_id=ctx.request_id,
        )
        return {"tables": [dict(table) for table in tables]}
    batch = dependencies.source_database_adapter.read_table_batch(
        database_url,
        table_name=table_name,
        batch_limit=sample_limit,
        checkpoint_column=optional_text(request.get("checkpointColumn")),
        network_route=route,
        connection_id=ctx.request_id,
    )
    return {
        "sample": list(batch.rows),
        "schema": dict(batch.schema),
        "checkpoint": dict(batch.checkpoint),
        "networkEvidence": dict(batch.network_evidence),
    }


def _source_network_route(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    source_name: str,
    *,
    allow_unregistered: bool = False,
) -> ConnectorNetworkRoute:
    with dependencies.engine.begin() as transaction:
        source = dependencies.source_registry_repository.source_by_name(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            source_name=source_name,
        )
    if source is None:
        if allow_unregistered:
            return ConnectorNetworkRoute(mode="direct")
        raise NotFound("source not found", details={"source_name": source_name})
    return resolve_source_network_route(
        dependencies.engine,
        dependencies.source_management_repository,
        ctx,
        source,
    )


__all__ = ["ConnectorOnboardingBoundary", "SourceExplorationDependencies", "explore_source_payload"]
