"""Immutable public contracts for Pipeline Builder v2 planning and execution."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from foundry_lite.application.services.pipeline_graph_contracts import (
    PipelineArtifactKind,
    PipelineNodeKind,
)
from foundry_lite.application.services.pipeline_source_contracts import (
    PipelineSourceContract,
    pipeline_source_contract_payload,
)
from foundry_lite.domain.errors import ValidationFailed

PipelineNodeRunStatus = Literal["queued", "running", "succeeded", "failed", "cancelled", "skipped"]
PipelinePreviewRunStatus = Literal["queued", "running", "succeeded", "partial", "failed", "cancelled"]
PipelineTriggerKind = Literal["manual", "cron", "interval", "data", "logic"]


@dataclass(frozen=True, slots=True)
class ComputeProfile:
    """Pinned compute coordinates without coupling a plan to one engine API."""

    profile_id: str
    engine: str
    engine_version: str
    capabilities: tuple[str, ...] = ()
    cpu_limit: float | None = None
    memory_mib: int | None = None
    timeout_seconds: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.profile_id, "compute profile id")
        _require_text(self.engine, "compute engine")
        _require_text(self.engine_version, "compute engine version")


@dataclass(frozen=True, slots=True)
class ModelRef:
    """Pinned model revision used by an execution deployment."""

    model_id: str
    model_version: str
    provider: str
    revision: str
    parameters_fingerprint: str
    executable_reference: str
    definition_snapshot: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        _require_text(self.model_id, "model id")
        _require_text(self.model_version, "model version")
        _require_text(self.provider, "model provider")
        _require_text(self.revision, "model revision")
        _require_text(self.parameters_fingerprint, "model parameters fingerprint")
        _require_text(self.executable_reference, "model executable reference")
        if self.provider == "trained_model" and not self.definition_snapshot:
            raise ValidationFailed("trained model definition snapshot is required")
        object.__setattr__(self, "definition_snapshot", _freeze_object(self.definition_snapshot))


@dataclass(frozen=True, slots=True)
class PipelineScheduleSpec:
    """Typed schedule input for manual, time, data, or logic triggers."""

    trigger_kind: PipelineTriggerKind
    timezone: str = "UTC"
    cron_expression: str | None = None
    interval_seconds: int | None = None
    start_at: str | None = None
    auto_pause_after_failures: int | None = None
    trigger_config: Mapping[str, object] = MappingProxyType({})
    is_enabled: bool = True

    def __post_init__(self) -> None:
        _require_text(self.timezone, "pipeline schedule timezone")
        _validate_schedule_coordinates(self)
        object.__setattr__(self, "trigger_config", _freeze_object(self.trigger_config))


@dataclass(frozen=True, slots=True)
class PipelineArtifactRef:
    """Logical artifact produced by one exact descriptor port."""

    artifact_id: str
    artifact_kind: PipelineArtifactKind
    producer_node_id: str
    producer_port_id: str
    descriptor_id: str
    spec_version: int
    resource_ref: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineArtifactManifest:
    """Durable evidence for one materialized or intermediate artifact."""

    artifact: PipelineArtifactRef
    manifest_version: int
    content_fingerprint: str
    metadata: Mapping[str, object] = MappingProxyType({})
    security_markings: tuple[str, ...] = ()
    row_count: int | None = None
    item_count: int | None = None
    byte_count: int | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_object(self.metadata))


@dataclass(frozen=True, slots=True)
class PipelineNodeAttempt:
    """One fenced attempt for a pipeline node run."""

    attempt_id: str
    node_run_id: str
    attempt_number: int
    status: PipelineNodeRunStatus
    fencing_token: int
    worker_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineNodeRun:
    """Node-level execution evidence independent of the selected runtime."""

    node_run_id: str
    pipeline_run_id: str
    node_id: str
    descriptor_id: str
    spec_version: int
    status: PipelineNodeRunStatus
    input_artifact_ids: tuple[str, ...] = ()
    output_artifact_ids: tuple[str, ...] = ()
    attempt_count: int = 0
    request_id: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class PipelinePreviewRun:
    """No-commit preview evidence for an unsaved canonical graph."""

    preview_run_id: str
    pipeline_id: str
    branch_id: str
    status: PipelinePreviewRunStatus
    graph_fingerprint: str
    target_node_ids: tuple[str, ...]
    limits: Mapping[str, object]
    artifacts: tuple[PipelineArtifactManifest, ...] = ()
    request_id: str | None = None
    is_commit_forbidden: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "limits", _freeze_object(self.limits))
        if not self.is_commit_forbidden:
            raise ValidationFailed("pipeline preview runs must forbid serving commits")


@dataclass(frozen=True, slots=True)
class PipelinePlanNode:
    """One engine-neutral node pinned to a descriptor specification."""

    node_id: str
    node_kind: PipelineNodeKind
    descriptor_id: str
    spec_version: int
    runtime_capability: str
    config: Mapping[str, object]

    def __post_init__(self) -> None:
        object.__setattr__(self, "config", _freeze_object(self.config))


@dataclass(frozen=True, slots=True)
class PipelinePlanEdge:
    """Named-port dependency retained in the execution plan."""

    edge_id: str
    source_node_id: str
    source_port_id: str
    target_node_id: str
    target_port_id: str


@dataclass(frozen=True, slots=True)
class PipelineExecutionPlan:
    """Immutable, engine-neutral plan compiled from a canonical v2 graph."""

    compiler_version: str
    graph_schema_version: int
    graph_fingerprint: str
    plan_fingerprint: str
    target_node_ids: tuple[str, ...]
    nodes: tuple[PipelinePlanNode, ...]
    edges: tuple[PipelinePlanEdge, ...]
    artifacts: tuple[PipelineArtifactRef, ...]
    result_artifact_ids: tuple[str, ...]
    source_contracts: tuple[PipelineSourceContract, ...] = ()
    validation_warnings: tuple[Mapping[str, object], ...] = ()
    compute_profile: ComputeProfile | None = None
    model_refs: tuple[ModelRef, ...] = ()

    def __post_init__(self) -> None:
        frozen = tuple(_freeze_object(warning) for warning in self.validation_warnings)
        object.__setattr__(self, "validation_warnings", frozen)


def pipeline_execution_plan_payload(plan: PipelineExecutionPlan) -> dict[str, object]:
    """Project an immutable plan into a durable JSON contract."""

    return {
        "compilerVersion": plan.compiler_version,
        "graphSchemaVersion": plan.graph_schema_version,
        "graphFingerprint": plan.graph_fingerprint,
        "planFingerprint": plan.plan_fingerprint,
        "targetNodeIds": list(plan.target_node_ids),
        "nodes": [_node_payload(node) for node in plan.nodes],
        "edges": [_edge_payload(edge) for edge in plan.edges],
        "artifacts": [_artifact_payload(artifact) for artifact in plan.artifacts],
        "resultArtifactIds": list(plan.result_artifact_ids),
        "sourceContracts": [pipeline_source_contract_payload(contract) for contract in plan.source_contracts],
        "validationWarnings": [thaw_json_value(warning) for warning in plan.validation_warnings],
        "computeProfile": _compute_payload(plan.compute_profile),
        "modelRefs": [_model_payload(model) for model in plan.model_refs],
    }


def pipeline_artifact_manifest_payload(manifest: PipelineArtifactManifest) -> dict[str, object]:
    """Project one immutable artifact manifest into the durable public JSON contract."""

    return {
        "manifestVersion": manifest.manifest_version,
        "artifact": _artifact_payload(manifest.artifact),
        "contentFingerprint": manifest.content_fingerprint,
        "metadata": thaw_json_value(manifest.metadata),
        "securityMarkings": list(manifest.security_markings),
        "rowCount": manifest.row_count,
        "itemCount": manifest.item_count,
        "byteCount": manifest.byte_count,
    }


def pipeline_plan_fingerprint(
    *,
    compiler_version: str,
    graph_fingerprint: str,
    target_node_ids: tuple[str, ...],
    nodes: tuple[PipelinePlanNode, ...],
    edges: tuple[PipelinePlanEdge, ...],
    artifacts: tuple[PipelineArtifactRef, ...],
    result_artifact_ids: tuple[str, ...],
    source_contracts: tuple[PipelineSourceContract, ...],
    compute_profile: ComputeProfile | None,
    model_refs: tuple[ModelRef, ...],
) -> str:
    """Hash every pinned execution coordinate in a stable JSON form."""

    payload = {
        "compilerVersion": compiler_version,
        "graphFingerprint": graph_fingerprint,
        "targetNodeIds": list(target_node_ids),
        "nodes": [_node_payload(node) for node in nodes],
        "edges": [_edge_payload(edge) for edge in edges],
        "artifacts": [_artifact_payload(artifact) for artifact in artifacts],
        "resultArtifactIds": list(result_artifact_ids),
        "sourceContracts": [pipeline_source_contract_payload(contract) for contract in source_contracts],
        "computeProfile": _compute_payload(compute_profile),
        "modelRefs": [_model_payload(model) for model in model_refs],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def thaw_json_value(value: object) -> object:
    """Return JSON-ready mutable containers for fingerprints and API payloads."""

    if isinstance(value, Mapping):
        return {str(key): thaw_json_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_json_value(item) for item in value]
    return value


def _node_payload(node: PipelinePlanNode) -> dict[str, object]:
    return {
        "nodeId": node.node_id,
        "kind": node.node_kind.value,
        "descriptorId": node.descriptor_id,
        "specVersion": node.spec_version,
        "runtimeCapability": node.runtime_capability,
        "config": thaw_json_value(node.config),
    }


def _edge_payload(edge: PipelinePlanEdge) -> dict[str, object]:
    return {
        "edgeId": edge.edge_id,
        "sourceNodeId": edge.source_node_id,
        "sourcePortId": edge.source_port_id,
        "targetNodeId": edge.target_node_id,
        "targetPortId": edge.target_port_id,
    }


def _artifact_payload(artifact: PipelineArtifactRef) -> dict[str, object]:
    return {
        "artifactId": artifact.artifact_id,
        "artifactKind": artifact.artifact_kind.value,
        "producerNodeId": artifact.producer_node_id,
        "producerPortId": artifact.producer_port_id,
        "descriptorId": artifact.descriptor_id,
        "specVersion": artifact.spec_version,
        "resourceRef": artifact.resource_ref,
    }


def _compute_payload(profile: ComputeProfile | None) -> object:
    if profile is None:
        return None
    return {
        "profileId": profile.profile_id,
        "engine": profile.engine,
        "engineVersion": profile.engine_version,
        "capabilities": list(profile.capabilities),
        "cpuLimit": profile.cpu_limit,
        "memoryMib": profile.memory_mib,
        "timeoutSeconds": profile.timeout_seconds,
    }


def _model_payload(model: ModelRef) -> dict[str, object]:
    return {
        "modelId": model.model_id,
        "modelVersion": model.model_version,
        "provider": model.provider,
        "revision": model.revision,
        "parametersFingerprint": model.parameters_fingerprint,
        "executableReference": model.executable_reference,
        "definitionSnapshot": thaw_json_value(model.definition_snapshot),
    }


def _freeze_object(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = {str(key): _freeze_value(item) for key, item in value.items()}
    return MappingProxyType(frozen)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_object(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValidationFailed(f"{field} is required")


def _validate_schedule_coordinates(spec: PipelineScheduleSpec) -> None:
    if spec.trigger_kind == "cron" and not (spec.cron_expression or "").strip():
        raise ValidationFailed("cron pipeline schedule requires cron_expression")
    if spec.trigger_kind == "interval" and (spec.interval_seconds or 0) < 1:
        raise ValidationFailed("interval pipeline schedule requires positive interval_seconds")
    if spec.auto_pause_after_failures is not None and spec.auto_pause_after_failures < 1:
        raise ValidationFailed("auto_pause_after_failures must be positive")
