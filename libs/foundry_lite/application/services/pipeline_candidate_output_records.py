"""Immutable record builders for governed Pipeline output candidates."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.pipeline_execution_repository import (
    PipelineNodeAttemptRecord,
    PipelineNodeRunRow,
    PipelineRunArtifactRecord,
    PipelineRunArtifactRow,
)
from foundry_lite.application.primitives import _json_hash, _new_id
from foundry_lite.application.services.pipeline_execution_contracts import (
    PipelineArtifactManifest,
    PipelineArtifactRef,
    pipeline_artifact_manifest_payload,
)
from foundry_lite.application.services.pipeline_node_evidence_records import inherited_security_envelope
from foundry_lite.application.services.pipeline_output_candidate_contracts import (
    PipelineOutputCandidatePolicy,
    require_candidate_policy,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PipelineCandidateStage:
    node_id: str
    descriptor_id: str
    spec_version: int
    resource_ref: str
    config: JsonObject
    policy: PipelineOutputCandidatePolicy
    stage_fingerprint: str


@dataclass(frozen=True, slots=True)
class ValidatedPipelineCandidate:
    stage: PipelineCandidateStage
    candidate_id: str
    content_fingerprint: str
    input_artifacts: tuple[JsonObject, ...]
    security_envelope: JsonObject
    manifest: JsonObject


@dataclass(frozen=True, slots=True)
class PipelineCandidateCommitResult:
    output: JsonObject
    timeline_event: JsonObject


def stage_pipeline_candidate(item: Mapping[str, object]) -> PipelineCandidateStage:
    descriptor_id = _required_text(item, "descriptorId")
    policy = require_candidate_policy(descriptor_id)
    resource_ref = _required_text(item, "resourceRef")
    raw_config = item.get("config")
    config = dict(raw_config) if isinstance(raw_config, Mapping) else {}
    spec_version = _candidate_spec_version(item)
    coordinates = {
        "nodeId": _required_text(item, "nodeId"),
        "descriptorId": descriptor_id,
        "specVersion": spec_version,
        "resourceRef": resource_ref,
        "config": config,
    }
    _require_item_matches_policy(item, policy)
    return PipelineCandidateStage(
        node_id=str(coordinates["nodeId"]),
        descriptor_id=descriptor_id,
        spec_version=spec_version,
        resource_ref=resource_ref,
        config=config,
        policy=policy,
        stage_fingerprint=_json_hash(coordinates),
    )


def validate_pipeline_candidate(
    stage: PipelineCandidateStage,
    inputs: tuple[JsonObject, ...],
    pins: Mapping[str, object],
) -> ValidatedPipelineCandidate:
    require_candidate_input_kind(stage.policy, inputs[0])
    classification = candidate_input_classification(inputs[0])
    security = inherited_security_envelope(classification, inputs)
    content_fingerprint = _content_fingerprint(stage, inputs, pins)
    candidate_id = f"pcandidate_{content_fingerprint[:24]}"
    manifest = _candidate_manifest(stage, inputs, security, pins, candidate_id, content_fingerprint)
    return ValidatedPipelineCandidate(
        stage=stage,
        candidate_id=candidate_id,
        content_fingerprint=content_fingerprint,
        input_artifacts=inputs,
        security_envelope=security,
        manifest=manifest,
    )


def candidate_attempt_record(
    ctx: RequestContext,
    node_run: PipelineNodeRunRow,
    candidate: ValidatedPipelineCandidate,
    now: str,
) -> PipelineNodeAttemptRecord:
    return PipelineNodeAttemptRecord(
        attempt_id=_new_id("pattempt"),
        tenant_id=ctx.tenant_id,
        node_run_id=str(node_run["id"]),
        attempt_number=1,
        executor_profile=candidate.stage.policy.validation_contract,
        input_manifest={
            "artifacts": list(candidate.input_artifacts),
            "candidateStageFingerprint": candidate.stage.stage_fingerprint,
        },
        started_at=now,
    )


def candidate_artifact_record(
    ctx: RequestContext,
    run_id: str,
    node_run: PipelineNodeRunRow,
    candidate: ValidatedPipelineCandidate,
    now: str,
) -> PipelineRunArtifactRecord:
    return PipelineRunArtifactRecord(
        artifact_id=_new_id("partifact"),
        tenant_id=ctx.tenant_id,
        run_id=run_id,
        node_run_id=str(node_run["id"]),
        node_id=candidate.stage.node_id,
        port_id=candidate.stage.policy.port_id,
        artifact_kind=candidate.stage.policy.artifact_kind.value,
        plane=candidate.stage.policy.plane,
        artifact_ref=candidate_artifact_ref(candidate),
        manifest=candidate.manifest,
        content_fingerprint=candidate.content_fingerprint,
        security_envelope=candidate.security_envelope,
        status="COMMITTED",
        is_serving=False,
        idempotency_key=f"{run_id}:{candidate.stage.node_id}:{candidate.stage.policy.port_id}:{candidate.candidate_id}",
        committed_at=now,
        created_at=now,
    )


def candidate_artifact_ref(candidate: ValidatedPipelineCandidate) -> JsonObject:
    ref: JsonObject = {
        "candidateId": candidate.candidate_id,
        "candidateType": candidate.stage.policy.candidate_type,
        "requestedResourceRef": candidate.stage.resource_ref,
        "servingState": "CANDIDATE_ONLY",
        "servingAssetCreated": False,
        "promotionRequired": True,
    }
    ref[candidate.stage.policy.resource_field] = candidate.stage.resource_ref
    return ref


def candidate_commit_result(
    candidate: ValidatedPipelineCandidate,
    artifact: PipelineRunArtifactRow,
) -> PipelineCandidateCommitResult:
    output: JsonObject = {
        "nodeId": candidate.stage.node_id,
        "artifactKind": candidate.stage.policy.artifact_kind.value,
        "plane": candidate.stage.policy.plane,
        "status": "COMMITTED",
        "commitKind": "GOVERNED_CANDIDATE",
        "isServing": False,
        "ref": {**dict(artifact["artifact_ref"]), "artifactId": artifact["id"]},
    }
    timeline: JsonObject = {
        "event": "pipeline.node.succeeded",
        "at": artifact["committed_at"],
        "nodeId": candidate.stage.node_id,
        "operationRunType": "pipeline_output_candidate",
        "artifactId": artifact["id"],
        "candidateId": candidate.candidate_id,
        "servingAssetCreated": False,
    }
    return PipelineCandidateCommitResult(output=output, timeline_event=timeline)


def candidate_event(
    candidate: ValidatedPipelineCandidate,
    artifact: PipelineRunArtifactRow,
) -> JsonObject:
    return {
        "runId": artifact["run_id"],
        "nodeId": artifact["node_id"],
        "artifactId": artifact["id"],
        "artifactKind": artifact["artifact_kind"],
        "candidateId": candidate.candidate_id,
        "descriptorId": candidate.stage.descriptor_id,
        "specVersion": candidate.stage.spec_version,
        "requestedResourceRef": candidate.stage.resource_ref,
        "contentFingerprint": candidate.content_fingerprint,
        "securityEnvelope": dict(candidate.security_envelope),
        "commitKind": "GOVERNED_CANDIDATE",
        "isServing": False,
        "servingAssetCreated": False,
        "promotionRequired": True,
    }


def matching_candidate_input_artifacts(
    edge: Mapping[str, object],
    artifacts: list[PipelineRunArtifactRow],
) -> list[PipelineRunArtifactRow]:
    return [
        artifact
        for artifact in artifacts
        if artifact["node_id"] == edge["sourceNodeId"]
        and artifact["port_id"] == edge["sourcePortId"]
        and artifact["status"] == "COMMITTED"
    ]


def require_candidate_input_kind(
    policy: PipelineOutputCandidatePolicy,
    artifact: Mapping[str, object],
) -> None:
    actual = str(artifact.get("artifactKind") or "")
    accepted = {kind.value for kind in policy.accepted_input_kinds}
    if actual not in accepted:
        raise ValidationFailed(
            "pipeline candidate input artifact kind is not accepted",
            details={"artifactKind": actual, "acceptedArtifactKinds": sorted(accepted)},
        )


def candidate_input_classification(artifact: Mapping[str, object]) -> str:
    envelope = artifact.get("securityEnvelope")
    if not isinstance(envelope, Mapping):
        raise ValidationFailed("pipeline candidate input security envelope is missing")
    classification = envelope.get("classification")
    if not isinstance(classification, str) or not classification.strip():
        return "UNCLASSIFIED"
    return classification.strip().upper()


def candidate_input_resource_id(artifact: Mapping[str, object]) -> str:
    for key in ("versionId", "candidateId", "artifactId"):
        value = artifact.get(key)
        if isinstance(value, str) and value:
            return value
    raise InvariantViolation("pipeline candidate lineage input resource id is missing")


def _require_item_matches_policy(
    item: Mapping[str, object],
    policy: PipelineOutputCandidatePolicy,
) -> None:
    expected = {
        "artifactKind": policy.artifact_kind.value,
        "plane": policy.plane,
        "portId": policy.port_id,
        "candidateType": policy.candidate_type,
    }
    actual = {key: item.get(key) for key in expected}
    if actual != expected:
        raise ValidationFailed(
            "pipeline governed candidate item does not match its pinned descriptor",
            details={"expected": expected, "actual": actual},
        )


def _candidate_manifest(
    stage: PipelineCandidateStage,
    inputs: tuple[JsonObject, ...],
    security: Mapping[str, object],
    pins: Mapping[str, object],
    candidate_id: str,
    content_fingerprint: str,
) -> JsonObject:
    spec = PipelineArtifactRef(
        artifact_id=f"artifact:{stage.node_id}:{stage.policy.port_id}",
        artifact_kind=stage.policy.artifact_kind,
        producer_node_id=stage.node_id,
        producer_port_id=stage.policy.port_id,
        descriptor_id=stage.descriptor_id,
        spec_version=stage.spec_version,
        resource_ref=stage.resource_ref,
    )
    manifest = PipelineArtifactManifest(
        artifact=spec,
        manifest_version=1,
        content_fingerprint=content_fingerprint,
        metadata=_manifest_metadata(stage, inputs, pins, candidate_id),
        security_markings=(str(security["classification"]),),
    )
    return pipeline_artifact_manifest_payload(manifest)


def _manifest_metadata(
    stage: PipelineCandidateStage,
    inputs: tuple[JsonObject, ...],
    pins: Mapping[str, object],
    candidate_id: str,
) -> JsonObject:
    return {
        "candidate": {
            "candidateId": candidate_id,
            "candidateType": stage.policy.candidate_type,
            "requestedResourceRef": stage.resource_ref,
            "servingAssetCreated": False,
            "promotionRequired": True,
            "servingGap": stage.policy.serving_gap,
        },
        "lifecycle": [
            {"phase": "stage", "status": "STAGED", "fingerprint": stage.stage_fingerprint},
            {"phase": "validate", "status": "VALIDATED", "contract": stage.policy.validation_contract},
            {"phase": "commit", "status": "COMMITTED", "commitKind": "GOVERNED_CANDIDATE"},
        ],
        "inputArtifacts": list(inputs),
        "pins": dict(pins),
        "config": dict(stage.config),
    }


def _content_fingerprint(
    stage: PipelineCandidateStage,
    inputs: tuple[JsonObject, ...],
    pins: Mapping[str, object],
) -> str:
    return _json_hash(
        {
            "stageFingerprint": stage.stage_fingerprint,
            "inputArtifacts": list(inputs),
            "pins": dict(pins),
            "validationContract": stage.policy.validation_contract,
        }
    )


def _required_text(item: Mapping[str, object], field: str) -> str:
    value = item.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("pipeline governed candidate field is required", details={"field": field})
    return value.strip()


def _candidate_spec_version(item: Mapping[str, object]) -> int:
    value = item.get("specVersion")
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return 1
