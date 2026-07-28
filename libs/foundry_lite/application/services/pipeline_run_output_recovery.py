"""Recover committed Pipeline outputs when terminal run evidence is incomplete."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.dataset_transaction_repository import (
    DatasetTransactionRepository,
    PipelineDatasetCommitRow,
)
from foundry_lite.application.ports.media_repository import (
    MediaRepository,
    PipelineMediaCommitVersionRow,
)
from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineExecutionRepository,
    PipelineRunArtifactRow,
)
from foundry_lite.application.ports.transaction_context import TransactionContext
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation


@dataclass(frozen=True)
class RecoveredPipelineOutput:
    """One durable output reconstructed from its strongest available evidence."""

    node_id: str
    artifact_kind: str
    plane: str
    status: str
    is_serving: bool
    ref: Mapping[str, object]
    manifest: Mapping[str, object]
    artifact_id: str | None
    recovery_source: str
    evidence_id: str


def committed_pipeline_outputs(
    transaction: TransactionContext,
    repository: PipelineExecutionRepository,
    dataset_transaction_repository: DatasetTransactionRepository,
    media_repository: MediaRepository,
    ctx: RequestContext,
    pipeline_run_id: str,
) -> list[RecoveredPipelineOutput]:
    node_rows = repository.node_runs_for_run(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        run_id=pipeline_run_id,
    )
    output_node_ids = {item["node_id"] for item in node_rows if item["descriptor_id"].startswith("output.")}
    artifacts = [
        _artifact_output(item)
        for item in repository.artifacts_for_run(
            transaction=transaction,
            tenant_id=ctx.tenant_id,
            run_id=pipeline_run_id,
        )
        if item["node_id"] in output_node_ids and item["status"] == "COMMITTED"
    ]
    transactions = dataset_transaction_repository.committed_pipeline_output_transactions(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        pipeline_run_id=pipeline_run_id,
    )
    seen = {_output_key(item) for item in artifacts}
    recovered_datasets = _missing_dataset_outputs(transactions, output_node_ids, seen)
    media_versions = media_repository.committed_pipeline_output_versions(
        transaction=transaction,
        tenant_id=ctx.tenant_id,
        pipeline_run_id=pipeline_run_id,
    )
    recovered_media = _missing_media_outputs(media_versions, pipeline_run_id, output_node_ids, seen)
    return [*artifacts, *recovered_datasets, *recovered_media]


def _missing_dataset_outputs(
    rows: list[PipelineDatasetCommitRow],
    output_node_ids: set[str],
    seen: set[tuple[str, str]],
) -> list[RecoveredPipelineOutput]:
    recovered: list[RecoveredPipelineOutput] = []
    for row in rows:
        output = _dataset_commit_output(row, output_node_ids)
        if output is not None and _output_key(output) not in seen:
            recovered.append(output)
            seen.add(_output_key(output))
    return recovered


def _missing_media_outputs(
    rows: list[PipelineMediaCommitVersionRow],
    pipeline_run_id: str,
    output_node_ids: set[str],
    seen: set[tuple[str, str]],
) -> list[RecoveredPipelineOutput]:
    by_transaction: dict[str, list[PipelineMediaCommitVersionRow]] = {}
    for row in rows:
        by_transaction.setdefault(row["transaction_id"], []).append(row)
    recovered: list[RecoveredPipelineOutput] = []
    for transaction_rows in by_transaction.values():
        output = _media_commit_output(transaction_rows, pipeline_run_id, output_node_ids)
        if _output_key(output) not in seen:
            recovered.append(output)
            seen.add(_output_key(output))
    return recovered


def _artifact_output(artifact: PipelineRunArtifactRow) -> RecoveredPipelineOutput:
    return RecoveredPipelineOutput(
        node_id=artifact["node_id"],
        artifact_kind=artifact["artifact_kind"],
        plane=artifact["plane"],
        status=artifact["status"],
        is_serving=artifact["is_serving"],
        ref=artifact["artifact_ref"],
        manifest=artifact["manifest"],
        artifact_id=artifact["id"],
        recovery_source="ARTIFACT_PASSPORT",
        evidence_id=artifact["id"],
    )


def _dataset_commit_output(
    row: PipelineDatasetCommitRow,
    output_node_ids: set[str],
) -> RecoveredPipelineOutput | None:
    node_id = _optional_text(row["metadata"].get("pipelineNodeId"))
    if node_id is None or node_id not in output_node_ids:
        return None
    descriptor_id = _optional_text(row["metadata"].get("descriptorId"))
    is_geospatial = descriptor_id == "output.geospatial"
    return RecoveredPipelineOutput(
        node_id=node_id,
        artifact_kind="geospatial_series" if is_geospatial else "dataset_version",
        plane="geospatial" if is_geospatial else "dataset",
        status="COMMITTED",
        is_serving=True,
        ref={
            "resourceRef" if is_geospatial else "datasetRef": row["dataset_ref"],
            "datasetId": row["dataset_id"],
            "versionId": row["version_id"],
            "transactionId": row["transaction_id"],
        },
        manifest={"metadata": dict(row["metadata"])},
        artifact_id=None,
        recovery_source="DATASET_TRANSACTION",
        evidence_id=row["transaction_id"],
    )


def _media_commit_output(
    rows: list[PipelineMediaCommitVersionRow],
    pipeline_run_id: str,
    output_node_ids: set[str],
) -> RecoveredPipelineOutput:
    node_id, generation, lineages = _media_commit_coordinates(rows, pipeline_run_id, output_node_ids)
    first = rows[0]
    return RecoveredPipelineOutput(
        node_id=node_id,
        artifact_kind="media_set_selection",
        plane="media",
        status="COMMITTED",
        is_serving=True,
        ref={
            "mediaSetRef": first["media_set_ref"],
            "mediaSetId": first["media_set_id"],
            "mediaTransactionId": first["transaction_id"],
            "mediaTransactionGeneration": generation,
            "mediaItemVersionIds": [row["media_item_version_id"] for row in rows],
        },
        manifest={
            "itemCount": len(rows),
            "commitKind": "SERVING_ASSET",
            "transactionGeneration": generation,
            "lineage": [dict(lineage) for lineage in lineages],
        },
        artifact_id=None,
        recovery_source="MEDIA_TRANSACTION",
        evidence_id=first["transaction_id"],
    )


def _media_commit_coordinates(
    rows: list[PipelineMediaCommitVersionRow],
    pipeline_run_id: str,
    output_node_ids: set[str],
) -> tuple[str, int, list[Mapping[str, object]]]:
    lineages = [_media_pipeline_lineage(row) for row in rows]
    node_ids = {_optional_text(lineage.get("pipelineNodeId")) for lineage in lineages}
    run_ids = {_optional_text(lineage.get("pipelineRunId")) for lineage in lineages}
    descriptors = {_optional_text(lineage.get("descriptorId")) for lineage in lineages}
    if len(node_ids) != 1 or node_ids - output_node_ids or run_ids != {pipeline_run_id}:
        raise _invalid_media_recovery(rows)
    if descriptors != {"output.media_set"}:
        raise _invalid_media_recovery(rows)
    node_id = next(iter(node_ids))
    generation = _consistent_media_generation(lineages, rows)
    if node_id is None:
        raise _invalid_media_recovery(rows)
    return node_id, generation, lineages


def _consistent_media_generation(
    lineages: list[Mapping[str, object]],
    rows: list[PipelineMediaCommitVersionRow],
) -> int:
    generation = lineages[0].get("transactionGeneration")
    if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
        raise _invalid_media_recovery(rows)
    if any(lineage.get("transactionGeneration") != generation for lineage in lineages[1:]):
        raise _invalid_media_recovery(rows)
    return generation


def _media_pipeline_lineage(row: PipelineMediaCommitVersionRow) -> Mapping[str, object]:
    source_ref = row["source_ref"]
    lineage = source_ref.get("pipelineOutput") if isinstance(source_ref, Mapping) else None
    if isinstance(lineage, Mapping):
        return lineage
    raise _invalid_media_recovery([row])


def _invalid_media_recovery(rows: list[PipelineMediaCommitVersionRow]) -> InvariantViolation:
    return InvariantViolation(
        "committed pipeline Media Set transaction has inconsistent recovery lineage",
        details={"media_transaction_id": rows[0]["transaction_id"]},
    )


def _output_key(output: RecoveredPipelineOutput) -> tuple[str, str]:
    for field in ("transactionId", "mediaTransactionId", "versionId"):
        value = _optional_text(output.ref.get(field))
        if value is not None:
            return output.node_id, f"{field}:{value}"
    return output.node_id, f"artifactKind:{output.artifact_kind}"


def reconciled_pipeline_output(artifact: RecoveredPipelineOutput) -> dict[str, object]:
    ref = dict(artifact.ref)
    if artifact.artifact_id is not None:
        ref["artifactId"] = artifact.artifact_id
    output: dict[str, object] = {
        "nodeId": artifact.node_id,
        "artifactKind": artifact.artifact_kind,
        "plane": artifact.plane,
        "status": artifact.status,
        "commitKind": "SERVING_ASSET" if artifact.is_serving else "GOVERNED_CANDIDATE",
        "isServing": artifact.is_serving,
        "ref": ref,
        "manifest": dict(artifact.manifest),
    }
    if artifact.artifact_id is None:
        output["artifactEvidence"] = {
            "status": "RECONCILIATION_REQUIRED",
            "isDurableRunOutput": True,
            "recoverySource": artifact.recovery_source,
            "evidenceId": artifact.evidence_id,
        }
    return output


def single_serving_dataset_fields(
    artifacts: list[RecoveredPipelineOutput],
) -> tuple[str | None, str | None]:
    matches = [
        artifact for artifact in artifacts if artifact.artifact_kind == "dataset_version" and artifact.is_serving
    ]
    if len(matches) != 1:
        return None, None
    ref = matches[0].ref
    return _optional_text(ref.get("datasetRef")), _optional_text(ref.get("versionId"))


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
