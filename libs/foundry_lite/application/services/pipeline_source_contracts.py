"""Immutable committed-source contracts used by Pipeline Builder v2 plans."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from foundry_lite.application.services.pipeline_graph_contracts import (
    JsonObject,
    PipelineArtifactKind,
    PipelineGraphV2,
    PipelineV2Edge,
    PipelineV2Node,
)
from foundry_lite.domain.errors import ValidationFailed


class PipelineSourceContractResolutionFailed(ValidationFailed):
    """Typed fail-closed result for missing or unsafe committed sources."""

    code = "PIPELINE_SOURCE_CONTRACT_RESOLUTION_FAILED"


@dataclass(frozen=True, slots=True)
class PipelineSourceVersionPin:
    """One exact committed version selected for a source node."""

    version_id: str
    ordinal: int
    content_fingerprint: str
    metadata: Mapping[str, object] = MappingProxyType({})

    def __post_init__(self) -> None:
        _require_text(self.version_id, "source version id")
        _require_text(self.content_fingerprint, "source version fingerprint")
        object.__setattr__(self, "metadata", _freeze_object(self.metadata))


@dataclass(frozen=True, slots=True)
class PipelineSourceContract:
    """Authoritative committed source state pinned into an execution plan."""

    node_id: str
    descriptor_id: str
    artifact_kind: PipelineArtifactKind
    resource_ref: str
    source_id: str
    schema_contract: Mapping[str, object]
    schema_hash: str
    schema_version: int | None
    version_pins: tuple[PipelineSourceVersionPin, ...]
    security_envelope: Mapping[str, object]
    access_evidence: Mapping[str, object]

    def __post_init__(self) -> None:
        _require_text(self.node_id, "source node id")
        _require_text(self.descriptor_id, "source descriptor id")
        _require_text(self.resource_ref, "source resource ref")
        _require_text(self.source_id, "source id")
        _require_text(self.schema_hash, "source schema hash")
        if not self.version_pins:
            raise ValidationFailed("pipeline source contract requires a committed version")
        object.__setattr__(self, "schema_contract", _freeze_object(self.schema_contract))
        object.__setattr__(self, "security_envelope", _freeze_object(self.security_envelope))
        object.__setattr__(self, "access_evidence", _freeze_object(self.access_evidence))


@dataclass(frozen=True, slots=True)
class PipelineSourceResolution:
    """Resolved source contracts plus non-blocking graph drift evidence."""

    contracts: tuple[PipelineSourceContract, ...]
    warnings: tuple[Mapping[str, object], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "warnings",
            tuple(_freeze_object(warning) for warning in self.warnings),
        )


def pipeline_source_contract_payload(contract: PipelineSourceContract) -> JsonObject:
    """Return the durable JSON representation included in a deployment plan."""

    return {
        "nodeId": contract.node_id,
        "descriptorId": contract.descriptor_id,
        "artifactKind": contract.artifact_kind.value,
        "resourceRef": contract.resource_ref,
        "sourceId": contract.source_id,
        "schemaContract": thaw_source_value(contract.schema_contract),
        "schemaHash": contract.schema_hash,
        "schemaVersion": contract.schema_version,
        "versionPins": [_version_pin_payload(pin) for pin in contract.version_pins],
        "securityEnvelope": thaw_source_value(contract.security_envelope),
        "accessEvidence": thaw_source_value(contract.access_evidence),
    }


def pipeline_source_contracts_fingerprint(
    contracts: Sequence[PipelineSourceContract],
) -> str:
    payload = [pipeline_source_contract_payload(contract) for contract in contracts]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def source_contracts_by_node(
    contracts: Sequence[PipelineSourceContract],
) -> dict[str, PipelineSourceContract]:
    result: dict[str, PipelineSourceContract] = {}
    for contract in contracts:
        if contract.node_id in result:
            raise ValidationFailed(
                "pipeline source node has duplicate resolved contracts",
                details={"nodeId": contract.node_id},
            )
        result[contract.node_id] = contract
    return result


def apply_source_contract_schemas(
    graph: PipelineGraphV2,
    contracts: Sequence[PipelineSourceContract],
) -> PipelineGraphV2:
    """Overlay actual committed schemas only for validation, never persistence."""

    by_node = source_contracts_by_node(contracts)
    result: PipelineGraphV2 = {
        "schemaVersion": 2,
        "nodes": [_validation_node(node, by_node.get(node["id"])) for node in graph["nodes"]],
        "edges": [_validation_edge(edge) for edge in graph["edges"]],
        "layout": dict(graph["layout"]),
        "outputContract": dict(graph["outputContract"]),
        "tests": [dict(item) for item in graph["tests"]],
        "schedule": graph["schedule"],
    }
    if "metadata" in graph:
        result["metadata"] = dict(graph["metadata"])
    return result


def source_config_without_untrusted_schema(
    descriptor_id: str,
    config: Mapping[str, object],
) -> JsonObject:
    result = {str(key): value for key, value in config.items()}
    if descriptor_id in {"source.dataset", "source.media_set", "source.stream", "source.geospatial"}:
        result.pop("schema", None)
    return result


def thaw_source_value(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): thaw_source_value(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_source_value(item) for item in value]
    return value


def _validation_node(
    node: PipelineV2Node,
    contract: PipelineSourceContract | None,
) -> PipelineV2Node:
    config = {str(key): value for key, value in node["config"].items()}
    if contract is not None:
        config["schema"] = _schema_columns(contract.schema_contract)
    return {
        "id": node["id"],
        "kind": node["kind"],
        "descriptorId": node["descriptorId"],
        "specVersion": node["specVersion"],
        "config": config,
    }


def _validation_edge(edge: PipelineV2Edge) -> PipelineV2Edge:
    return {
        "id": edge["id"],
        "sourceNodeId": edge["sourceNodeId"],
        "sourcePortId": edge["sourcePortId"],
        "targetNodeId": edge["targetNodeId"],
        "targetPortId": edge["targetPortId"],
    }


def _schema_columns(schema_contract: Mapping[str, object]) -> list[JsonObject]:
    for field in ("columns", "fields"):
        value = schema_contract.get(field)
        if isinstance(value, (list, tuple)):
            return [_column_payload(item) for item in value if isinstance(item, Mapping)]
    return []


def _column_payload(value: Mapping[object, object]) -> JsonObject:
    return {str(key): item for key, item in value.items()}


def _version_pin_payload(pin: PipelineSourceVersionPin) -> JsonObject:
    return {
        "versionId": pin.version_id,
        "ordinal": pin.ordinal,
        "contentFingerprint": pin.content_fingerprint,
        "metadata": thaw_source_value(pin.metadata),
    }


def _freeze_object(value: Mapping[str, object]) -> Mapping[str, object]:
    return MappingProxyType({str(key): _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _freeze_object({str(key): item for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value


def _require_text(value: str, field: str) -> None:
    if not value.strip():
        raise ValidationFailed(f"{field} is required")
