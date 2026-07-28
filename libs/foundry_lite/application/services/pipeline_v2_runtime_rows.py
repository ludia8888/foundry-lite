"""Tabular bridge, governed semantic, and Dataset output handlers for Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.ports.dataset_repository import DatasetRow
from foundry_lite.application.ports.language_model import GovernedSemanticModelPort
from foundry_lite.application.primitives import CommitResult, _now
from foundry_lite.application.services.pipeline_dataset_output_projection import (
    PipelineDatasetOutputProjection,
    pipeline_dataset_output_evidence,
    project_pipeline_dataset_output,
)
from foundry_lite.application.services.pipeline_media_reference import (
    required_source_media_reference,
)
from foundry_lite.application.services.pipeline_semantic_config import SemanticInterpretationSpec
from foundry_lite.application.services.pipeline_semantic_interpretation import (
    interpret_semantic_items,
    semantic_interpretation_spec,
)
from foundry_lite.application.services.pipeline_semantic_row_cache import (
    SemanticCacheContext,
    SemanticRowCacheSession,
    semantic_scoped_security_policy_fingerprint,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    artifact_input_refs,
    single_input_artifact,
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
_MAX_SEMANTIC_BUILD_CONCURRENCY = 4


class PipelineV2DatasetRegistry(Protocol):
    """Dataset registry boundary needed by Graph v2 output nodes."""

    def find_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
    ) -> DatasetRow | None: ...

    def create_dataset(
        self,
        dataset_ref: str,
        *,
        ctx: RequestContext | None = None,
        classification: str | None = None,
    ) -> DatasetRow: ...


class PipelineV2DatasetIngest(Protocol):
    """Serving Dataset commit boundary used by output.dataset."""

    def sync_rows_batch(
        self,
        dataset_ref: str,
        rows: Sequence[Mapping[str, object]],
        *,
        fieldnames: Sequence[str],
        ctx: RequestContext | None = None,
        sync_name: str | None = None,
        tx_type: str = "APPEND",
        source_type: str = "source.batch",
        transaction_metadata: Mapping[str, object] | None = None,
    ) -> CommitResult | None: ...


class PipelineV2RowRuntime:
    """Execute row-oriented Graph v2 nodes without bypassing serving commits."""

    def __init__(
        self,
        *,
        dataset_registry: PipelineV2DatasetRegistry,
        dataset_ingest: PipelineV2DatasetIngest,
        model_gateway: GovernedSemanticModelPort,
        semantic_cache: SemanticRowCacheSession,
        ctx: RequestContext,
        run_id: str,
        pipeline_id: str,
        deployment_id: str,
        resource_security_policy_fingerprint: str,
    ) -> None:
        self._dataset_registry = dataset_registry
        self._dataset_ingest = dataset_ingest
        self._model_gateway = model_gateway
        self._semantic_cache = semantic_cache
        self._ctx = ctx
        self._run_id = run_id
        self._pipeline_id = pipeline_id
        self._deployment_id = deployment_id
        self._resource_security_policy_fingerprint = resource_security_policy_fingerprint

    def content_units_to_rows(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        rows = tuple(_content_unit_row(item) for item in source.items)
        return _intermediate_rows_artifact(
            node,
            rows,
            inputs,
            [source],
            run_id=self._run_id,
        )

    def media_to_rows(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        rows = tuple(_media_reference_row(item) for item in source.items)
        return _intermediate_rows_artifact(
            node,
            rows,
            inputs,
            [source],
            run_id=self._run_id,
        )

    def stream_to_rows(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        rows = tuple(dict(item) for item in source.items)
        return _intermediate_rows_artifact(
            node,
            rows,
            inputs,
            [source],
            run_id=self._run_id,
        )

    def use_llm(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        spec, rows = self._interpret_semantic_rows(node, source)
        manifest = {
            "inputArtifacts": artifact_input_refs(inputs),
            "rowCount": len(rows),
            "modelAlias": spec.model_alias,
            "promptVersionId": spec.prompt_version_id,
            "outputSchema": dict(spec.output_schema),
            **_expected_model_pins(node.config),
        }
        return PipelineV2RuntimeArtifact(
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=node.spec_version,
            port_id="dataset",
            artifact_kind="dataset_version",
            plane="dataset",
            items=tuple(rows),
            artifact_ref=_intermediate_ref(self._run_id, node.node_id),
            manifest=manifest,
            security_envelope=inherited_runtime_security([source]),
            status="COMMITTED",
            is_serving=False,
            committed_at=_now(),
        )

    def _interpret_semantic_rows(
        self,
        node: PipelineV2RuntimeNode,
        source: PipelineV2RuntimeArtifact,
    ) -> tuple[SemanticInterpretationSpec, list[JsonObject]]:
        spec = semantic_interpretation_spec(node.config)
        rows = interpret_semantic_items(
            source.items,
            spec=spec,
            gateway=self._model_gateway,
            ctx=self._ctx,
            cache=self._semantic_cache,
            source_security_envelope=source.security_envelope,
            cache_context=self._cache_context(
                node,
                source.security_envelope,
                cache_generation=spec.cache_generation,
            ),
            max_concurrency=max(1, min(len(source.items), _MAX_SEMANTIC_BUILD_CONCURRENCY)),
        )
        return spec, rows

    def _cache_context(
        self,
        node: PipelineV2RuntimeNode,
        security_envelope: Mapping[str, object],
        *,
        cache_generation: int,
    ) -> SemanticCacheContext:
        return SemanticCacheContext(
            pipeline_id=self._pipeline_id,
            scope_kind="deployment",
            scope_id=self._deployment_id,
            node_id=node.node_id,
            descriptor_id=node.descriptor_id,
            spec_version=str(node.spec_version),
            cache_generation=cache_generation,
            resource_security_policy_fingerprint=semantic_scoped_security_policy_fingerprint(
                self._resource_security_policy_fingerprint,
                security_envelope,
            ),
        )

    def output_dataset(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        dataset_ref = _required_text(node.config, "outputDatasetRef", node.node_id)
        security = inherited_runtime_security([source])
        require_runtime_security_preserved([source], security, resource_ref=dataset_ref)
        projection = project_pipeline_dataset_output(
            source.items,
            node.config.get("outputContract"),
        )
        classification = str(security["classification"])
        self._ensure_dataset(dataset_ref, classification)
        result = self._commit_rows(node, dataset_ref, projection, security, inputs)
        return _committed_dataset_artifact(node, dataset_ref, projection, security, result)

    def _ensure_dataset(self, dataset_ref: str, classification: str) -> None:
        existing = self._dataset_registry.find_dataset(dataset_ref, ctx=self._ctx)
        if existing is None:
            self._dataset_registry.create_dataset(
                dataset_ref,
                ctx=self._ctx,
                classification=classification,
            )
            return
        require_dataset_classification(
            existing["classification"],
            classification,
            dataset_ref=dataset_ref,
        )

    def _commit_rows(
        self,
        node: PipelineV2RuntimeNode,
        dataset_ref: str,
        projection: PipelineDatasetOutputProjection,
        security: Mapping[str, object],
        inputs: RuntimeInputs,
    ) -> CommitResult:
        if not projection.serving_rows:
            raise ValidationFailed(
                "pipeline Dataset output requires at least one row",
                details={"nodeId": node.node_id, "datasetRef": dataset_ref},
            )
        evidence = pipeline_dataset_output_evidence(projection, security)
        result = self._dataset_ingest.sync_rows_batch(
            dataset_ref,
            projection.serving_rows,
            fieldnames=projection.fieldnames,
            ctx=self._ctx,
            sync_name=f"pipeline:{self._run_id}:{node.node_id}",
            tx_type="SNAPSHOT",
            source_type="pipeline.graph_v2",
            transaction_metadata={
                "pipelineRunId": self._run_id,
                "pipelineNodeId": node.node_id,
                "descriptorId": node.descriptor_id,
                "specVersion": node.spec_version,
                "inputArtifacts": artifact_input_refs(inputs),
                **evidence,
            },
        )
        if result is None:
            raise InvariantViolation("pipeline Dataset output did not create a version")
        return result


def _intermediate_rows_artifact(
    node: PipelineV2RuntimeNode,
    rows: tuple[JsonObject, ...],
    inputs: RuntimeInputs,
    sources: Sequence[PipelineV2RuntimeArtifact],
    *,
    run_id: str,
) -> PipelineV2RuntimeArtifact:
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=rows,
        artifact_ref=_intermediate_ref(run_id, node.node_id),
        manifest={
            "inputArtifacts": artifact_input_refs(inputs),
            "rowCount": len(rows),
            "schema": _infer_schema(rows),
            "commitKind": "INTERMEDIATE",
        },
        security_envelope=inherited_runtime_security(sources),
        status="COMMITTED",
        is_serving=False,
        committed_at=_now(),
    )


def _committed_dataset_artifact(
    node: PipelineV2RuntimeNode,
    dataset_ref: str,
    projection: PipelineDatasetOutputProjection,
    security: Mapping[str, object],
    result: CommitResult,
) -> PipelineV2RuntimeArtifact:
    evidence = pipeline_dataset_output_evidence(projection, security)
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=projection.serving_rows,
        artifact_ref={
            "datasetRef": dataset_ref,
            "datasetId": result.dataset_id,
            "versionId": result.version_id,
            "transactionId": result.transaction_id,
        },
        manifest={
            "datasetRef": dataset_ref,
            "versionNumber": result.version_number,
            "rowCount": result.row_count,
            "manifestUri": result.manifest_uri,
            "schemaHash": result.schema_hash,
            "commitKind": "SERVING_ASSET",
            **evidence,
        },
        security_envelope=dict(security),
        status="COMMITTED",
        is_serving=True,
        committed_at=_now(),
    )


def _content_unit_row(item: Mapping[str, object]) -> JsonObject:
    row = dict(item)
    row.setdefault("contentUnitId", item.get("contentUnitId"))
    row.setdefault("text", str(item.get("text") or ""))
    return row


def _media_reference_row(item: Mapping[str, object]) -> JsonObject:
    reference = required_source_media_reference(item)
    envelope = item.get("securityEnvelope")
    return {
        "mediaReference": reference,
        "mediaItemVersionId": reference["mediaItemVersionId"],
        "securityEnvelope": dict(envelope) if isinstance(envelope, Mapping) else {},
    }


def _fieldnames(rows: Sequence[Mapping[str, object]]) -> list[str]:
    fields = sorted({str(field) for row in rows for field in row})
    if not fields:
        raise ValidationFailed("pipeline Dataset output rows have no fields")
    return fields


def _infer_schema(rows: Sequence[Mapping[str, object]]) -> list[JsonObject]:
    fields = _fieldnames(rows) if rows else []
    return [{"name": field, "type": _value_type(_first_value(rows, field))} for field in fields]


def _first_value(rows: Sequence[Mapping[str, object]], field: str) -> object:
    return next((row.get(field) for row in rows if row.get(field) is not None), None)


def _value_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, (list, tuple)):
        return "array"
    return "string"


def _intermediate_ref(run_id: str, node_id: str) -> JsonObject:
    return {
        "runtimeArtifactId": f"{run_id}:{node_id}",
        "servingState": "INTERMEDIATE_NOT_SERVING",
    }


def _required_text(
    config: Mapping[str, object],
    field: str,
    node_id: str,
) -> str:
    value = config.get(field)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise ValidationFailed(
        "pipeline runtime config field is required",
        details={"nodeId": node_id, "field": field},
    )


def _expected_model_pins(config: Mapping[str, object]) -> JsonObject:
    fields = ("expectedModelId", "expectedModelRevision")
    return {
        field: config[field] for field in fields if isinstance(config.get(field), str) and str(config[field]).strip()
    }
