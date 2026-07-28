"""Idempotent request helpers for Pipeline Builder runs."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.pipeline_repository import (
    PipelineRepository,
    PipelineRunRecord,
    PipelineRunRow,
    PipelineVersionRow,
)
from foundry_lite.application.ports.transaction_context import TransactionManager
from foundry_lite.application.primitives import _json_hash, _now
from foundry_lite.application.services.pipeline_payloads import run_record
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed


def deployed_pipeline_version(
    transaction_manager: TransactionManager,
    repository: PipelineRepository,
    ctx: RequestContext,
    pipeline_id: str,
    version_id: str | None,
) -> PipelineVersionRow:
    if version_id is not None:
        with transaction_manager.begin() as conn:
            version = repository.version_by_id(transaction=conn, tenant_id=ctx.tenant_id, version_id=version_id)
        if version is None:
            raise NotFound("pipeline version not found", details={"version_id": version_id})
        require_pipeline_match(version, pipeline_id)
        require_deployed(version)
        return version
    with transaction_manager.begin() as conn:
        rows = repository.list_versions(transaction=conn, tenant_id=ctx.tenant_id, pipeline_id=pipeline_id, limit=100)
    for row in rows:
        if row["deployed_at"] is not None:
            return row
    raise NotFound("deployed pipeline version not found", details={"pipeline_id": pipeline_id})


def run_request_fingerprint(
    pipeline_id: str,
    version: PipelineVersionRow,
    parameters: Mapping[str, object] | None,
    target_node_ids: list[str] | None,
) -> str:
    return _json_hash(
        {
            "parameters": dict(parameters or {}),
            "pipelineId": pipeline_id,
            "targetNodeIds": list(target_node_ids or []),
            "versionId": str(version["id"]),
        }
    )


def new_run_record(
    ctx: RequestContext,
    *,
    pipeline_id: str,
    version: PipelineVersionRow,
    idempotency_key: str,
    request_fingerprint: str,
    parameters: Mapping[str, object] | None,
    target_node_ids: list[str] | None,
) -> PipelineRunRecord:
    return run_record(
        ctx,
        pipeline_id=pipeline_id,
        version_id=str(version["id"]),
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        plan_fingerprint=str(version["plan_fingerprint"] or version["graph_fingerprint"]),
        parameters=parameters,
        target_node_ids=target_node_ids,
        now=_now(),
    )


def require_idempotent_run(row: PipelineRunRow, request_fingerprint: str) -> None:
    if row["request_fingerprint"] == request_fingerprint:
        return
    raise ConflictDetected(
        "pipeline run idempotency key was reused with a different request",
        details={
            "run_id": row["id"],
            "existing_request_fingerprint": row["request_fingerprint"],
            "request_fingerprint": request_fingerprint,
        },
    )


def require_pipeline_match(version: PipelineVersionRow, pipeline_id: str) -> None:
    if version["pipeline_id"] != pipeline_id:
        raise ValidationFailed(
            "pipeline version belongs to a different pipeline",
            details={"pipeline_id": pipeline_id, "versionPipelineId": version["pipeline_id"]},
        )


def require_deployed(version: PipelineVersionRow) -> None:
    if version["deployed_at"] is None:
        raise ConflictDetected("pipeline version is not deployed", details={"version_id": version["id"]})
