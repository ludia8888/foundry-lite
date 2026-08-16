"""Managed source sync run completion helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    ConnectorNetworkRoute,
    SourceConnectionUpdate,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    SourceRegistryRepository,
    SourceTableBatch,
    TransactionContext,
    TransactionManager,
)
from foundry_lite.application.ports.secret_provider import SecretVault
from foundry_lite.application.services.runtime_error_payloads import scrub_error_mapping, scrub_error_text
from foundry_lite.application.services.source_exploration_execution import (
    execute_source_exploration as execute_source_exploration,
)
from foundry_lite.application.services.source_exploration_payloads import (
    ConnectorOnboardingBoundary,
)
from foundry_lite.application.services.source_exploration_payloads import (
    SourceExplorationDependencies as SourceExplorationDependencies,
)
from foundry_lite.application.services.source_management_helpers import (
    CommitResultLike,
    commit_result_payload,
    fieldnames,
    int_value,
    mapping,
    now,
    optional_text,
    required_text,
    target_dataset_ref,
)
from foundry_lite.application.services.source_management_streaming import (
    complete_stream_run as complete_stream_run,
)
from foundry_lite.application.services.source_management_views import sync_run_view
from foundry_lite.application.services.source_network_routing import resolve_source_network_route
from foundry_lite.application.services.source_workflow_completion import source_workflow_completion
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


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
    result = {"workflowRun": scrub_error_mapping(workflow)}
    workflow_run_id = str(workflow["workflowRunId"])
    completion = source_workflow_completion(workflow)
    return finish_run(
        service,
        ctx,
        sync,
        run,
        completion.status,
        workflow_run_id,
        completion.dataset_version_id,
        {},
        result,
        completion.error,
    )


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
    changed_at = now()
    completed_at = None if status == "running" else changed_at
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
            updated_at=changed_at,
        )
        _update_source_after_run(service, conn, ctx, sync, run, status, workflow_run_id, result, changed_at)
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
    error = {
        "message": scrub_error_text(exc.message),
        "details": scrub_error_mapping(exc.details),
        "requestId": ctx.request_id,
    }
    sync = {"sync_name": run["sync_name"]}
    return finish_run(service, ctx, sync, run, "failed", None, None, {}, {}, error)
