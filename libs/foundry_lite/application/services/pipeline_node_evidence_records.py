"""Record builders for durable synchronous Pipeline Dataset artifacts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRecord,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import CommitResult, _json_hash, _new_id
from foundry_lite.application.services.pipeline_execution_contracts import (
    PipelineArtifactManifest,
    PipelineArtifactRef,
    pipeline_artifact_manifest_payload,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.application.services.pipeline_node_execution_evidence_types import (
    PipelineNodeAttemptContext,
)
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification as _classification,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class _DatasetArtifactRecordInputs:
    ctx: RequestContext
    run_id: str
    node_run_id: str
    node_id: str
    spec: Mapping[str, object]
    dataset_ref: str
    artifact_ref: JsonObject
    metadata: Mapping[str, object]
    security_envelope: Mapping[str, object]
    idempotency_key: str
    committed_at: str
    now: str


def source_artifact_record(
    *,
    ctx: RequestContext,
    run_id: str,
    node_run_id: str,
    spec: Mapping[str, object],
    dataset_ref: str,
    version: Mapping[str, object],
    security_envelope: Mapping[str, object],
    idempotency_key: str,
    now: str,
) -> PipelineRunArtifactRecord:
    artifact_ref: JsonObject = {"datasetRef": dataset_ref, "versionId": str(version["id"])}
    metadata = dataset_version_manifest(version)
    return _committed_dataset_artifact_record(
        _DatasetArtifactRecordInputs(
            ctx=ctx,
            run_id=run_id,
            node_run_id=node_run_id,
            node_id=str(spec["producerNodeId"]),
            spec=spec,
            dataset_ref=dataset_ref,
            artifact_ref=artifact_ref,
            metadata=metadata,
            security_envelope=security_envelope,
            idempotency_key=idempotency_key,
            committed_at=str(version["created_at"]),
            now=now,
        )
    )


def unstarted_attempt_record(
    *,
    tenant_id: str,
    node_run_id: str,
    executor_profile: str,
    started_at: str,
) -> PipelineNodeAttemptRecord:
    return PipelineNodeAttemptRecord(
        attempt_id=_new_id("pattempt"),
        tenant_id=tenant_id,
        node_run_id=node_run_id,
        attempt_number=1,
        executor_profile=executor_profile,
        input_manifest={"resolutionFailedBeforeExecution": True},
        started_at=started_at,
    )


def output_artifact_record(
    *,
    ctx: RequestContext,
    run_id: str,
    attempt: PipelineNodeAttemptContext,
    spec: Mapping[str, object],
    result: CommitResult,
    version: Mapping[str, object],
    pins: Mapping[str, object],
    security_envelope: Mapping[str, object],
    now: str,
) -> PipelineRunArtifactRecord:
    artifact_ref: JsonObject = {
        "datasetRef": result.dataset_ref,
        "versionId": result.version_id,
        "transformRunId": attempt.transform_plan.run_id,
    }
    metadata = output_artifact_manifest(attempt, spec, result, version, pins)
    return _committed_dataset_artifact_record(
        _DatasetArtifactRecordInputs(
            ctx=ctx,
            run_id=run_id,
            node_run_id=attempt.node_run_id,
            node_id=attempt.node_id,
            spec=spec,
            dataset_ref=result.dataset_ref,
            artifact_ref=artifact_ref,
            metadata=metadata,
            security_envelope=security_envelope,
            idempotency_key=artifact_idempotency_key(run_id, spec, result.version_id),
            committed_at=str(version["created_at"]),
            now=now,
        )
    )


def output_artifact_manifest(
    attempt: PipelineNodeAttemptContext,
    spec: Mapping[str, object],
    result: CommitResult,
    version: Mapping[str, object],
    pins: Mapping[str, object],
) -> JsonObject:
    return {
        **dataset_version_manifest(version),
        "descriptorId": spec["descriptorId"],
        "specVersion": spec["specVersion"],
        "inputArtifacts": list(attempt.input_artifacts),
        "pins": dict(pins),
        "schemaHash": result.schema_hash,
    }


def dataset_version_manifest(version: Mapping[str, object]) -> JsonObject:
    return {
        "datasetId": version["dataset_id"],
        "versionNumber": version["version_number"],
        "transactionId": version["transaction_id"],
        "schemaVersion": version["schema_version"],
        "manifestUri": version["manifest_uri"],
        "rowCount": version["row_count"],
        "byteSize": version["byte_size"],
    }


def _committed_dataset_artifact_record(inputs: _DatasetArtifactRecordInputs) -> PipelineRunArtifactRecord:
    content_fingerprint = _dataset_content_fingerprint(inputs.artifact_ref, inputs.metadata)
    manifest = _dataset_artifact_manifest(
        spec=inputs.spec,
        dataset_ref=inputs.dataset_ref,
        metadata=inputs.metadata,
        security_envelope=inputs.security_envelope,
        content_fingerprint=content_fingerprint,
    )
    return PipelineRunArtifactRecord(
        artifact_id=_new_id("partifact"),
        tenant_id=inputs.ctx.tenant_id,
        run_id=inputs.run_id,
        node_run_id=inputs.node_run_id,
        node_id=inputs.node_id,
        port_id=str(inputs.spec["producerPortId"]),
        artifact_kind="dataset_version",
        plane="dataset",
        artifact_ref=inputs.artifact_ref,
        manifest=manifest,
        content_fingerprint=content_fingerprint,
        security_envelope=dict(inputs.security_envelope),
        status="COMMITTED",
        is_serving=True,
        idempotency_key=inputs.idempotency_key,
        committed_at=inputs.committed_at,
        created_at=inputs.now,
    )


def _dataset_artifact_manifest(
    *,
    spec: Mapping[str, object],
    dataset_ref: str,
    metadata: Mapping[str, object],
    security_envelope: Mapping[str, object],
    content_fingerprint: str,
) -> JsonObject:
    artifact = PipelineArtifactRef(
        artifact_id=str(spec["artifactId"]),
        artifact_kind=PipelineArtifactKind.DATASET_VERSION,
        producer_node_id=str(spec["producerNodeId"]),
        producer_port_id=str(spec["producerPortId"]),
        descriptor_id=str(spec["descriptorId"]),
        spec_version=_required_int(spec, "specVersion"),
        resource_ref=str(spec.get("resourceRef") or dataset_ref),
    )
    manifest = PipelineArtifactManifest(
        artifact=artifact,
        manifest_version=1,
        content_fingerprint=content_fingerprint,
        metadata=dict(metadata),
        security_markings=(_classification(security_envelope.get("classification")),),
        row_count=_optional_int(metadata.get("rowCount")),
        byte_count=_optional_int(metadata.get("byteSize")),
    )
    return pipeline_artifact_manifest_payload(manifest)


def _dataset_content_fingerprint(
    artifact_ref: Mapping[str, object],
    metadata: Mapping[str, object],
) -> str:
    return _json_hash({"artifactRef": dict(artifact_ref), "metadata": dict(metadata)})


def _optional_int(value: object) -> int | None:
    return int(value) if isinstance(value, int) else None


def _required_int(value: Mapping[str, object], field: str) -> int:
    result = value.get(field)
    if isinstance(result, int) and not isinstance(result, bool):
        return result
    raise ValidationFailed("pipeline artifact integer coordinate is invalid", details={"field": field})


def source_security_envelope(dataset: Mapping[str, object]) -> JsonObject:
    return {
        "classification": _classification(dataset.get("classification")),
        "inheritance": "source",
        "sourceDatasetId": dataset["id"],
    }


def inherited_security_envelope(
    output_classification: object,
    input_artifacts: tuple[JsonObject, ...],
) -> JsonObject:
    sources = sorted({_artifact_classification(artifact) for artifact in input_artifacts})
    if not sources:
        sources = ["UNCLASSIFIED"]
    output = _classification(output_classification)
    _require_not_weaker(output, sources)
    return {
        "classification": output,
        "inheritance": "preserved",
        "sourceClassifications": sources,
    }


def artifact_idempotency_key(
    run_id: str,
    spec: Mapping[str, object],
    version_id: str,
) -> str:
    return f"{run_id}:{spec['producerNodeId']}:{spec['producerPortId']}:{version_id}"


def node_artifact_ref(row: PipelineRunArtifactRow) -> JsonObject:
    ref = row["artifact_ref"]
    return {
        "artifactId": row["id"],
        "artifactKind": row["artifact_kind"],
        "nodeId": row["node_id"],
        "portId": row["port_id"],
        "artifactRef": dict(ref),
        "datasetRef": ref.get("datasetRef"),
        "versionId": ref.get("versionId"),
        "transformRunId": ref.get("transformRunId"),
        "candidateId": ref.get("candidateId"),
        "candidateType": ref.get("candidateType"),
        "requestedResourceRef": ref.get("requestedResourceRef"),
        "servingState": ref.get("servingState"),
    }


def input_artifact_ref(
    row: PipelineRunArtifactRow,
    edge: Mapping[str, object],
) -> JsonObject:
    payload = node_artifact_ref(row)
    payload["sourceNodeId"] = edge.get("sourceNodeId", row["node_id"])
    payload["sourcePortId"] = edge.get("sourcePortId", row["port_id"])
    payload["targetPortId"] = edge.get("targetPortId")
    payload["securityEnvelope"] = dict(row["security_envelope"])
    return payload


def _artifact_classification(artifact: Mapping[str, object]) -> str:
    envelope = artifact.get("securityEnvelope")
    if not isinstance(envelope, Mapping):
        raise ValidationFailed("pipeline input artifact security envelope is missing")
    return _classification(envelope.get("classification"))


def _require_not_weaker(output: str, sources: list[str]) -> None:
    known_sources = all(source in _CLASSIFICATION_RANK for source in sources)
    if output in _CLASSIFICATION_RANK and known_sources:
        required_rank = max(_CLASSIFICATION_RANK[source] for source in sources)
        if _CLASSIFICATION_RANK[output] >= required_rank:
            return
    elif all(source == output for source in sources):
        return
    raise ValidationFailed(
        "pipeline output classification would weaken an input artifact",
        details={"outputClassification": output, "sourceClassifications": sources},
    )
