"""Bridge in-memory Graph v2 artifacts to durable execution evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_graph_contracts import (
    PipelineArtifactKind,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_records import (
    pipeline_graph_v2_artifact_idempotency_key,
    pipeline_graph_v2_input_artifact,
)
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    PipelineGraphV2ArtifactSpec,
    PipelineGraphV2InputArtifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeEdge,
    PipelineV2RuntimeNode,
)
from foundry_lite.domain.errors import InvariantViolation

JsonObject = dict[str, object]


def runtime_artifact_spec(
    run_id: str,
    artifact: PipelineV2RuntimeArtifact,
) -> PipelineGraphV2ArtifactSpec:
    """Build one immutable artifact passport for the generic evidence writer."""

    kind = _artifact_kind(artifact.artifact_kind)
    committed_at = artifact.committed_at or _now()
    pins = _artifact_pins(artifact.manifest)
    return PipelineGraphV2ArtifactSpec(
        port_id=artifact.port_id,
        artifact_kind=kind,
        plane=artifact.plane,
        artifact_ref=artifact.artifact_ref,
        manifest=artifact.manifest,
        security_envelope=artifact.security_envelope,
        content_fingerprint=artifact.content_fingerprint,
        status=artifact.status,
        is_serving=artifact.is_serving,
        idempotency_key=pipeline_graph_v2_artifact_idempotency_key(
            run_id=run_id,
            node_id=artifact.node_id,
            port_id=artifact.port_id,
            content_fingerprint=artifact.content_fingerprint,
        ),
        committed_at=committed_at,
        resource_ref=_resource_ref(artifact.artifact_ref),
        pins=pins,
        row_count=len(artifact.items) if kind == PipelineArtifactKind.DATASET_VERSION else None,
        item_count=len(artifact.items) if kind != PipelineArtifactKind.DATASET_VERSION else None,
        byte_count=_byte_count(artifact.items),
    )


def runtime_input_evidence(
    node: PipelineV2RuntimeNode,
    edges: Sequence[PipelineV2RuntimeEdge],
    persisted: Mapping[str, PipelineRunArtifactRow],
) -> tuple[PipelineGraphV2InputArtifact, ...]:
    """Resolve exact upstream evidence rows through named target ports."""

    incoming = sorted(
        (edge for edge in edges if edge.target_node_id == node.node_id),
        key=lambda edge: (edge.target_port_id, edge.source_node_id, edge.edge_id),
    )
    return tuple(_input_evidence(edge, persisted) for edge in incoming)


def available_runtime_input_evidence(
    node: PipelineV2RuntimeNode,
    edges: Sequence[PipelineV2RuntimeEdge],
    persisted: Mapping[str, PipelineRunArtifactRow],
) -> tuple[PipelineGraphV2InputArtifact, ...]:
    """Return only upstream passports that exist when a node must be skipped."""

    incoming = sorted(
        (edge for edge in edges if edge.target_node_id == node.node_id and edge.source_node_id in persisted),
        key=lambda edge: (edge.target_port_id, edge.source_node_id, edge.edge_id),
    )
    return tuple(_input_evidence(edge, persisted) for edge in incoming)


def runtime_output_payload(
    artifact: PipelineV2RuntimeArtifact,
    persisted: PipelineRunArtifactRow,
) -> JsonObject:
    """Project a terminal artifact into the public run output contract."""

    commit_kind = "SERVING_ASSET" if artifact.is_serving else str(artifact.manifest.get("commitKind") or "INTERMEDIATE")
    return {
        "nodeId": artifact.node_id,
        "artifactKind": artifact.artifact_kind,
        "plane": artifact.plane,
        "status": artifact.status,
        "commitKind": commit_kind,
        "isServing": artifact.is_serving,
        "ref": {**dict(artifact.artifact_ref), "artifactId": persisted["id"]},
        "manifest": dict(artifact.manifest),
    }


def committed_output_reconciliation_payload(
    artifact: PipelineV2RuntimeArtifact,
    error: Mapping[str, object],
) -> JsonObject:
    """Preserve a serving commit when its secondary artifact passport write fails."""

    return {
        "nodeId": artifact.node_id,
        "artifactKind": artifact.artifact_kind,
        "plane": artifact.plane,
        "status": artifact.status,
        "commitKind": "SERVING_ASSET",
        "isServing": artifact.is_serving,
        "ref": dict(artifact.artifact_ref),
        "manifest": dict(artifact.manifest),
        "artifactEvidence": {
            "status": "RECONCILIATION_REQUIRED",
            "isDurableRunOutput": True,
            "error": dict(error),
        },
    }


def runtime_node_timeline(
    node: PipelineV2RuntimeNode,
    persisted: PipelineRunArtifactRow,
) -> JsonObject:
    return {
        "event": "pipeline.node.succeeded",
        "at": persisted["committed_at"],
        "nodeId": node.node_id,
        "descriptorId": node.descriptor_id,
        "specVersion": node.spec_version,
        "operationRunType": "pipeline_graph_v2",
        "artifactId": persisted["id"],
        "artifactKind": persisted["artifact_kind"],
        "isServing": persisted["is_serving"],
    }


def _input_evidence(
    edge: PipelineV2RuntimeEdge,
    persisted: Mapping[str, PipelineRunArtifactRow],
) -> PipelineGraphV2InputArtifact:
    row = persisted.get(edge.source_node_id)
    if row is None:
        raise InvariantViolation(
            "pipeline upstream evidence artifact is unavailable",
            details={
                "nodeId": edge.target_node_id,
                "sourceNodeId": edge.source_node_id,
            },
        )
    if row["port_id"] != edge.source_port_id:
        raise InvariantViolation(
            "pipeline upstream evidence port does not match the plan",
            details={"edgeId": edge.edge_id},
        )
    return pipeline_graph_v2_input_artifact(row, target_port_id=edge.target_port_id)


def _artifact_kind(value: str) -> PipelineArtifactKind:
    try:
        return PipelineArtifactKind(value)
    except ValueError as exc:
        raise InvariantViolation(
            "pipeline runtime emitted an unknown artifact kind",
            details={"artifactKind": value},
        ) from exc


def _artifact_pins(manifest: Mapping[str, object]) -> JsonObject:
    pin_fields = (
        "processorId",
        "model",
        "embeddingModelVersion",
        "modelAlias",
        "expectedModelId",
        "expectedModelRevision",
        "promptVersionId",
        "outputSchema",
        "versionPins",
    )
    return {field: manifest[field] for field in pin_fields if field in manifest}


def _resource_ref(artifact_ref: Mapping[str, object]) -> str | None:
    for field in ("datasetRef", "mediaSetRef", "indexRef", "mappingRef", "generation"):
        value = artifact_ref.get(field)
        if isinstance(value, str) and value:
            return value
    return None


def _byte_count(items: Sequence[Mapping[str, object]]) -> int | None:
    if not items:
        return None
    total = 0
    for item in items:
        value = item.get("byteSize")
        if not isinstance(value, int):
            return None
        total += value
    return total
