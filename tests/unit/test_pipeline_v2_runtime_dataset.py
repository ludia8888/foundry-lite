from __future__ import annotations

from dataclasses import dataclass, replace
from types import MappingProxyType

import pytest
from foundry_lite.application.services.pipeline_dataset_version_read_contracts import (
    ExactDatasetVersionReadRequest,
    ExactDatasetVersionReadResult,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeNode,
    PipelineV2SourceContract,
    PipelineV2SourceVersion,
)
from foundry_lite.application.services.pipeline_v2_runtime_dataset import (
    PipelineV2DatasetRuntime,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation

_CTX = RequestContext(
    tenant_id="tenant-demo",
    actor_user_id="user-data-engineer",
    request_id="req-runtime",
    roles=("data_engineer",),
)


@dataclass
class _ExactReader:
    result: ExactDatasetVersionReadResult
    requests: list[ExactDatasetVersionReadRequest]

    def read(
        self,
        ctx: RequestContext,
        *,
        request: ExactDatasetVersionReadRequest,
    ) -> ExactDatasetVersionReadResult:
        assert ctx == _CTX
        self.requests.append(request)
        return self.result


def test_dataset_runtime_preserves_exact_rows_and_source_passport() -> None:
    reader = _ExactReader(_result(), [])
    runtime = PipelineV2DatasetRuntime(reader=reader, ctx=_CTX)

    artifact = runtime.source_dataset(_node(), _contract())

    assert [row["sequence"] for row in artifact.items] == [1, 2]
    assert artifact.artifact_ref == {
        "datasetRef": "raw.orders",
        "datasetId": "ds-orders",
        "versionId": "dsv-orders-v7",
        "versionNumber": 7,
        "transactionId": "dstx-orders-v7",
    }
    assert artifact.port_id == "dataset"
    assert artifact.artifact_kind == "dataset_version"
    assert artifact.plane == "dataset"
    assert artifact.status == "COMMITTED"
    assert artifact.is_serving is True
    assert artifact.committed_at == "2026-07-17T03:00:00Z"
    assert artifact.manifest["schemaContract"] == {"columns": [{"name": "sequence", "type": "integer"}]}
    assert artifact.manifest["schemaHash"] == "schema-orders-v3"
    assert artifact.manifest["schemaVersion"] == 3
    assert artifact.manifest["securityEnvelope"] == {
        "tenantId": "tenant-demo",
        "classification": "CONFIDENTIAL",
        "inheritance": "source",
    }
    assert artifact.manifest["accessEvidence"] == {
        "tenantId": "tenant-demo",
        "principalId": "user-data-engineer",
        "requestId": "req-runtime",
        "permission": "dataset:read",
        "scopeEnforcement": "tenant_scoped_exact_version",
    }
    assert artifact.manifest["sourceAccessEvidence"] == {
        "tenantId": "tenant-demo",
        "principalId": "deployment-user",
        "requestId": "req-deploy",
        "permission": "dataset:read",
        "scopeEnforcement": "tenant_scoped_repository",
    }
    assert artifact.manifest["versionPins"] == [
        {
            "versionId": "dsv-orders-v7",
            "ordinal": 7,
            "contentFingerprint": "fingerprint-orders-v7",
            "metadata": {
                "versionNumber": 7,
                "branch": "main",
                "manifestUri": "file:///orders/v7/manifest.json",
            },
        }
    ]
    assert artifact.manifest["storageManifest"] == {
        "version_id": "dsv-orders-v7",
        "dataset": "raw.orders",
        "branch": "main",
        "schema_hash": "schema-orders-v3",
        "files": [
            {
                "uri": "file:///orders/v7/part-00000.parquet",
                "format": "parquet",
                "row_count": 2,
                "byte_size": 128,
                "content_hash": "a" * 64,
            }
        ],
        "created_at": "2026-07-17T03:00:00Z",
        "storage_profile": "local",
    }
    assert len(reader.requests) == 1
    assert reader.requests[0].version_id == "dsv-orders-v7"
    assert reader.requests[0].schema_hash == "schema-orders-v3"


def test_dataset_runtime_rejects_node_contract_mismatch_before_reader_call() -> None:
    reader = _ExactReader(_result(), [])
    runtime = PipelineV2DatasetRuntime(reader=reader, ctx=_CTX)
    node = replace(_node(), config={"datasetRef": "raw.latest_orders"})

    with pytest.raises(InvariantViolation) as raised:
        runtime.source_dataset(node, _contract())

    assert raised.value.details["contractDatasetRef"] == "raw.orders"
    assert reader.requests == []


def test_dataset_runtime_rejects_reader_coordinate_drift() -> None:
    reader = _ExactReader(replace(_result(), version_id="dsv-orders-latest"), [])
    runtime = PipelineV2DatasetRuntime(reader=reader, ctx=_CTX)

    with pytest.raises(InvariantViolation) as raised:
        runtime.source_dataset(_node(), _contract())

    assert raised.value.details == {
        "nodeId": "source-orders",
        "expectedVersionId": "dsv-orders-v7",
        "actualVersionId": "dsv-orders-latest",
    }


def test_stream_runtime_reads_backing_version_and_emits_checkpoint_passport() -> None:
    reader = _ExactReader(_result(), [])
    runtime = PipelineV2DatasetRuntime(reader=reader, ctx=_CTX)

    artifact = runtime.source_stream(_stream_node(), _stream_contract())

    assert artifact.artifact_kind == "stream_checkpoint"
    assert artifact.port_id == "stream"
    assert artifact.plane == "stream"
    assert artifact.artifact_ref["sourceRef"] == "orders_live"
    assert artifact.artifact_ref["sourceSyncRunId"] == "ssr-7"
    assert artifact.artifact_ref["checkpoint"] == {"partitionOffsets": {"0": 42}}
    assert reader.requests[0].dataset_ref == "raw.orders"
    assert reader.requests[0].version_id == "dsv-orders-v7"


def test_geospatial_runtime_validates_geojson_rows_and_preserves_version_pin() -> None:
    result = replace(
        _result(),
        rows=({"asset_id": "A-1", "geometry": {"type": "Point", "coordinates": [127.0, 37.5]}},),
    )
    reader = _ExactReader(result, [])
    runtime = PipelineV2DatasetRuntime(reader=reader, ctx=_CTX)

    artifact = runtime.source_geospatial(_geo_node(), _geo_contract())

    assert artifact.artifact_kind == "geospatial_series"
    assert artifact.port_id == "series"
    assert artifact.plane == "geospatial"
    assert artifact.artifact_ref["resourceRef"] == "raw.orders"
    assert artifact.manifest["sourceMetadata"]["geospatialSpec"]["coordinateReferenceSystem"] == "EPSG:4326"


def _node() -> PipelineV2RuntimeNode:
    return PipelineV2RuntimeNode(
        node_id="source-orders",
        kind="source",
        descriptor_id="source.dataset",
        spec_version=1,
        runtime_capability="graph_v2_executable",
        config={"datasetRef": "raw.orders"},
    )


def _contract() -> PipelineV2SourceContract:
    return PipelineV2SourceContract(
        node_id="source-orders",
        descriptor_id="source.dataset",
        artifact_kind="dataset_version",
        resource_ref="raw.orders",
        source_id="ds-orders",
        schema_contract={"columns": [{"name": "sequence", "type": "integer"}]},
        schema_hash="schema-orders-v3",
        schema_version=3,
        version_pins=(
            PipelineV2SourceVersion(
                version_id="dsv-orders-v7",
                ordinal=7,
                content_fingerprint="fingerprint-orders-v7",
                metadata={
                    "versionNumber": 7,
                    "branch": "main",
                    "manifestUri": "file:///orders/v7/manifest.json",
                },
            ),
        ),
        security_envelope={
            "tenantId": "tenant-demo",
            "classification": "CONFIDENTIAL",
            "inheritance": "source",
        },
        access_evidence={
            "tenantId": "tenant-demo",
            "principalId": "deployment-user",
            "requestId": "req-deploy",
            "permission": "dataset:read",
            "scopeEnforcement": "tenant_scoped_repository",
        },
    )


def _stream_node() -> PipelineV2RuntimeNode:
    return replace(
        _node(),
        descriptor_id="source.stream",
        runtime_capability="streaming_pipeline_runtime",
        config={"sourceRef": "orders_live"},
    )


def _stream_contract() -> PipelineV2SourceContract:
    base = _contract()
    pin = replace(
        base.version_pins[0],
        metadata={
            **base.version_pins[0].metadata,
            "backingDatasetRef": "raw.orders",
            "backingDatasetId": "ds-orders",
            "sourceSyncRunId": "ssr-7",
            "checkpointEnd": {"partitionOffsets": {"0": 42}},
        },
    )
    return replace(
        base,
        descriptor_id="source.stream",
        artifact_kind="stream_checkpoint",
        resource_ref="orders_live",
        source_id="ss-7",
        version_pins=(pin,),
    )


def _geo_node() -> PipelineV2RuntimeNode:
    return replace(
        _node(),
        descriptor_id="source.geospatial",
        runtime_capability="geospatial_pipeline_runtime",
        config={"resourceRef": "raw.orders", "geometryField": "geometry"},
    )


def _geo_contract() -> PipelineV2SourceContract:
    base = _contract()
    pin = replace(
        base.version_pins[0],
        metadata={
            **base.version_pins[0].metadata,
            "backingDatasetRef": "raw.orders",
            "backingDatasetId": "ds-orders",
            "geospatialSpec": {
                "encoding": "geojson",
                "geometryField": "geometry",
                "longitudeField": None,
                "latitudeField": None,
                "timeField": None,
                "coordinateReferenceSystem": "EPSG:4326",
            },
        },
    )
    return replace(
        base,
        descriptor_id="source.geospatial",
        artifact_kind="geospatial_series",
        version_pins=(pin,),
    )


def _result() -> ExactDatasetVersionReadResult:
    schema = MappingProxyType({"columns": (MappingProxyType({"name": "sequence", "type": "integer"}),)})
    manifest = MappingProxyType(
        {
            "version_id": "dsv-orders-v7",
            "dataset": "raw.orders",
            "branch": "main",
            "schema_hash": "schema-orders-v3",
            "files": (
                MappingProxyType(
                    {
                        "uri": "file:///orders/v7/part-00000.parquet",
                        "format": "parquet",
                        "row_count": 2,
                        "byte_size": 128,
                        "content_hash": "a" * 64,
                    }
                ),
            ),
            "created_at": "2026-07-17T03:00:00Z",
            "storage_profile": "local",
        }
    )
    return ExactDatasetVersionReadResult(
        dataset_ref="raw.orders",
        dataset_id="ds-orders",
        version_id="dsv-orders-v7",
        version_number=7,
        transaction_id="dstx-orders-v7",
        branch="main",
        manifest_uri="file:///orders/v7/manifest.json",
        rows=(
            {"sequence": 1, "metadata": MappingProxyType({"source": "part-0"})},
            {"sequence": 2, "metadata": MappingProxyType({"source": "part-0"})},
        ),
        schema_contract=schema,
        schema_hash="schema-orders-v3",
        schema_version=3,
        manifest=manifest,
        security_envelope=MappingProxyType(
            {
                "tenantId": "tenant-demo",
                "classification": "CONFIDENTIAL",
                "inheritance": "source",
            }
        ),
        access_evidence=MappingProxyType(
            {
                "tenantId": "tenant-demo",
                "principalId": "user-data-engineer",
                "requestId": "req-runtime",
                "permission": "dataset:read",
                "scopeEnforcement": "tenant_scoped_exact_version",
            }
        ),
        content_fingerprint="fingerprint-orders-v7",
    )
