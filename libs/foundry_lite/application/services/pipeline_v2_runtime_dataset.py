"""Exact committed Dataset source adapter for production Graph v2 execution."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadRequest,
    ExactDatasetVersionReadResult,
)
from foundry_lite.application.services.pipeline_geospatial_contracts import (
    validate_geospatial_rows,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

JsonObject = dict[str, object]


class ExactDatasetVersionReader(Protocol):
    """Read one deployment-pinned Dataset version without a latest lookup."""

    def read(
        self,
        ctx: RequestContext,
        *,
        request: ExactDatasetVersionReadRequest,
    ) -> ExactDatasetVersionReadResult: ...


class PipelineV2DatasetRuntime:
    """Convert an exact committed Dataset read into a Graph v2 runtime artifact."""

    def __init__(
        self,
        *,
        reader: ExactDatasetVersionReader,
        ctx: RequestContext,
    ) -> None:
        self._reader = reader
        self._ctx = ctx

    def source_dataset(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact:
        _validate_source_coordinates(node, contract)
        request = ExactDatasetVersionReadRequest.from_source_contract(contract)
        result = self._reader.read(self._ctx, request=request)
        _validate_reader_result(request, result)
        return _runtime_artifact(node, request, result)

    def source_stream(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact:
        _validate_structured_source(node, contract, "source.stream", "stream_checkpoint", "sourceRef")
        request, result = self._read_backing_dataset(contract)
        pin = contract.version_pins[0]
        return PipelineV2RuntimeArtifact(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            port_id="stream",
            artifact_kind="stream_checkpoint",
            plane="stream",
            items=tuple(_json_object(row) for row in result.rows),
            artifact_ref=_stream_artifact_ref(contract, request, pin.metadata),
            manifest=_structured_manifest(contract, request, result, pin.metadata),
            security_envelope=_json_object(result.security_envelope),
            status="COMMITTED",
            is_serving=True,
            committed_at=_manifest_created_at(result),
        )

    def source_geospatial(
        self,
        node: PipelineV2RuntimeNode,
        contract: PipelineV2SourceContract,
    ) -> PipelineV2RuntimeArtifact:
        _validate_structured_source(node, contract, "source.geospatial", "geospatial_series", "resourceRef")
        request, result = self._read_backing_dataset(contract)
        metadata = contract.version_pins[0].metadata
        spec = _required_mapping(metadata, "geospatialSpec")
        validate_geospatial_rows(result.rows, spec)
        return PipelineV2RuntimeArtifact(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            port_id="series",
            artifact_kind="geospatial_series",
            plane="geospatial",
            items=tuple(_json_object(row) for row in result.rows),
            artifact_ref={
                "resourceRef": contract.resource_ref,
                **result.artifact_ref(),
            },
            manifest=_structured_manifest(contract, request, result, metadata),
            security_envelope=_json_object(result.security_envelope),
            status="COMMITTED",
            is_serving=True,
            committed_at=_manifest_created_at(result),
        )

    def _read_backing_dataset(
        self,
        contract: PipelineV2SourceContract,
    ) -> tuple[ExactDatasetVersionReadRequest, ExactDatasetVersionReadResult]:
        request = ExactDatasetVersionReadRequest.from_source_contract(contract)
        result = self._reader.read(self._ctx, request=request)
        _validate_reader_result(request, result)
        return request, result


def _runtime_artifact(
    node: PipelineV2RuntimeNode,
    request: ExactDatasetVersionReadRequest,
    result: ExactDatasetVersionReadResult,
) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=tuple(_json_object(row) for row in result.rows),
        artifact_ref=_json_object(result.artifact_ref()),
        manifest=_runtime_manifest(request, result),
        security_envelope=_json_object(result.security_envelope),
        status="COMMITTED",
        is_serving=True,
        committed_at=_manifest_created_at(result),
    )


def _runtime_manifest(
    request: ExactDatasetVersionReadRequest,
    result: ExactDatasetVersionReadResult,
) -> JsonObject:
    return {
        **result.runtime_manifest(),
        "commitKind": "SOURCE",
        "contentFingerprint": result.content_fingerprint,
        "schemaContract": _json_object(result.schema_contract),
        "securityEnvelope": _json_object(result.security_envelope),
        "accessEvidence": _json_object(result.access_evidence),
        "sourceAccessEvidence": _json_object(request.access_evidence),
        "versionPins": [_version_pin(request)],
        "storageManifest": _json_object(result.manifest),
    }


def _version_pin(request: ExactDatasetVersionReadRequest) -> JsonObject:
    return {
        "versionId": request.version_id,
        "ordinal": request.version_number,
        "contentFingerprint": request.content_fingerprint,
        "metadata": _json_object(request.version_metadata),
    }


def _validate_source_coordinates(
    node: PipelineV2RuntimeNode,
    contract: PipelineV2SourceContract,
) -> None:
    dataset_ref = node.config.get("datasetRef")
    is_matching = (
        node.kind == "source"
        and node.descriptor_id == "source.dataset"
        and contract.node_id == node.node_id
        and contract.descriptor_id == node.descriptor_id
        and contract.artifact_kind == "dataset_version"
        and isinstance(dataset_ref, str)
        and dataset_ref.strip() == contract.resource_ref
    )
    if is_matching:
        return
    raise InvariantViolation(
        "pipeline Dataset source contract does not match the runtime node",
        details={
            "nodeId": node.node_id,
            "descriptorId": node.descriptor_id,
            "datasetRef": dataset_ref,
            "contractDatasetRef": contract.resource_ref,
        },
    )


def _validate_structured_source(
    node: PipelineV2RuntimeNode,
    contract: PipelineV2SourceContract,
    descriptor_id: str,
    artifact_kind: str,
    ref_field: str,
) -> None:
    resource_ref = node.config.get(ref_field)
    is_matching = (
        node.kind == "source"
        and node.descriptor_id == descriptor_id
        and contract.node_id == node.node_id
        and contract.descriptor_id == descriptor_id
        and contract.artifact_kind == artifact_kind
        and isinstance(resource_ref, str)
        and resource_ref.strip() == contract.resource_ref
    )
    if not is_matching:
        raise InvariantViolation(
            "pipeline structured source contract does not match the runtime node",
            details={"nodeId": node.node_id, "descriptorId": node.descriptor_id, "resourceRef": resource_ref},
        )


def _stream_artifact_ref(
    contract: PipelineV2SourceContract,
    request: ExactDatasetVersionReadRequest,
    metadata: Mapping[str, object],
) -> JsonObject:
    return {
        "sourceRef": contract.resource_ref,
        "sourceSyncId": contract.source_id,
        "sourceSyncRunId": metadata.get("sourceSyncRunId"),
        "datasetRef": request.dataset_ref,
        "versionId": request.version_id,
        "checkpoint": _json_value(metadata.get("checkpointEnd")),
    }


def _structured_manifest(
    contract: PipelineV2SourceContract,
    request: ExactDatasetVersionReadRequest,
    result: ExactDatasetVersionReadResult,
    metadata: Mapping[str, object],
) -> JsonObject:
    return {
        **_runtime_manifest(request, result),
        "resourceRef": contract.resource_ref,
        "sourceDescriptorId": contract.descriptor_id,
        "sourceMetadata": _json_object(metadata),
    }


def _required_mapping(value: Mapping[str, object], field: str) -> Mapping[str, object]:
    item = value.get(field)
    if isinstance(item, Mapping):
        return item
    raise InvariantViolation("pipeline structured source metadata is invalid", details={"field": field})


def _validate_reader_result(
    request: ExactDatasetVersionReadRequest,
    result: ExactDatasetVersionReadResult,
) -> None:
    coordinates = (
        (result.dataset_ref, request.dataset_ref),
        (result.dataset_id, request.dataset_id),
        (result.version_id, request.version_id),
        (result.version_number, request.version_number),
        (result.schema_hash, request.schema_hash),
        (result.schema_version, request.schema_version),
        (result.content_fingerprint, request.content_fingerprint),
    )
    if all(actual == expected for actual, expected in coordinates):
        return
    raise InvariantViolation(
        "exact Dataset reader returned a different source coordinate",
        details={
            "nodeId": request.node_id,
            "expectedVersionId": request.version_id,
            "actualVersionId": result.version_id,
        },
    )


def _manifest_created_at(result: ExactDatasetVersionReadResult) -> str:
    value = result.manifest.get("created_at")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise InvariantViolation(
        "exact Dataset source manifest has no commit timestamp",
        details={"versionId": result.version_id},
    )


def _json_object(value: Mapping[str, object]) -> JsonObject:
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> object:
    if isinstance(value, Mapping):
        return _json_object(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value
