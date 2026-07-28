"""Governed geospatial output runtime for Pipeline Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import PipelineExecutionLeaseFence
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.pipeline_geospatial_contracts import (
    geospatial_spec,
    validate_geospatial_rows,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    artifact_input_refs,
    single_input_artifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_rows import (
    PipelineV2DatasetIngest,
    PipelineV2DatasetRegistry,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import (
    inherited_runtime_security,
    require_dataset_classification,
    require_runtime_security_preserved,
)
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed

JsonObject = dict[str, object]
RuntimeInputs = Mapping[str, Sequence[PipelineV2RuntimeArtifact]]


class PipelineV2GeospatialRuntime:
    """Validate spatial semantics and commit one immutable serving series."""

    def __init__(
        self,
        *,
        dataset_registry: PipelineV2DatasetRegistry,
        dataset_ingest: PipelineV2DatasetIngest,
        ctx: RequestContext,
        run_id: str,
        execution_lease_guard: PipelineExecutionLeaseFence,
    ) -> None:
        self._dataset_registry = dataset_registry
        self._dataset_ingest = dataset_ingest
        self._ctx = ctx
        self._run_id = run_id
        self._execution_lease_guard = execution_lease_guard

    def output_geospatial(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        resource_ref = _required_text(node.config, "resourceRef", node.node_id)
        security = inherited_runtime_security([source])
        require_runtime_security_preserved([source], security, resource_ref=resource_ref)
        rows = tuple(dict(row) for row in source.items)
        spec = geospatial_spec(_schema_contract(rows), node.config)
        validate_geospatial_rows(rows, spec)
        self._ensure_dataset(resource_ref, str(security["classification"]))
        result = self._commit(node, resource_ref, rows, spec, security, inputs)
        return _geospatial_artifact(node, resource_ref, rows, spec, security, result)

    def _ensure_dataset(self, resource_ref: str, classification: str) -> None:
        existing = self._dataset_registry.find_dataset(resource_ref, ctx=self._ctx)
        if existing is None:
            self._dataset_registry.create_dataset(
                resource_ref,
                ctx=self._ctx,
                classification=classification,
            )
            return
        require_dataset_classification(
            existing["classification"],
            classification,
            dataset_ref=resource_ref,
        )

    def _commit(
        self,
        node: PipelineV2RuntimeNode,
        resource_ref: str,
        rows: Sequence[Mapping[str, object]],
        spec: Mapping[str, object],
        security: Mapping[str, object],
        inputs: RuntimeInputs,
    ) -> CommitResult:
        fieldnames = sorted({str(field) for row in rows for field in row})
        if not fieldnames:
            raise ValidationFailed("geospatial output rows have no fields")
        result = self._dataset_ingest.sync_rows_batch(
            resource_ref,
            rows,
            fieldnames=fieldnames,
            ctx=self._ctx,
            sync_name=f"pipeline:{self._run_id}:{node.node_id}",
            tx_type="SNAPSHOT",
            source_type="pipeline.graph_v2.geospatial",
            transaction_metadata={
                "pipelineRunId": self._run_id,
                "pipelineNodeId": node.node_id,
                "descriptorId": node.descriptor_id,
                "specVersion": node.spec_version,
                "inputArtifacts": artifact_input_refs(inputs),
                "geospatialSpec": dict(spec),
                "securityEnvelope": dict(security),
            },
            before_commit=self._execution_lease_guard.require_active,
        )
        if result is None:
            raise InvariantViolation("pipeline geospatial output did not create a version")
        return result


def _geospatial_artifact(
    node: PipelineV2RuntimeNode,
    resource_ref: str,
    rows: tuple[JsonObject, ...],
    spec: Mapping[str, object],
    security: Mapping[str, object],
    result: CommitResult,
) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="series",
        artifact_kind="geospatial_series",
        plane="geospatial",
        items=rows,
        artifact_ref={
            "resourceRef": resource_ref,
            "datasetId": result.dataset_id,
            "versionId": result.version_id,
            "transactionId": result.transaction_id,
        },
        manifest={
            "resourceRef": resource_ref,
            "versionNumber": result.version_number,
            "rowCount": result.row_count,
            "manifestUri": result.manifest_uri,
            "schemaHash": result.schema_hash,
            "geospatialSpec": dict(spec),
            "commitKind": "SERVING_ASSET",
        },
        security_envelope=dict(security),
        status="COMMITTED",
        is_serving=True,
        committed_at=_now(),
    )


def _schema_contract(rows: Sequence[Mapping[str, object]]) -> JsonObject:
    fields = sorted({str(field) for row in rows for field in row})
    return {"columns": [{"name": field} for field in fields]}


def _required_text(config: Mapping[str, object], field: str, node_id: str) -> str:
    value = config.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed(
        "pipeline runtime config field is required",
        details={"nodeId": node_id, "field": field},
    )
