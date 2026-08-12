"""Serialized deployment replay and reviewed-head admission helpers."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineDeploymentRow,
    PipelineExecutionRepository,
)
from foundry_lite.application.ports.pipeline_repository import PipelineRepository, PipelineVersionRow
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound

LEGACY_DEPLOYMENT_CAPABILITIES = frozenset(
    {"tabular_v1_compiler", "semantic_index_candidate_runtime", "ontology_mapping_candidate_runtime"}
)


class PipelineDeploymentOutcomeUnknown(Exception):
    """Deployment committed, but its response projection failed."""

    original: Exception

    def __init__(self, original: Exception) -> None:
        super().__init__("pipeline deployment committed before response projection failed")
        self.original = original


def project_committed_deployment(projection: Callable[[], dict[str, object]]) -> dict[str, object]:
    try:
        return projection()
    except Exception as exc:
        raise PipelineDeploymentOutcomeUnknown(exc) from exc


def locked_deployment_replay(
    execution_repository: PipelineExecutionRepository,
    pipeline_repository: PipelineRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    idempotency_key: str,
    request_fingerprint: str,
) -> tuple[PipelineDeploymentRow, PipelineVersionRow] | None:
    """Resolve a replay only after the pipeline deployment lock is held."""

    row = execution_repository.deployment_by_idempotency_key(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        idempotency_key=idempotency_key,
    )
    if row is None:
        return None
    require_matching_deployment(row, request_fingerprint)
    version = pipeline_repository.version_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        version_id=str(row["version_id"]),
    )
    if version is None:
        raise NotFound("pipeline deployment version not found", details={"version_id": row["version_id"]})
    return row, version


def deployment_receipt(
    execution_repository: PipelineExecutionRepository,
    pipeline_repository: PipelineRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    idempotency_key: str,
) -> tuple[PipelineDeploymentRow, PipelineVersionRow] | None:
    """Load an exact durable deployment receipt without starting promotion."""
    row = execution_repository.deployment_by_idempotency_key(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        idempotency_key=idempotency_key,
    )
    if row is None:
        return None
    version = require_stored_version(pipeline_repository, conn, ctx, str(row["version_id"]))
    return row, version


def require_stored_version(
    pipeline_repository: PipelineRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    version_id: str,
) -> PipelineVersionRow:
    version = pipeline_repository.version_by_id(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        version_id=version_id,
    )
    if version is None:
        raise NotFound("pipeline deployment version not found", details={"version_id": version_id})
    return version


def has_dataset_source(graph: Mapping[str, object]) -> bool:
    nodes = graph.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(
        isinstance(node, Mapping)
        and (
            node.get("descriptorId") in {"source.dataset", "source.stream", "source.geospatial"}
            or node.get("type") == "dataset"
        )
        for node in nodes
    )


def require_expected_current_deployment(
    execution_repository: PipelineExecutionRepository,
    conn: TransactionContext,
    ctx: RequestContext,
    version: PipelineVersionRow,
    options: Mapping[str, object] | None,
) -> None:
    """Compare the reviewed deployment head inside the serialized transaction."""

    expected = optional_text(options, "expectedCurrentDeploymentId")
    if expected is None:
        return
    rows = execution_repository.list_deployments(
        transaction=conn,
        tenant_id=ctx.tenant_id,
        pipeline_id=str(version["pipeline_id"]),
        limit=1,
    )
    actual = str(rows[0]["id"]) if rows else "none"
    if actual != expected:
        raise ConflictDetected(
            "pipeline deployment head changed after the release was reviewed",
            details={"expectedCurrentDeploymentId": expected, "currentDeploymentId": actual},
        )


def require_matching_deployment(row: PipelineDeploymentRow, request_fingerprint: str) -> None:
    if row["request_fingerprint"] == request_fingerprint:
        return
    raise ConflictDetected(
        "pipeline deployment idempotency key was reused with a different request",
        details={"deployment_id": row["id"], "request_fingerprint": request_fingerprint},
    )


def optional_text(options: Mapping[str, object] | None, key: str) -> str | None:
    value = (options or {}).get(key)
    return value.strip() if isinstance(value, str) and value.strip() else None


__all__ = [
    "LEGACY_DEPLOYMENT_CAPABILITIES",
    "deployment_receipt",
    "has_dataset_source",
    "locked_deployment_replay",
    "optional_text",
    "PipelineDeploymentOutcomeUnknown",
    "project_committed_deployment",
    "require_stored_version",
    "require_expected_current_deployment",
    "require_matching_deployment",
]
