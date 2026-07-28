"""Record and passport builders for generic Graph v2 execution evidence."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRecord,
    PipelineNodeRunRecord,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _json_hash, _new_id
from foundry_lite.application.services.pipeline_execution_contracts import (
    PipelineArtifactManifest,
    PipelineArtifactRef,
    pipeline_artifact_manifest_payload,
    thaw_json_value,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.application.services.pipeline_graph_v2_execution_evidence_types import (
    JsonObject,
    PipelineGraphV2ArtifactSpec,
    PipelineGraphV2AttemptContext,
    PipelineGraphV2InputArtifact,
    input_artifact_payloads,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, ValidationFailed


def pipeline_graph_v2_input_artifact(
    row: PipelineRunArtifactRow,
    *,
    target_port_id: str,
) -> PipelineGraphV2InputArtifact:
    """Convert a committed artifact row into an exact downstream edge passport."""

    try:
        artifact_kind = PipelineArtifactKind(row["artifact_kind"])
    except ValueError as exc:
        raise ValidationFailed(
            "pipeline input artifact kind is not part of Graph v2",
            details={"artifactKind": row["artifact_kind"]},
        ) from exc
    return PipelineGraphV2InputArtifact(
        artifact_id=str(row["id"]),
        artifact_kind=artifact_kind,
        plane=str(row["plane"]),
        source_node_id=str(row["node_id"]),
        source_port_id=str(row["port_id"]),
        target_port_id=target_port_id,
        artifact_ref=dict(row["artifact_ref"]),
        manifest=dict(row["manifest"]),
        content_fingerprint=str(row["content_fingerprint"]),
        security_envelope=dict(row["security_envelope"]),
        status=str(row["status"]),
        is_serving=bool(row["is_serving"]),
    )


def pipeline_graph_v2_content_fingerprint(
    *,
    artifact_ref: Mapping[str, object],
    manifest: Mapping[str, object],
    pins: Mapping[str, object],
) -> str:
    """Fingerprint domain coordinates before they are wrapped in execution metadata."""

    return _json_hash(
        {
            "artifactRef": dict(artifact_ref),
            "manifest": dict(manifest),
            "pins": dict(pins),
        }
    )


def pipeline_graph_v2_artifact_idempotency_key(
    *,
    run_id: str,
    node_id: str,
    port_id: str,
    content_fingerprint: str,
) -> str:
    return f"{run_id}:{node_id}:{port_id}:{content_fingerprint}"


def graph_v2_node_run_record(
    *,
    ctx: RequestContext,
    run_id: str,
    node_id: str,
    descriptor_id: str,
    spec_version: int,
    input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
    now: str,
) -> PipelineNodeRunRecord:
    return PipelineNodeRunRecord(
        node_run_id=_new_id("pnode"),
        tenant_id=ctx.tenant_id,
        run_id=run_id,
        node_id=node_id,
        descriptor_id=descriptor_id,
        spec_version=str(spec_version),
        input_artifacts=input_artifact_payloads(input_artifacts),
        created_at=now,
    )


def graph_v2_attempt_record(
    *,
    ctx: RequestContext,
    node_run_id: str,
    attempt_number: int,
    descriptor_id: str,
    spec_version: int,
    executor_profile: str,
    input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
    now: str,
) -> PipelineNodeAttemptRecord:
    return PipelineNodeAttemptRecord(
        attempt_id=_new_id("pattempt"),
        tenant_id=ctx.tenant_id,
        node_run_id=node_run_id,
        attempt_number=attempt_number,
        executor_profile=executor_profile,
        input_manifest=graph_v2_input_manifest(
            ctx=ctx,
            descriptor_id=descriptor_id,
            spec_version=spec_version,
            executor_profile=executor_profile,
            input_artifacts=input_artifacts,
        ),
        started_at=now,
    )


def graph_v2_input_manifest(
    *,
    ctx: RequestContext,
    descriptor_id: str,
    spec_version: int,
    executor_profile: str,
    input_artifacts: tuple[PipelineGraphV2InputArtifact, ...],
) -> JsonObject:
    return {
        "descriptorId": descriptor_id,
        "specVersion": spec_version,
        "executorProfile": executor_profile,
        "requestId": ctx.request_id,
        "artifacts": input_artifact_payloads(input_artifacts),
    }


def graph_v2_artifact_record(
    *,
    ctx: RequestContext,
    run_id: str,
    attempt: PipelineGraphV2AttemptContext,
    spec: PipelineGraphV2ArtifactSpec,
    artifact_id: str,
    now: str,
) -> PipelineRunArtifactRecord:
    return PipelineRunArtifactRecord(
        artifact_id=artifact_id,
        tenant_id=ctx.tenant_id,
        run_id=run_id,
        node_run_id=attempt.node_run_id,
        node_id=attempt.node_id,
        port_id=spec.port_id,
        artifact_kind=spec.artifact_kind.value,
        plane=spec.plane,
        artifact_ref=_thaw_object(spec.artifact_ref),
        manifest=_artifact_manifest(ctx, attempt, spec, artifact_id),
        content_fingerprint=spec.content_fingerprint,
        security_envelope=_thaw_object(spec.security_envelope),
        status=spec.status,
        is_serving=spec.is_serving,
        idempotency_key=spec.idempotency_key,
        committed_at=spec.committed_at,
        created_at=now,
    )


def graph_v2_output_artifact_ref(
    row: PipelineRunArtifactRow,
    attempt: PipelineGraphV2AttemptContext,
) -> JsonObject:
    return {
        "artifactId": row["id"],
        "artifactKind": row["artifact_kind"],
        "plane": row["plane"],
        "nodeId": row["node_id"],
        "portId": row["port_id"],
        "descriptorId": attempt.descriptor_id,
        "specVersion": attempt.spec_version,
        "artifactRef": dict(row["artifact_ref"]),
        "contentFingerprint": row["content_fingerprint"],
        "securityEnvelope": dict(row["security_envelope"]),
        "status": row["status"],
        "isServing": row["is_serving"],
    }


def require_artifact_record_matches(
    row: PipelineRunArtifactRow,
    record: PipelineRunArtifactRecord,
) -> None:
    expected = _artifact_record_semantics(record)
    actual = _artifact_row_semantics(row)
    if actual != expected:
        raise ConflictDetected(
            "pipeline artifact idempotency key was reused with different evidence",
            details={
                "idempotencyKey": record.idempotency_key,
                "artifactId": row["id"],
            },
        )


def _artifact_manifest(
    ctx: RequestContext,
    attempt: PipelineGraphV2AttemptContext,
    spec: PipelineGraphV2ArtifactSpec,
    artifact_id: str,
) -> JsonObject:
    artifact = PipelineArtifactRef(
        artifact_id=artifact_id,
        artifact_kind=spec.artifact_kind,
        producer_node_id=attempt.node_id,
        producer_port_id=spec.port_id,
        descriptor_id=attempt.descriptor_id,
        spec_version=attempt.spec_version,
        resource_ref=spec.resource_ref,
    )
    manifest = PipelineArtifactManifest(
        artifact=artifact,
        manifest_version=1,
        content_fingerprint=spec.content_fingerprint,
        metadata=_artifact_metadata(ctx, attempt, spec),
        security_markings=_security_markings(spec.security_envelope),
        row_count=spec.row_count,
        item_count=spec.item_count,
        byte_count=spec.byte_count,
    )
    return pipeline_artifact_manifest_payload(manifest)


def _artifact_metadata(
    ctx: RequestContext,
    attempt: PipelineGraphV2AttemptContext,
    spec: PipelineGraphV2ArtifactSpec,
) -> JsonObject:
    return {
        **_thaw_object(spec.manifest),
        "descriptorId": attempt.descriptor_id,
        "specVersion": attempt.spec_version,
        "executorProfile": attempt.executor_profile,
        "nodeRunId": attempt.node_run_id,
        "attemptId": attempt.attempt_id,
        "attemptNumber": attempt.attempt_number,
        "requestId": ctx.request_id,
        "inputArtifacts": input_artifact_payloads(attempt.input_artifacts),
        "pins": thaw_json_value(spec.pins),
    }


def _thaw_object(value: Mapping[str, object]) -> JsonObject:
    return cast(JsonObject, thaw_json_value(value))


def _security_markings(envelope: Mapping[str, object]) -> tuple[str, ...]:
    classification = str(envelope["classification"]).strip().upper()
    raw_markings = envelope.get("markings")
    markings = (
        tuple(str(value).strip() for value in raw_markings if str(value).strip())
        if isinstance(raw_markings, (list, tuple))
        else ()
    )
    return tuple(dict.fromkeys((classification, *markings)))


def _artifact_record_semantics(record: PipelineRunArtifactRecord) -> JsonObject:
    return {
        "runId": record.run_id,
        "nodeRunId": record.node_run_id,
        "nodeId": record.node_id,
        "portId": record.port_id,
        "artifactKind": record.artifact_kind,
        "plane": record.plane,
        "artifactRef": record.artifact_ref,
        "manifest": record.manifest,
        "contentFingerprint": record.content_fingerprint,
        "securityEnvelope": record.security_envelope,
        "status": record.status,
        "isServing": record.is_serving,
        "idempotencyKey": record.idempotency_key,
        "committedAt": record.committed_at,
    }


def _artifact_row_semantics(row: PipelineRunArtifactRow) -> JsonObject:
    return {
        "runId": row["run_id"],
        "nodeRunId": row["node_run_id"],
        "nodeId": row["node_id"],
        "portId": row["port_id"],
        "artifactKind": row["artifact_kind"],
        "plane": row["plane"],
        "artifactRef": row["artifact_ref"],
        "manifest": row["manifest"],
        "contentFingerprint": row["content_fingerprint"],
        "securityEnvelope": row["security_envelope"],
        "status": row["status"],
        "isServing": row["is_serving"],
        "idempotencyKey": row["idempotency_key"],
        "committedAt": row["committed_at"],
    }
