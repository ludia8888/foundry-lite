"""Typed inputs for generic Graph v2 node execution evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import cast

from foundry_lite.application.services.pipeline_execution_contracts import thaw_json_value
from foundry_lite.application.services.pipeline_graph_contracts import PipelineArtifactKind
from foundry_lite.domain.classification import (
    CLASSIFICATION_RANKS as _CLASSIFICATION_RANK,
)
from foundry_lite.domain.classification import (
    normalize_classification,
)
from foundry_lite.domain.errors import ValidationFailed

JsonObject = dict[str, object]

_ARTIFACT_PLANES = {
    PipelineArtifactKind.DATASET_VERSION: "dataset",
    PipelineArtifactKind.VIRTUAL_TABLE: "virtual",
    PipelineArtifactKind.MEDIA_SET_SELECTION: "media",
    PipelineArtifactKind.MEDIA_DERIVATIVE_SET: "media",
    PipelineArtifactKind.CONTENT_UNIT_SET: "content",
    PipelineArtifactKind.VECTOR_INDEX_GENERATION: "index",
    PipelineArtifactKind.STREAM_CHECKPOINT: "stream",
    PipelineArtifactKind.GEOSPATIAL_SERIES: "geospatial",
    PipelineArtifactKind.ONTOLOGY_MAPPING: "ontology",
}


def validated_error_payload(error: Mapping[str, object]) -> JsonObject:
    payload = cast(JsonObject, thaw_json_value(error))
    if not payload:
        raise ValidationFailed("pipeline node error evidence cannot be empty")
    return payload


@dataclass(frozen=True, slots=True)
class PipelineGraphV2InputArtifact:
    """One committed upstream artifact connected through exact named ports."""

    artifact_id: str
    artifact_kind: PipelineArtifactKind
    plane: str
    source_node_id: str
    source_port_id: str
    target_port_id: str
    artifact_ref: Mapping[str, object]
    manifest: Mapping[str, object]
    content_fingerprint: str
    security_envelope: Mapping[str, object]
    status: str
    is_serving: bool

    def __post_init__(self) -> None:
        _require_text(self.artifact_id, "pipeline input artifact id")
        _require_text(self.source_node_id, "pipeline input source node id")
        _require_text(self.source_port_id, "pipeline input source port id")
        _require_text(self.target_port_id, "pipeline input target port id")
        _require_text(self.content_fingerprint, "pipeline input content fingerprint")
        _require_plane(self.artifact_kind, self.plane)
        _require_committed_status(self.status)
        _require_security_envelope(self.security_envelope)
        object.__setattr__(self, "artifact_ref", _freeze_object(self.artifact_ref))
        object.__setattr__(self, "manifest", _freeze_object(self.manifest))
        object.__setattr__(self, "security_envelope", _freeze_object(self.security_envelope))

    def payload(self) -> JsonObject:
        """Return the exact named-port passport persisted on a downstream node."""

        return {
            "artifactId": self.artifact_id,
            "artifactKind": self.artifact_kind.value,
            "plane": self.plane,
            "sourceNodeId": self.source_node_id,
            "sourcePortId": self.source_port_id,
            "targetPortId": self.target_port_id,
            "artifactRef": thaw_json_value(self.artifact_ref),
            "manifest": thaw_json_value(self.manifest),
            "contentFingerprint": self.content_fingerprint,
            "securityEnvelope": thaw_json_value(self.security_envelope),
            "status": self.status,
            "isServing": self.is_serving,
        }


@dataclass(frozen=True, slots=True)
class PipelineGraphV2ArtifactSpec:
    """Exact durable output artifact coordinates for one successful node."""

    port_id: str
    artifact_kind: PipelineArtifactKind
    plane: str
    artifact_ref: Mapping[str, object]
    manifest: Mapping[str, object]
    security_envelope: Mapping[str, object]
    content_fingerprint: str
    status: str
    is_serving: bool
    idempotency_key: str
    committed_at: str | None
    resource_ref: str | None = None
    pins: Mapping[str, object] = MappingProxyType({})
    row_count: int | None = None
    item_count: int | None = None
    byte_count: int | None = None

    def __post_init__(self) -> None:
        _require_text(self.port_id, "pipeline artifact port id")
        _require_text(self.content_fingerprint, "pipeline artifact content fingerprint")
        _require_text(self.idempotency_key, "pipeline artifact idempotency key")
        _require_plane(self.artifact_kind, self.plane)
        _require_committed_status(self.status)
        _require_text(self.committed_at or "", "pipeline artifact committed at")
        _require_security_envelope(self.security_envelope)
        _require_counts(self.row_count, self.item_count, self.byte_count)
        object.__setattr__(self, "artifact_ref", _freeze_object(self.artifact_ref))
        object.__setattr__(self, "manifest", _freeze_object(self.manifest))
        object.__setattr__(self, "security_envelope", _freeze_object(self.security_envelope))
        object.__setattr__(self, "pins", _freeze_object(self.pins))


@dataclass(frozen=True, slots=True)
class PipelineGraphV2AttemptContext:
    """Stable coordinates used to complete, fail, or replay one node attempt."""

    node_id: str
    descriptor_id: str
    spec_version: int
    executor_profile: str
    node_run_id: str
    attempt_id: str
    attempt_number: int
    input_artifacts: tuple[PipelineGraphV2InputArtifact, ...]

    def __post_init__(self) -> None:
        _require_text(self.node_id, "pipeline node id")
        _require_text(self.descriptor_id, "pipeline descriptor id")
        _require_text(self.executor_profile, "pipeline executor profile")
        _require_text(self.node_run_id, "pipeline node run id")
        _require_text(self.attempt_id, "pipeline node attempt id")
        if self.spec_version < 1 or self.attempt_number < 1:
            raise ValidationFailed("pipeline node spec and attempt versions must be positive")


def canonical_input_artifacts(
    artifacts: tuple[PipelineGraphV2InputArtifact, ...],
) -> tuple[PipelineGraphV2InputArtifact, ...]:
    """Return stable named-port order and reject duplicate edge evidence."""

    ordered = tuple(
        sorted(
            artifacts,
            key=lambda item: (
                item.target_port_id,
                item.source_node_id,
                item.source_port_id,
                item.artifact_id,
            ),
        )
    )
    coordinates = [
        (item.artifact_id, item.source_node_id, item.source_port_id, item.target_port_id) for item in ordered
    ]
    if len(coordinates) != len(set(coordinates)):
        raise ValidationFailed("pipeline input artifact edge evidence is duplicated")
    return ordered


def input_artifact_payloads(
    artifacts: tuple[PipelineGraphV2InputArtifact, ...],
) -> list[JsonObject]:
    return [artifact.payload() for artifact in canonical_input_artifacts(artifacts)]


def artifact_plane(artifact_kind: PipelineArtifactKind) -> str:
    return _ARTIFACT_PLANES[artifact_kind]


def require_node_coordinates(
    *,
    node_id: str,
    descriptor_id: str,
    spec_version: int,
    executor_profile: str | None = None,
) -> None:
    _require_text(node_id, "pipeline node id")
    _require_text(descriptor_id, "pipeline descriptor id")
    if spec_version < 1:
        raise ValidationFailed("pipeline node spec version must be positive")
    if executor_profile is not None:
        _require_text(executor_profile, "pipeline executor profile")


def require_output_security(
    attempt: PipelineGraphV2AttemptContext,
    artifact: PipelineGraphV2ArtifactSpec,
) -> None:
    if not attempt.input_artifacts:
        return
    sources = [_classification(item.security_envelope) for item in attempt.input_artifacts]
    output = _classification(artifact.security_envelope)
    if _security_is_not_weaker(output, sources):
        return
    raise ValidationFailed(
        "pipeline output security envelope would weaken an input artifact",
        details={"outputClassification": output, "sourceClassifications": sorted(set(sources))},
    )


def _require_plane(artifact_kind: PipelineArtifactKind, plane: str) -> None:
    expected = artifact_plane(artifact_kind)
    if plane != expected:
        raise ValidationFailed(
            "pipeline artifact plane does not match its kind",
            details={"artifactKind": artifact_kind.value, "expectedPlane": expected, "actualPlane": plane},
        )


def _require_committed_status(status: str) -> None:
    if status != "COMMITTED":
        raise ValidationFailed(
            "production Graph v2 execution evidence requires a committed artifact",
            details={"status": status},
        )


def _require_security_envelope(envelope: Mapping[str, object]) -> None:
    classification = envelope.get("classification")
    if not isinstance(classification, str) or not classification.strip():
        raise ValidationFailed("pipeline artifact security classification is required")


def _require_counts(*counts: int | None) -> None:
    if any(value is not None and value < 0 for value in counts):
        raise ValidationFailed("pipeline artifact counts cannot be negative")


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValidationFailed(f"{field} is required")


def _freeze_object(value: Mapping[str, object]) -> Mapping[str, object]:
    frozen = {str(key): _freeze_value(item) for key, item in value.items()}
    return MappingProxyType(frozen)


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_object(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _classification(envelope: Mapping[str, object]) -> str:
    return normalize_classification(envelope.get("classification"))


def _security_is_not_weaker(output: str, sources: list[str]) -> bool:
    if output in _CLASSIFICATION_RANK and all(source in _CLASSIFICATION_RANK for source in sources):
        return _CLASSIFICATION_RANK[output] >= max(_CLASSIFICATION_RANK[source] for source in sources)
    return all(source == output for source in sources)
