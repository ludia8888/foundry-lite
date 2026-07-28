"""Typed runtime values for production Pipeline Builder Graph v2 execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from foundry_lite.application.primitives import _json_hash
from foundry_lite.domain.errors import InvariantViolation

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PipelineV2RuntimeNode:
    """One immutable node read from a deployed execution plan."""

    node_id: str
    kind: str
    descriptor_id: str
    spec_version: int
    runtime_capability: str
    config: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PipelineV2RuntimeEdge:
    """Named-port dependency retained by the production runtime."""

    edge_id: str
    source_node_id: str
    source_port_id: str
    target_node_id: str
    target_port_id: str


@dataclass(frozen=True, slots=True)
class PipelineV2SourceVersion:
    """Exact committed source version pinned at deployment."""

    version_id: str
    ordinal: int
    content_fingerprint: str
    metadata: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PipelineV2SourceContract:
    """Authoritative source contract embedded in the execution plan."""

    node_id: str
    descriptor_id: str
    artifact_kind: str
    resource_ref: str
    source_id: str
    schema_contract: Mapping[str, object]
    schema_hash: str
    schema_version: int | None
    version_pins: tuple[PipelineV2SourceVersion, ...]
    security_envelope: Mapping[str, object]
    access_evidence: Mapping[str, object]


@dataclass(frozen=True, slots=True)
class PipelineV2RuntimeArtifact:
    """Materialized node value plus the durable artifact coordinates to record."""

    node_id: str
    descriptor_id: str
    spec_version: int
    port_id: str
    artifact_kind: str
    plane: str
    items: tuple[JsonObject, ...]
    artifact_ref: Mapping[str, object]
    manifest: Mapping[str, object]
    security_envelope: Mapping[str, object]
    status: str
    is_serving: bool
    committed_at: str | None = None
    content_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        fingerprint = _json_hash(
            {
                "artifactRef": dict(self.artifact_ref),
                "manifest": dict(self.manifest),
                "items": list(self.items),
            }
        )
        object.__setattr__(self, "content_fingerprint", fingerprint)


RuntimeInputs = Mapping[str, Sequence[PipelineV2RuntimeArtifact]]


@dataclass(frozen=True, slots=True)
class PipelineV2RunResult:
    """Terminal Graph v2 runtime result returned to the Pipeline run service."""

    outputs: tuple[JsonObject, ...]
    timeline: tuple[JsonObject, ...]
    error: Mapping[str, object] | None = None
    failed_node_id: str | None = None
    skipped_node_ids: tuple[str, ...] = ()


def runtime_plan_nodes(plan: Mapping[str, object]) -> tuple[PipelineV2RuntimeNode, ...]:
    """Parse deployed plan nodes without accepting malformed coordinates."""

    rows = _mapping_rows(plan.get("nodes"), "nodes")
    return tuple(_runtime_node(row) for row in rows)


def runtime_plan_edges(plan: Mapping[str, object]) -> tuple[PipelineV2RuntimeEdge, ...]:
    """Parse deployed named-port edges."""

    rows = _mapping_rows(plan.get("edges"), "edges")
    return tuple(_runtime_edge(row) for row in rows)


def runtime_source_contracts(
    plan: Mapping[str, object],
) -> dict[str, PipelineV2SourceContract]:
    """Parse and index exact committed source contracts by node."""

    rows = _mapping_rows(plan.get("sourceContracts"), "sourceContracts")
    contracts = [_source_contract(row) for row in rows]
    by_node = {contract.node_id: contract for contract in contracts}
    if len(by_node) != len(contracts):
        raise InvariantViolation("pipeline execution plan has duplicate source contracts")
    return by_node


def input_artifacts_for_node(
    node_id: str,
    edges: Sequence[PipelineV2RuntimeEdge],
    artifacts: Mapping[str, PipelineV2RuntimeArtifact],
) -> dict[str, tuple[PipelineV2RuntimeArtifact, ...]]:
    """Resolve exact upstream artifacts grouped by target port."""

    grouped: dict[str, list[PipelineV2RuntimeArtifact]] = {}
    incoming = sorted(
        (edge for edge in edges if edge.target_node_id == node_id),
        key=lambda edge: (edge.target_port_id, edge.source_node_id, edge.edge_id),
    )
    for edge in incoming:
        artifact = artifacts.get(edge.source_node_id)
        if artifact is None:
            raise InvariantViolation(
                "pipeline upstream artifact is unavailable",
                details={"nodeId": node_id, "sourceNodeId": edge.source_node_id},
            )
        if artifact.port_id != edge.source_port_id:
            raise InvariantViolation(
                "pipeline runtime artifact port does not match the execution plan",
                details={
                    "nodeId": node_id,
                    "sourceNodeId": edge.source_node_id,
                    "expectedPortId": edge.source_port_id,
                    "actualPortId": artifact.port_id,
                },
            )
        grouped.setdefault(edge.target_port_id, []).append(artifact)
    return {port: tuple(values) for port, values in grouped.items()}


def single_input_artifact(
    node: PipelineV2RuntimeNode,
    inputs: Mapping[str, Sequence[PipelineV2RuntimeArtifact]],
) -> PipelineV2RuntimeArtifact:
    """Require one connected upstream artifact for a unary runtime node."""

    artifacts = [artifact for group in inputs.values() for artifact in group]
    if len(artifacts) != 1:
        raise InvariantViolation(
            "pipeline runtime node requires exactly one input artifact",
            details={"nodeId": node.node_id, "inputCount": len(artifacts)},
        )
    return artifacts[0]


def artifact_input_refs(
    inputs: Mapping[str, Sequence[PipelineV2RuntimeArtifact]],
) -> list[JsonObject]:
    """Project runtime inputs into stable lineage coordinates."""

    refs: list[JsonObject] = []
    for target_port_id in sorted(inputs):
        for artifact in inputs[target_port_id]:
            refs.append(
                {
                    "nodeId": artifact.node_id,
                    "portId": artifact.port_id,
                    "targetPortId": target_port_id,
                    "artifactKind": artifact.artifact_kind,
                    "artifactRef": dict(artifact.artifact_ref),
                    "contentFingerprint": artifact.content_fingerprint,
                }
            )
    return refs


def _runtime_node(row: Mapping[str, object]) -> PipelineV2RuntimeNode:
    config = row.get("config")
    if not isinstance(config, Mapping):
        raise InvariantViolation("pipeline execution plan node config is invalid")
    return PipelineV2RuntimeNode(
        node_id=_required_text(row, "nodeId"),
        kind=_required_text(row, "kind"),
        descriptor_id=_required_text(row, "descriptorId"),
        spec_version=_required_int(row, "specVersion"),
        runtime_capability=_required_text(row, "runtimeCapability"),
        config={str(key): value for key, value in config.items()},
    )


def _runtime_edge(row: Mapping[str, object]) -> PipelineV2RuntimeEdge:
    return PipelineV2RuntimeEdge(
        edge_id=_required_text(row, "edgeId"),
        source_node_id=_required_text(row, "sourceNodeId"),
        source_port_id=_required_text(row, "sourcePortId"),
        target_node_id=_required_text(row, "targetNodeId"),
        target_port_id=_required_text(row, "targetPortId"),
    )


def _source_contract(row: Mapping[str, object]) -> PipelineV2SourceContract:
    pins = tuple(_source_version(pin) for pin in _mapping_rows(row.get("versionPins"), "versionPins"))
    envelope = row.get("securityEnvelope")
    schema = row.get("schemaContract")
    access = row.get("accessEvidence")
    if not isinstance(envelope, Mapping):
        raise InvariantViolation("pipeline source security envelope is invalid")
    if not isinstance(schema, Mapping) or not isinstance(access, Mapping):
        raise InvariantViolation("pipeline source schema or access evidence is invalid")
    return PipelineV2SourceContract(
        node_id=_required_text(row, "nodeId"),
        descriptor_id=_required_text(row, "descriptorId"),
        artifact_kind=_required_text(row, "artifactKind"),
        resource_ref=_required_text(row, "resourceRef"),
        source_id=_required_text(row, "sourceId"),
        schema_contract={str(key): value for key, value in schema.items()},
        schema_hash=_required_text(row, "schemaHash"),
        schema_version=_optional_int(row.get("schemaVersion"), "schemaVersion"),
        version_pins=pins,
        security_envelope={str(key): value for key, value in envelope.items()},
        access_evidence={str(key): value for key, value in access.items()},
    )


def _source_version(row: Mapping[str, object]) -> PipelineV2SourceVersion:
    metadata = row.get("metadata")
    if not isinstance(metadata, Mapping):
        raise InvariantViolation("pipeline source version metadata is invalid")
    return PipelineV2SourceVersion(
        version_id=_required_text(row, "versionId"),
        ordinal=_required_int(row, "ordinal"),
        content_fingerprint=_required_text(row, "contentFingerprint"),
        metadata={str(key): value for key, value in metadata.items()},
    )


def _mapping_rows(value: object, field: str) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        raise InvariantViolation("pipeline execution plan collection is invalid", details={"field": field})
    rows = [row for row in value if isinstance(row, Mapping)]
    if len(rows) != len(value):
        raise InvariantViolation("pipeline execution plan row is invalid", details={"field": field})
    return rows


def _required_text(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise InvariantViolation("pipeline execution plan text coordinate is invalid", details={"field": field})
    return value.strip()


def _required_int(row: Mapping[str, object], field: str) -> int:
    value = row.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvariantViolation("pipeline execution plan integer coordinate is invalid", details={"field": field})
    return value


def _optional_int(value: object, field: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvariantViolation(
            "pipeline execution plan integer coordinate is invalid",
            details={"field": field},
        )
    return value
