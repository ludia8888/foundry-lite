from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.dataset_repository import DatasetRow
from foundry_lite.application.ports.transform_repository import (
    TransformRepository,
    TransformRow,
    TransformRunRecord,
    TransformRunRow,
)
from foundry_lite.application.primitives import INPUT_PATTERN, _new_id, _now
from foundry_lite.application.services.transform_protocols import (
    TransformDatasetRegistry,
    TransformDatasetTransactions,
    TransformDatasetVersions,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import NotFound, ValidationFailed


@dataclass(frozen=True)
class TransformRunPlan:
    """Transaction and runtime identifiers for one transform execution."""

    transform_id: str
    entrypoint: str
    checks: list[dict[str, object]]
    output_dataset: DatasetRow
    input_versions: dict[str, str]
    transaction_id: str
    run_id: str
    retry_of_run_id: str | None = None


def start_transform_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    dataset_version_service: TransformDatasetVersions,
    transform_repository: TransformRepository,
) -> TransformRunPlan:
    transform = _get_transform(conn, ctx, api_name, transform_repository)
    input_versions = _resolve_transform_inputs(
        conn,
        ctx,
        transform["entrypoint"],
        transform["inputs"],
        dataset_registry_service,
        dataset_version_service,
    )
    return _open_transform_run(
        conn=conn,
        ctx=ctx,
        transform=transform,
        input_versions=input_versions,
        dataset_registry_service=dataset_registry_service,
        dataset_transaction_service=dataset_transaction_service,
        transform_repository=transform_repository,
    )


def start_failed_transform_retry(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    transform_run_id: str,
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    transform_repository: TransformRepository,
) -> TransformRunPlan:
    failed_run = _failed_transform_run(conn, ctx, transform_run_id, transform_repository)
    transform = transform_repository.transform_by_id(transaction=conn, transform_id=failed_run["transform_id"])
    if transform is None or transform["tenant_id"] != ctx.tenant_id:
        raise NotFound("transform not found", details={"transform_run_id": transform_run_id})
    return _open_transform_run(
        conn=conn,
        ctx=ctx,
        transform=transform,
        input_versions=dict(failed_run["input_versions"]),
        dataset_registry_service=dataset_registry_service,
        dataset_transaction_service=dataset_transaction_service,
        transform_repository=transform_repository,
        retry_of_run_id=transform_run_id,
    )


def _open_transform_run(
    *,
    conn: TransactionContext,
    ctx: RequestContext,
    transform: TransformRow,
    input_versions: dict[str, str],
    dataset_registry_service: TransformDatasetRegistry,
    dataset_transaction_service: TransformDatasetTransactions,
    transform_repository: TransformRepository,
    retry_of_run_id: str | None = None,
) -> TransformRunPlan:
    output_dataset = dataset_registry_service.get_dataset(str(transform["output_dataset_ref"]), ctx=ctx)
    transaction_id = dataset_transaction_service._open_dataset_transaction(conn, ctx, output_dataset, "SNAPSHOT")
    plan = TransformRunPlan(
        transform_id=str(transform["id"]),
        entrypoint=str(transform["entrypoint"]),
        checks=[dict(check) for check in transform["checks"]],
        output_dataset=output_dataset,
        input_versions=input_versions,
        transaction_id=transaction_id,
        run_id=_new_id("transform_run"),
        retry_of_run_id=retry_of_run_id,
    )
    _insert_transform_run(conn, ctx, plan, transform_repository)
    return plan


def _failed_transform_run(
    conn: TransactionContext,
    ctx: RequestContext,
    transform_run_id: str,
    transform_repository: TransformRepository,
) -> TransformRunRow:
    row = transform_repository.transform_run_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        transform_run_id=transform_run_id,
    )
    if row is None:
        raise NotFound("transform run not found", details={"transform_run_id": transform_run_id})
    if row["status"] != "FAILED":
        raise ValidationFailed("transform run is not failed", details={"transform_run_id": transform_run_id})
    return row


def _insert_transform_run(
    conn: TransactionContext,
    ctx: RequestContext,
    plan: TransformRunPlan,
    transform_repository: TransformRepository,
) -> None:
    transform_repository.insert_transform_run(
        transaction=conn,
        record=TransformRunRecord(
            transform_run_id=plan.run_id,
            tenant_id=ctx.tenant_id,
            transform_id=plan.transform_id,
            status="RUNNING",
            input_versions=plan.input_versions,
            output_version_id=None,
            transaction_id=plan.transaction_id,
            error=None,
            created_at=_now(),
            completed_at=None,
        ),
    )


def _get_transform(
    conn: TransactionContext,
    ctx: RequestContext,
    api_name: str,
    transform_repository: TransformRepository,
) -> TransformRow:
    row = transform_repository.transform_by_api_name(transaction=conn, tenant_id=ctx.tenant_id, api_name=api_name)
    if row is None:
        raise NotFound("transform not found", details={"api_name": api_name})
    return row


def _resolve_transform_inputs(
    conn: TransactionContext,
    ctx: RequestContext,
    entrypoint: str,
    inputs: Mapping[str, str],
    dataset_registry_service: TransformDatasetRegistry,
    dataset_version_service: TransformDatasetVersions,
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    template = Path(entrypoint).read_text(encoding="utf-8")
    referenced = set(INPUT_PATTERN.findall(template))
    configured = set(inputs.values())
    undeclared = sorted(referenced - configured)
    if undeclared:
        raise ValidationFailed(
            "transform SQL references undeclared input datasets",
            details={"dataset_refs": undeclared},
        )
    for dataset_ref in sorted(referenced | configured):
        dataset = dataset_registry_service.get_dataset(dataset_ref, ctx=ctx)
        version = dataset_version_service._latest_version_by_dataset_id(conn, dataset["id"])
        resolved[dataset_ref] = version["id"]
    return resolved
