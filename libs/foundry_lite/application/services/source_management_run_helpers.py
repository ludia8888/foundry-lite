"""Managed source sync run completion helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from foundry_lite.application.ports import (
    ConnectorAdapter,
    ConnectorNetworkRoute,
    ConnectorSnapshotRequest,
    ProductWorkflowRun,
    SourceConnectionUpdate,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    SourceRegistryRepository,
    SourceTableBatch,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.secret_provider import SecretVault
from foundry_lite.application.ports.source_stream_adapter import SourceStreamAdapter
from foundry_lite.application.private_network_policy import require_private_network_bypass_allowed
from foundry_lite.application.services.source_management_helpers import (
    CommitResultLike,
    commit_result_payload,
    fieldnames,
    int_value,
    mapping,
    now,
    optional_text,
    required_text,
    rest_source_config,
    target_dataset_ref,
    text,
)
from foundry_lite.application.services.source_management_streaming import (
    complete_stream_run as complete_stream_run,
)
from foundry_lite.application.services.source_management_streaming import (
    explore_stream_source,
)
from foundry_lite.application.services.source_management_views import sync_run_view
from foundry_lite.application.services.source_network_routing import resolve_source_network_route
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


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


class DatasetIngestBoundary(Protocol):
    def sync_rows_batch(
        self,
        dataset_ref: str,
        rows: Sequence[Mapping[str, object]],
        *,
        fieldnames: Sequence[str],
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        tx_type: str = "APPEND",
        source_type: str = "source.batch",
        transaction_metadata: Mapping[str, object] | None = None,
    ) -> CommitResultLike | None: ...


class SourceRunStore(Protocol):
    @property
    def engine(self) -> TransactionManager: ...

    @property
    def source_management_repository(self) -> SourceManagementRepository: ...

    @property
    def source_registry_repository(self) -> SourceRegistryRepository: ...

    @property
    def source_database_adapter(self) -> SourceDatabaseAdapter: ...

    @property
    def secret_vault(self) -> SecretVault: ...


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
    # Refuse the private-network bypass on a protected runtime before any probe
    # runs, so a stored allowPrivateNetwork flag cannot open an SSRF path here.
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
        return _explore_registered_rest(
            dependencies,
            ctx,
            source_name,
            connector_name,
            resource_name,
        )
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
    return _database_preview(dependencies, ctx, request, database_url, table_name, sample_limit, route)


def _database_preview(
    dependencies: SourceExplorationDependencies,
    ctx: RequestContext,
    request: Mapping[str, object],
    database_url: str,
    table_name: str,
    sample_limit: int,
    route: ConnectorNetworkRoute,
) -> dict[str, object]:
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
    with dependencies.engine.begin() as conn:
        source = dependencies.source_registry_repository.source_by_name(
            transaction=conn,
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


def complete_rest_run(
    service: SourceRunStore,
    connector_onboarding_service: ConnectorOnboardingBoundary,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
) -> dict[str, object]:
    summary = mapping(sync["config_summary"])
    connector_name = required_text(summary, "connectorName")
    resource_name = required_text(summary, "resourceName")
    require_rest_sync_target_matches_resource(connector_onboarding_service, ctx, sync, connector_name, resource_name)
    workflow = connector_onboarding_service.start_resource_sync(
        connector_name,
        resource_name,
        idempotency_key=str(run["idempotency_key"]),
        ctx=ctx,
        sync_name=str(sync["sync_name"]),
        source_name=str(sync["source_name"]),
        transaction_type=str(sync["mode"]),
    )
    return finish_rest_workflow(service, ctx, sync, run, workflow)


def finish_rest_workflow(
    service: SourceRunStore,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
    workflow: Mapping[str, object],
) -> dict[str, object]:
    result = {"workflowRun": dict(workflow)}
    output = mapping(workflow.get("output"))
    workflow_status = str(workflow.get("status") or "running")
    workflow_run_id = str(workflow["workflowRunId"])
    if workflow_status == "succeeded" and isinstance(output.get("committedVersionId"), str):
        return finish_run(
            service, ctx, sync, run, "succeeded", workflow_run_id, output["committedVersionId"], {}, result, None
        )
    if workflow_status == "failed":
        return finish_run(
            service, ctx, sync, run, "failed", workflow_run_id, None, {}, result, mapping(workflow.get("error"))
        )
    return finish_run(service, ctx, sync, run, "running", workflow_run_id, None, {}, result, None)


def require_rest_sync_target_matches_resource(
    connector_onboarding_service: ConnectorOnboardingBoundary,
    ctx: RequestContext,
    sync: Mapping[str, object],
    connector_name: str,
    resource_name: str,
) -> None:
    expected = target_dataset_ref(sync)
    actual = rest_resource_dataset_ref(connector_onboarding_service, ctx, connector_name, resource_name)
    if expected != actual:
        raise ValidationFailed(
            "managed sync target dataset does not match connector resource dataset",
            details={
                "syncName": sync["sync_name"],
                "connectorName": connector_name,
                "resourceName": resource_name,
                "targetDatasetRef": expected,
                "connectorDatasetRef": actual,
            },
        )


def rest_resource_dataset_ref(
    connector_onboarding_service: ConnectorOnboardingBoundary,
    ctx: RequestContext,
    connector_name: str,
    resource_name: str,
) -> str:
    connection = connector_onboarding_service.get_connection(connector_name, ctx=ctx)
    for resource in cast(Sequence[Mapping[str, object]], connection.get("resources", [])):
        if resource.get("resourceName") == resource_name:
            return required_text(resource, "datasetRef")
    raise NotFound(
        "connector resource not found",
        details={"connector_name": connector_name, "resource_name": resource_name},
    )


def complete_database_run(
    service: SourceRunStore,
    dataset_ingest_service: DatasetIngestBoundary,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
) -> dict[str, object]:
    summary = mapping(sync["config_summary"])
    dataset_ref = target_dataset_ref(sync)
    batch = _read_database_batch(service, ctx, sync, run, summary)
    # An empty (caught-up) batch returns an empty checkpoint; persisting that would
    # reset the resume cursor to None and the next tick would re-ingest the whole
    # table as duplicates. Carry the prior checkpoint forward instead.
    checkpoint: Mapping[str, object] = dict(batch.checkpoint) or mapping(run["checkpoint_start"])
    commit = dataset_ingest_service.sync_rows_batch(
        dataset_ref,
        batch.rows,
        fieldnames=fieldnames(batch.rows, batch.schema),
        ctx=ctx,
        sync_name=str(sync["sync_name"]),
        tx_type=str(sync["mode"]),
        source_type=f"source.{sync['source_type']}",
        transaction_metadata={
            "sourceManagedSync": {"runId": run["id"], "checkpoint": dict(checkpoint)},
            "sourceNetworkEvidence": dict(batch.network_evidence),
        },
    )
    result = commit_result_payload(commit, batch)
    return finish_run(
        service, ctx, sync, run, "succeeded", None, result.get("datasetVersionId"), checkpoint, result, None
    )


def _read_database_batch(
    service: SourceRunStore,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
    summary: Mapping[str, object],
) -> SourceTableBatch:
    database_url = service.secret_vault.get_secret(required_text(summary, "databaseUrlSecretRef")).value
    return service.source_database_adapter.read_table_batch(
        database_url,
        table_name=required_text(summary, "tableName"),
        batch_limit=int_value(run.get("batch_limit") or summary.get("batchLimit"), 100),
        checkpoint_column=optional_text(summary.get("checkpointColumn")),
        after_value=mapping(run["checkpoint_start"]).get("lastValue"),
        network_route=_database_network_route(service, ctx, sync),
        connection_id=f"{ctx.request_id}:{run['id']}",
    )


def _database_network_route(
    service: SourceRunStore,
    ctx: RequestContext,
    sync: Mapping[str, object],
) -> ConnectorNetworkRoute:
    source_name = str(sync["source_name"])
    with service.engine.begin() as conn:
        source = service.source_registry_repository.source_by_name(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            source_name=source_name,
        )
    if source is None:
        raise NotFound("source not found", details={"source_name": source_name})
    return resolve_source_network_route(service.engine, service.source_management_repository, ctx, source)


def finish_run(
    service: SourceRunStore,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
    status: str,
    workflow_run_id: str | None,
    dataset_version_id: object,
    checkpoint: Mapping[str, object],
    result: Mapping[str, object],
    error: Mapping[str, object] | None,
) -> dict[str, object]:
    completed_at = now()
    with service.engine.begin() as conn:
        updated = service.source_management_repository.update_sync_run_result(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            run_id=str(run["id"]),
            status=status,
            dataset_version_id=dataset_version_id if isinstance(dataset_version_id, str) else None,
            checkpoint_end=checkpoint,
            result_summary=result,
            error=error,
            completed_at=completed_at,
            workflow_run_id=workflow_run_id,
        )
        service.source_management_repository.update_sync_after_run(
            transaction=conn,
            tenant_id=ctx.tenant_id,
            sync_name=str(sync["sync_name"]),
            run_id=str(run["id"]),
            workflow_run_id=workflow_run_id,
            checkpoint=checkpoint,
            updated_at=completed_at,
        )
        _update_source_after_run(service, conn, ctx, sync, run, status, workflow_run_id, result, completed_at)
    return sync_run_view(updated or run)


def _update_source_after_run(
    service: SourceRunStore,
    conn: TransactionContext,
    ctx: RequestContext,
    sync: Mapping[str, object],
    run: Mapping[str, object],
    status: str,
    workflow_run_id: str | None,
    result: Mapping[str, object],
    completed_at: str,
) -> None:
    source = service.source_registry_repository.source_by_name(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        source_name=str(sync.get("source_name") or run["source_name"]),
    )
    if source is None:
        return
    service.source_registry_repository.update_source(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        source_name=source["source_name"],
        patch=SourceConnectionUpdate(
            last_run_id=str(run["id"]),
            last_workflow_run_id=workflow_run_id,
            last_commit_ref=result if status == "succeeded" else None,
            updated_at=completed_at,
        ),
    )


def fail_run(
    service: SourceRunStore,
    ctx: RequestContext,
    run: Mapping[str, object],
    exc: FoundryLiteError,
) -> dict[str, object]:
    error = {"message": exc.message, "details": dict(exc.details), "requestId": ctx.request_id}
    sync = {"sync_name": run["sync_name"]}
    return finish_run(service, ctx, sync, run, "failed", None, None, {}, {}, error)
