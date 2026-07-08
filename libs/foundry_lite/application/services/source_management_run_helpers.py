"""Managed source sync run completion helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from foundry_lite.application.ports import (
    ProductWorkflowRun,
    SourceDatabaseAdapter,
    SourceManagementRepository,
    TransactionManager,
)
from foundry_lite.application.ports.secret_provider import SecretVault
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
from foundry_lite.application.services.source_management_views import sync_run_view
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import FoundryLiteError, NotFound, ValidationFailed


class ConnectorOnboardingBoundary(Protocol):
    def start_resource_sync(
        self,
        connector_name: str,
        resource_name: str,
        *,
        idempotency_key: str,
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
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
    database_url = service.secret_vault.get_secret(required_text(summary, "databaseUrlSecretRef")).value
    batch = service.source_database_adapter.read_table_batch(
        database_url,
        table_name=required_text(summary, "tableName"),
        batch_limit=int_value(run.get("batch_limit") or summary.get("batchLimit"), 100),
        checkpoint_column=optional_text(summary.get("checkpointColumn")),
        after_value=mapping(run["checkpoint_start"]).get("lastValue"),
    )
    commit = dataset_ingest_service.sync_rows_batch(
        dataset_ref,
        batch.rows,
        fieldnames=fieldnames(batch.rows, batch.schema),
        ctx=ctx,
        sync_name=str(sync["sync_name"]),
        tx_type=str(sync["mode"]),
        source_type=f"source.{sync['source_type']}",
        transaction_metadata={"sourceManagedSync": {"runId": run["id"], "checkpoint": dict(batch.checkpoint)}},
    )
    result = commit_result_payload(commit, batch)
    return finish_run(
        service, ctx, sync, run, "succeeded", None, result.get("datasetVersionId"), batch.checkpoint, result, None
    )


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
    return sync_run_view(updated or run)


def fail_run(
    service: SourceRunStore,
    ctx: RequestContext,
    run: Mapping[str, object],
    exc: FoundryLiteError,
) -> dict[str, object]:
    error = {"message": exc.message, "details": dict(exc.details)}
    sync = {"sync_name": run["sync_name"]}
    return finish_run(service, ctx, sync, run, "failed", None, None, {}, {}, error)
