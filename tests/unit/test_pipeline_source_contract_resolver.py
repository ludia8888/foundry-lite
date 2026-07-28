from __future__ import annotations

from dataclasses import dataclass, field
from typing import cast

import pytest
from foundry_lite.application.ports.dataset_quality_repository import (
    DatasetQualityRepository,
    DatasetSchemaRow,
)
from foundry_lite.application.ports.dataset_repository import DatasetRepository, DatasetRow
from foundry_lite.application.ports.dataset_version_repository import (
    DatasetVersionRepository,
    DatasetVersionRow,
)
from foundry_lite.application.ports.media_repository import (
    MediaItemVersionRecord,
    MediaRepository,
    MediaSetRecord,
    MediaSetSelectionRecord,
)
from foundry_lite.application.ports.source_management_repository import (
    SourceManagementRepository,
    SourceSyncRow,
    SourceSyncRunRow,
)
from foundry_lite.application.services.pipeline_execution_contracts import (
    pipeline_execution_plan_payload,
)
from foundry_lite.application.services.pipeline_graph_contracts import PipelineV2Node
from foundry_lite.application.services.pipeline_plan_compiler import PipelinePlanCompiler
from foundry_lite.application.services.pipeline_source_contract_resolver import (
    PipelineSourceContractResolutionFailed,
    PipelineSourceContractResolver,
    _require_complete_media_selection,
    _schema_columns,
    _split_resource_ref,
    _text_list,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import PermissionDenied


@dataclass
class _DatasetRepo:
    rows: list[DatasetRow] = field(default_factory=list)

    def active_dataset_by_ref(
        self,
        *,
        transaction: object,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> DatasetRow | None:
        del transaction
        return next(
            (
                row
                for row in self.rows
                if row["tenant_id"] == tenant_id
                and row["namespace"] == namespace
                and row["name"] == name
                and row["status"] == "active"
            ),
            None,
        )


@dataclass
class _VersionRepo:
    rows: list[DatasetVersionRow] = field(default_factory=list)

    def latest_version_by_dataset_id(
        self,
        *,
        transaction: object,
        dataset_id: str,
    ) -> DatasetVersionRow | None:
        del transaction
        matching = [row for row in self.rows if row["dataset_id"] == dataset_id]
        return max(matching, key=lambda row: row["version_number"]) if matching else None

    def version_by_dataset_id_and_id(
        self,
        *,
        transaction: object,
        dataset_id: str,
        version_id: str,
    ) -> DatasetVersionRow | None:
        del transaction
        return next(
            (row for row in self.rows if row["dataset_id"] == dataset_id and row["id"] == version_id),
            None,
        )


@dataclass
class _QualityRepo:
    rows: list[DatasetSchemaRow] = field(default_factory=list)

    def schema_by_version(
        self,
        *,
        transaction: object,
        dataset_id: str,
        schema_version: int,
    ) -> DatasetSchemaRow | None:
        del transaction
        return next(
            (row for row in self.rows if row["dataset_id"] == dataset_id and row["version"] == schema_version),
            None,
        )


@dataclass
class _MediaRepo:
    media_sets: list[MediaSetRecord] = field(default_factory=list)
    selections: list[MediaSetSelectionRecord] = field(default_factory=list)

    def media_set_by_ref(
        self,
        *,
        transaction: object,
        tenant_id: str,
        namespace: str,
        name: str,
    ) -> MediaSetRecord | None:
        del transaction
        return next(
            (
                row
                for row in self.media_sets
                if row.tenant_id == tenant_id and row.namespace == namespace and row.name == name
            ),
            None,
        )

    def select_media_set_versions(
        self,
        *,
        transaction: object,
        tenant_id: str,
        media_set_id: str,
        media_item_version_ids: list[str] | None = None,
        logical_path_prefix: str | None = None,
        limit: int = 20,
    ) -> list[MediaSetSelectionRecord]:
        del transaction
        ids = set(media_item_version_ids or ())
        rows = [
            item
            for item in self.selections
            if item.version.tenant_id == tenant_id
            and item.media_set_id == media_set_id
            and (not ids or item.version.media_item_version_id in ids)
            and (logical_path_prefix is None or item.logical_path.startswith(logical_path_prefix))
            and item.version.status == "COMMITTED"
        ]
        return rows[:limit]


@dataclass
class _SourceRepo:
    sync: SourceSyncRow
    runs: list[SourceSyncRunRow]

    def sync_by_name(
        self,
        *,
        transaction: object,
        tenant_id: str,
        sync_name: str,
    ) -> SourceSyncRow | None:
        del transaction
        if self.sync["tenant_id"] == tenant_id and self.sync["sync_name"] == sync_name:
            return self.sync
        return None

    def list_sync_runs(self, *, tenant_id: str, sync_name: str) -> list[SourceSyncRunRow]:
        return [run for run in self.runs if run["tenant_id"] == tenant_id and run["sync_name"] == sync_name]


def test_dataset_source_uses_committed_schema_and_pins_security_access_and_version() -> None:
    resolver = _resolver()
    graph = _dataset_graph(datasetVersionId="dsv-1", schema=[{"name": "stale", "type": "string"}])
    ctx = _ctx()

    resolution = resolver.resolve(transaction=object(), graph=graph, ctx=ctx)
    plan = PipelinePlanCompiler().compile(
        graph,
        source_resolution=resolution,
        require_source_contracts=True,
    )
    payload = pipeline_execution_plan_payload(plan)
    source = cast(dict[str, object], payload["sourceContracts"][0])
    source_node = cast(dict[str, object], payload["nodes"][0])

    assert source["resourceRef"] == "raw.orders"
    assert source["schemaHash"] == "schema-hash-1"
    assert source["schemaContract"] == {
        "columns": [
            {"name": "order_id", "type": "string"},
            {"name": "amount", "type": "integer"},
        ]
    }
    assert source["versionPins"][0]["versionId"] == "dsv-1"
    assert source["securityEnvelope"]["classification"] == "CONFIDENTIAL"
    assert source["accessEvidence"] == {
        "tenantId": "tenant-a",
        "principalId": "engineer-a",
        "requestId": "req-source-contract",
        "permission": "dataset:read",
        "scopeEnforcement": "tenant_scoped_repository",
    }
    assert "schema" not in source_node["config"]
    assert resolution.warnings[0]["code"] == "source_schema_drift"
    assert plan.validation_warnings[-1]["graphSchemaIgnored"] is True


@pytest.mark.parametrize(
    ("fixture_state", "reason"),
    [
        ("missing", "source_not_found"),
        ("uncommitted", "source_has_no_committed_version"),
        ("staged", "source_version_not_committed"),
    ],
)
def test_dataset_source_fails_closed_when_missing_or_uncommitted(
    fixture_state: str,
    reason: str,
) -> None:
    datasets = [] if fixture_state == "missing" else [_dataset()]
    versions = [_version(status="STAGED")] if fixture_state == "staged" else []
    resolver = _resolver(datasets=datasets, versions=versions)

    with pytest.raises(PipelineSourceContractResolutionFailed) as captured:
        resolver.resolve(transaction=object(), graph=_dataset_graph(), ctx=_ctx())

    assert captured.value.details["reason"] == reason
    assert captured.value.details["nodeId"] == "source"


def test_media_source_pins_exact_committed_selection_and_preserves_security() -> None:
    media_set = _media_set()
    selection = _media_selection()
    resolver = _resolver(media_sets=[media_set], selections=[selection])
    graph = _media_graph(mediaItemVersionIds=["miv-1"])

    resolution = resolver.resolve(transaction=object(), graph=graph, ctx=_ctx())
    contract = resolution.contracts[0]
    pin = contract.version_pins[0]

    assert contract.resource_ref == "legal.contracts"
    assert contract.schema_contract["schemaType"] == "document"
    assert contract.security_envelope["classification"] == "CONFIDENTIAL"
    assert contract.security_envelope["policyVersions"] == ("policy-v3",)
    assert pin.version_id == "miv-1"
    assert pin.content_fingerprint == "sha256:pdf"
    assert pin.metadata["logicalPath"] == "/contracts/acme.pdf"
    assert pin.metadata["securityEnvelope"]["classification"] == "CONFIDENTIAL"


def test_media_source_rejects_uncommitted_selection_and_weaker_envelope() -> None:
    staged = _media_selection(status="STAGED")
    resolver = _resolver(media_sets=[_media_set()], selections=[staged])
    with pytest.raises(PipelineSourceContractResolutionFailed) as uncommitted:
        resolver.resolve(transaction=object(), graph=_media_graph(), ctx=_ctx())
    assert uncommitted.value.details["reason"] == "source_has_no_committed_version"

    weak = _media_selection(classification="public")
    resolver = _resolver(media_sets=[_media_set()], selections=[weak])
    with pytest.raises(PipelineSourceContractResolutionFailed) as weakened:
        resolver.resolve(transaction=object(), graph=_media_graph(), ctx=_ctx())
    assert weakened.value.details["reason"] == "source_security_weakened"


def test_media_source_requires_caller_clearance_before_pinning_secret_version() -> None:
    secret = _media_selection(classification="secret")
    resolver = _resolver(
        media_sets=[_media_set(classification="secret")],
        selections=[secret],
    )

    with pytest.raises(PermissionDenied, match="clearance"):
        resolver.resolve(transaction=object(), graph=_media_graph(), ctx=_ctx())


def test_media_source_rejects_dropped_media_set_retention_control() -> None:
    resolver = _resolver(
        media_sets=[_media_set(retention_policy_id="retention-7y")],
        selections=[_media_selection()],
    )

    with pytest.raises(PipelineSourceContractResolutionFailed) as captured:
        resolver.resolve(transaction=object(), graph=_media_graph(), ctx=_ctx())

    assert captured.value.details["reason"] == "source_security_weakened"
    assert captured.value.details["weakenedFields"] == ["retentionPolicyId"]


def test_source_resolution_is_tenant_scoped_even_when_logical_ref_matches() -> None:
    resolver = _resolver()
    other_tenant = RequestContext(
        tenant_id="tenant-b",
        actor_user_id="engineer-b",
        request_id="req-other",
        roles=("data_engineer",),
    )

    with pytest.raises(PipelineSourceContractResolutionFailed) as captured:
        resolver.resolve(transaction=object(), graph=_dataset_graph(), ctx=other_tenant)

    assert captured.value.details["reason"] == "source_not_found"
    assert captured.value.details["resourceRef"] == "raw.orders"


def test_stream_source_pins_committed_dataset_version_and_checkpoint() -> None:
    repository = _SourceRepo(_stream_sync(), [_stream_run()])
    resolver = _resolver(source_repository=cast(SourceManagementRepository, repository))

    resolution = resolver.resolve(transaction=object(), graph=_stream_graph(), ctx=_ctx())

    contract = resolution.contracts[0]
    pin = contract.version_pins[0]
    assert contract.artifact_kind.value == "stream_checkpoint"
    assert contract.resource_ref == "orders_live"
    assert pin.version_id == "dsv-1"
    assert pin.metadata["backingDatasetRef"] == "raw.orders"
    assert pin.metadata["sourceSyncRunId"] == "ssr-1"
    assert pin.metadata["checkpointEnd"] == {"partitionOffsets": {"0": 42}}


def test_geospatial_source_requires_actual_spatial_schema_and_pins_spec() -> None:
    resolver = _resolver(schemas=[_geo_schema()])

    resolution = resolver.resolve(transaction=object(), graph=_geospatial_graph(), ctx=_ctx())

    contract = resolution.contracts[0]
    assert contract.artifact_kind.value == "geospatial_series"
    assert contract.version_pins[0].metadata["geospatialSpec"] == {
        "encoding": "geojson",
        "geometryField": "geometry",
        "longitudeField": None,
        "latitudeField": None,
        "timeField": "event_time",
        "coordinateReferenceSystem": "EPSG:4326",
    }


@pytest.mark.parametrize(
    ("fixture_state", "reason"),
    [
        ("missing", "source_not_found"),
        ("batch", "source_is_not_checkpointed_stream"),
        ("no_run", "source_has_no_committed_checkpoint"),
    ],
)
def test_stream_source_fails_closed_without_a_committed_stream_checkpoint(
    fixture_state: str,
    reason: str,
) -> None:
    sync = _stream_sync()
    if fixture_state == "missing":
        sync = cast(SourceSyncRow, {**sync, "tenant_id": "tenant-b"})
    if fixture_state == "batch":
        sync = cast(SourceSyncRow, {**sync, "capability": "batch"})
    runs = [] if fixture_state == "no_run" else [_stream_run()]
    repository = _SourceRepo(sync, runs)
    resolver = _resolver(source_repository=cast(SourceManagementRepository, repository))

    with pytest.raises(PipelineSourceContractResolutionFailed) as captured:
        resolver.resolve(transaction=object(), graph=_stream_graph(), ctx=_ctx())

    assert captured.value.details["reason"] == reason


def test_dataset_source_rejects_missing_schema_and_cross_tenant_version() -> None:
    resolver = _resolver(schemas=[])
    with pytest.raises(PipelineSourceContractResolutionFailed) as missing_schema:
        resolver.resolve(transaction=object(), graph=_dataset_graph(), ctx=_ctx())
    assert missing_schema.value.details["reason"] == "source_schema_not_found"

    other_tenant_version = cast(DatasetVersionRow, {**_version(), "tenant_id": "tenant-b"})
    resolver = _resolver(versions=[other_tenant_version])
    with pytest.raises(PipelineSourceContractResolutionFailed) as uncommitted:
        resolver.resolve(transaction=object(), graph=_dataset_graph(), ctx=_ctx())
    assert uncommitted.value.details["reason"] == "source_version_not_committed"


def test_media_source_rejects_missing_set_and_incomplete_or_oversized_selection() -> None:
    resolver = _resolver()
    with pytest.raises(PipelineSourceContractResolutionFailed) as missing_set:
        resolver.resolve(transaction=object(), graph=_media_graph(), ctx=_ctx())
    assert missing_set.value.details["reason"] == "source_not_found"

    node = cast(dict[str, object], _media_graph()["nodes"][0])
    typed_node = cast(PipelineV2Node, node)
    selection = _media_selection()
    with pytest.raises(PipelineSourceContractResolutionFailed) as oversized:
        _require_complete_media_selection(typed_node, "legal.contracts", [], [selection] * 10_001)
    assert oversized.value.details["reason"] == "source_selection_too_large"

    with pytest.raises(PipelineSourceContractResolutionFailed) as missing_version:
        _require_complete_media_selection(typed_node, "legal.contracts", ["miv-2"], [selection])
    assert missing_version.value.details["missing_version_ids"] == ["miv-2"]


def test_source_contract_parsing_rejects_invalid_refs_and_media_selection_values() -> None:
    node = cast(PipelineV2Node, _media_graph()["nodes"][0])

    for resource_ref in ("invalid", ".name", "namespace."):
        with pytest.raises(PipelineSourceContractResolutionFailed) as invalid_ref:
            _split_resource_ref(resource_ref, node)
        assert invalid_ref.value.details["reason"] == "source_ref_invalid"

    for selection in ("miv-1", ["miv-1", None]):
        with pytest.raises(PipelineSourceContractResolutionFailed) as invalid_selection:
            _text_list(selection, node, "mediaItemVersionIds")
        assert invalid_selection.value.details["reason"] == "source_selection_invalid"

    assert _text_list(None, node, "mediaItemVersionIds") == []
    assert _schema_columns({"fields": ({"name": "id"}, "invalid")}) == [{"name": "id"}]
    assert _schema_columns({}) == []


def test_matching_graph_schema_does_not_emit_drift_warning() -> None:
    resolver = _resolver()
    graph = _dataset_graph(schema=_schema()["schema_json"]["columns"])

    resolution = resolver.resolve(transaction=object(), graph=graph, ctx=_ctx())

    assert resolution.warnings == ()


def _resolver(
    *,
    datasets: list[DatasetRow] | None = None,
    versions: list[DatasetVersionRow] | None = None,
    schemas: list[DatasetSchemaRow] | None = None,
    media_sets: list[MediaSetRecord] | None = None,
    selections: list[MediaSetSelectionRecord] | None = None,
    source_repository: SourceManagementRepository | None = None,
) -> PipelineSourceContractResolver:
    return PipelineSourceContractResolver(
        dataset_repository=cast(DatasetRepository, _DatasetRepo(datasets if datasets is not None else [_dataset()])),
        dataset_version_repository=cast(
            DatasetVersionRepository,
            _VersionRepo(versions if versions is not None else [_version()]),
        ),
        dataset_quality_repository=cast(
            DatasetQualityRepository,
            _QualityRepo(schemas if schemas is not None else [_schema()]),
        ),
        media_repository=cast(
            MediaRepository,
            _MediaRepo(media_sets or [], selections or []),
        ),
        source_management_repository=source_repository or cast(SourceManagementRepository, object()),
    )


def _dataset() -> DatasetRow:
    return {
        "id": "ds-orders",
        "tenant_id": "tenant-a",
        "namespace": "raw",
        "name": "orders",
        "description": None,
        "storage_kind": "parquet_manifest",
        "storage_uri": "memory://orders",
        "owner_team": "finance-data",
        "classification": "confidential",
        "status": "active",
        "primary_key": ["order_id"],
        "partition_spec": [],
        "sort_order": [],
        "target_file_size_bytes": None,
        "created_at": "2026-07-17T00:00:00Z",
        "updated_at": "2026-07-17T00:00:00Z",
    }


def _version(*, status: str = "COMMITTED") -> DatasetVersionRow:
    return {
        "id": "dsv-1",
        "tenant_id": "tenant-a",
        "dataset_id": "ds-orders",
        "branch": "main",
        "version_number": 1,
        "transaction_id": "dstx-1",
        "schema_version": 1,
        "manifest_uri": "memory://orders/v1/manifest.json",
        "row_count": 2,
        "byte_size": 128,
        "status": status,
        "superseded_by_version_id": None,
        "created_at": "2026-07-17T00:00:01Z",
    }


def _schema() -> DatasetSchemaRow:
    return {
        "id": "schema-1",
        "dataset_id": "ds-orders",
        "version": 1,
        "schema_json": {
            "columns": [
                {"name": "order_id", "type": "string"},
                {"name": "amount", "type": "integer"},
            ]
        },
        "schema_hash": "schema-hash-1",
        "created_at": "2026-07-17T00:00:01Z",
    }


def _geo_schema() -> DatasetSchemaRow:
    row = _schema()
    return {
        **row,
        "schema_json": {
            "columns": [
                {"name": "asset_id", "type": "string"},
                {"name": "geometry", "type": "object"},
                {"name": "event_time", "type": "string"},
            ]
        },
        "schema_hash": "schema-hash-geo-1",
    }


def _stream_sync() -> SourceSyncRow:
    return cast(
        SourceSyncRow,
        {
            "id": "ss-1",
            "tenant_id": "tenant-a",
            "sync_name": "orders_live",
            "source_name": "orders-kafka",
            "source_type": "kafka",
            "capability": "streaming",
            "target_dataset_ref": "raw.orders",
            "status": "active",
        },
    )


def _stream_run() -> SourceSyncRunRow:
    return cast(
        SourceSyncRunRow,
        {
            "id": "ssr-1",
            "tenant_id": "tenant-a",
            "sync_name": "orders_live",
            "dataset_version_id": "dsv-1",
            "status": "succeeded",
            "checkpoint_start": {"partitionOffsets": {"0": 40}},
            "checkpoint_end": {"partitionOffsets": {"0": 42}},
        },
    )


def _media_set(
    *,
    retention_policy_id: str | None = None,
    classification: str = "confidential",
) -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id="ms-1",
        tenant_id="tenant-a",
        namespace="legal",
        name="contracts",
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="default",
        classification=classification,
        retention_policy_id=retention_policy_id,
        created_at="2026-07-17T00:00:00Z",
        updated_at="2026-07-17T00:00:00Z",
    )


def _media_selection(
    *,
    status: str = "COMMITTED",
    classification: str = "confidential",
) -> MediaSetSelectionRecord:
    version = MediaItemVersionRecord(
        media_item_version_id="miv-1",
        tenant_id="tenant-a",
        media_item_id="mi-1",
        media_transaction_id="mtx-1",
        version_number=1,
        blob_key="media/contracts/acme.pdf",
        content_hash="sha256:pdf",
        byte_size=512,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={},
        security_envelope={
            "tenantId": "tenant-a",
            "classification": classification,
            "policyVersion": "policy-v3",
            "allowedPrincipalSetId": "legal-readers",
        },
        source_ref=None,
        status=status,
        created_at="2026-07-17T00:00:01Z",
        committed_at="2026-07-17T00:00:02Z" if status == "COMMITTED" else None,
    )
    return MediaSetSelectionRecord(
        media_set_id="ms-1",
        logical_path="/contracts/acme.pdf",
        version=version,
    )


def _ctx() -> RequestContext:
    return RequestContext(
        tenant_id="tenant-a",
        actor_user_id="engineer-a",
        request_id="req-source-contract",
        roles=("data_engineer",),
    )


def _dataset_graph(**source_config: object) -> dict[str, object]:
    return _graph(
        source={
            "id": "source",
            "kind": "source",
            "descriptorId": "source.dataset",
            "specVersion": 1,
            "config": {"datasetRef": "raw.orders", **source_config},
        },
        output={
            "id": "out",
            "kind": "output",
            "descriptorId": "output.dataset",
            "specVersion": 1,
            "config": {"outputDatasetRef": "analytics.orders"},
        },
        source_port="dataset",
        target_port="input",
    )


def _media_graph(**source_config: object) -> dict[str, object]:
    return _graph(
        source={
            "id": "source",
            "kind": "source",
            "descriptorId": "source.media_set",
            "specVersion": 1,
            "config": {"mediaSetRef": "legal.contracts", **source_config},
        },
        output={
            "id": "out",
            "kind": "output",
            "descriptorId": "output.media_set",
            "specVersion": 1,
            "config": {"mediaSetRef": "legal.processed"},
        },
        source_port="media",
        target_port="media",
    )


def _stream_graph() -> dict[str, object]:
    graph = _dataset_graph()
    graph["nodes"] = [
        {
            "id": "source",
            "kind": "source",
            "descriptorId": "source.stream",
            "specVersion": 1,
            "config": {"sourceRef": "orders_live"},
        },
        {
            "id": "bridge",
            "kind": "transform",
            "descriptorId": "bridge.stream_to_dataset",
            "specVersion": 1,
            "config": {},
        },
        graph["nodes"][1],
    ]
    graph["edges"] = [
        {
            "id": "source-bridge",
            "sourceNodeId": "source",
            "sourcePortId": "stream",
            "targetNodeId": "bridge",
            "targetPortId": "stream",
        },
        {
            "id": "bridge-out",
            "sourceNodeId": "bridge",
            "sourcePortId": "dataset",
            "targetNodeId": "out",
            "targetPortId": "input",
        },
    ]
    return graph


def _geospatial_graph() -> dict[str, object]:
    return _graph(
        source={
            "id": "source",
            "kind": "source",
            "descriptorId": "source.geospatial",
            "specVersion": 1,
            "config": {"resourceRef": "raw.orders", "geometryField": "geometry", "timeField": "event_time"},
        },
        output={
            "id": "out",
            "kind": "output",
            "descriptorId": "output.geospatial",
            "specVersion": 1,
            "config": {
                "resourceRef": "analytics.asset_locations",
                "geometryField": "geometry",
                "timeField": "event_time",
            },
        },
        source_port="series",
        target_port="input",
    )


def _graph(
    *,
    source: dict[str, object],
    output: dict[str, object],
    source_port: str,
    target_port: str,
) -> dict[str, object]:
    return {
        "schemaVersion": 2,
        "nodes": [source, output],
        "edges": [
            {
                "id": "source-out",
                "sourceNodeId": "source",
                "sourcePortId": source_port,
                "targetNodeId": "out",
                "targetPortId": target_port,
            }
        ],
        "layout": {},
        "outputContract": {"columns": []},
        "tests": [],
        "schedule": None,
    }
