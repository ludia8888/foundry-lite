"""Batch-only reusable trained-model runtime for Pipeline Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import TransactionManager
from foundry_lite.application.ports.media_repository import MediaRepository
from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelInferencePort,
    TrainedModelInvocation,
)
from foundry_lite.application.primitives import _json_hash, _now
from foundry_lite.application.services.pipeline_execution_contracts import ModelRef
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    map_trained_model_inputs,
    merge_trained_model_outputs,
    require_trained_model_definition_pin,
    require_trained_model_invocation_pin,
    trained_model_branch_config,
    validate_trained_model_config,
)
from foundry_lite.application.services.pipeline_trained_model_snapshot import (
    trained_model_definition_from_snapshot,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
    artifact_input_refs,
    single_input_artifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import inherited_runtime_security
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import InvariantViolation, ValidationFailed


class PipelineV2TrainedModelRuntime:
    """Resolve latest branch model and execute one tabular input/output batch."""

    def __init__(
        self,
        *,
        adapter: TrainedModelInferencePort,
        run_id: str,
        model_refs: Sequence[ModelRef],
        transaction_manager: TransactionManager | None = None,
        media_repository: MediaRepository | None = None,
        ctx: RequestContext | None = None,
    ) -> None:
        self._adapter = adapter
        self._run_id = run_id
        self._model_refs = tuple(model_refs)
        self._transaction_manager = transaction_manager
        self._media_repository = media_repository
        self._ctx = ctx

    def execute(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        model_ref = _required_text(node.config, "modelRef")
        pin = _required_model_pin(node, model_ref, self._model_refs)
        branch, fallbacks = trained_model_branch_config(node.config)
        definition = trained_model_definition_from_snapshot(pin.definition_snapshot)
        require_trained_model_definition_pin(
            model_ref=model_ref,
            expected_model_version=pin.model_version,
            expected_revision=pin.revision,
            expected_executable_reference=pin.executable_reference,
            definition=definition,
        )
        validate_trained_model_config(node.config, definition)
        mapped = map_trained_model_inputs(source.items, node.config, definition)
        invocation = TrainedModelInvocation(
            model_ref=model_ref,
            branch=branch,
            fallback_branches=fallbacks,
            rows=mapped,
            expected_model_version=pin.model_version,
            expected_revision=pin.revision,
            expected_executable_reference=pin.executable_reference,
            pinned_definition=definition,
        )
        result = self._adapter.infer(invocation)
        require_trained_model_invocation_pin(invocation, result.definition)
        trusted_media = self._trusted_media_coordinates(result.rows, definition)
        rows = merge_trained_model_outputs(
            source.items,
            result.rows,
            node.config,
            definition,
            trusted_media_coordinates=trusted_media,
        )
        return _artifact(node, source, inputs, rows, result.runtime_evidence, definition, self._run_id)

    def _trusted_media_coordinates(
        self,
        rows: Sequence[Mapping[str, object]],
        definition: TrainedModelDefinition,
    ) -> frozenset[tuple[str, str, str]] | None:
        version_ids = _model_media_version_ids(rows, definition)
        if version_ids is None:
            return None
        if self._transaction_manager is None or self._media_repository is None or self._ctx is None:
            raise InvariantViolation("trained-model media output requires tenant catalog validation")
        with self._transaction_manager.begin() as transaction:
            versions = self._media_repository.get_media_item_versions(
                transaction=transaction,
                tenant_id=self._ctx.tenant_id,
                ids=sorted(version_ids),
            )
        return frozenset(
            (
                version.media_item_version_id,
                version.sniffed_mime_type,
                version.content_hash.removeprefix("sha256:"),
            )
            for version in versions
            if version.status == "COMMITTED"
        )


def _required_model_pin(
    node: PipelineV2RuntimeNode,
    model_ref: str,
    model_refs: Sequence[ModelRef],
) -> ModelRef:
    config_fingerprint = _json_hash(dict(node.config))
    matches = {
        (
            pin.model_version,
            pin.provider,
            pin.revision,
            pin.parameters_fingerprint,
            pin.executable_reference,
            _json_hash(dict(pin.definition_snapshot)),
        ): pin
        for pin in model_refs
        if pin.model_id == model_ref and pin.parameters_fingerprint == config_fingerprint
    }
    if len(matches) != 1:
        raise InvariantViolation(
            "deployed trained-model node has no unique matching execution-plan pin",
            details={
                "nodeId": node.node_id,
                "modelRef": model_ref,
                "parametersFingerprint": config_fingerprint,
                "matchingPinCount": len(matches),
            },
        )
    pin = next(iter(matches.values()))
    if pin.provider != "trained_model":
        raise InvariantViolation(
            "deployed trained-model node pin has an unexpected provider",
            details={"nodeId": node.node_id, "modelRef": model_ref, "provider": pin.provider},
        )
    return pin


def _model_media_version_ids(
    rows: Sequence[Mapping[str, object]],
    definition: TrainedModelDefinition,
) -> set[str] | None:
    fields = [field.name for field in definition.output_fields if field.data_type == "mediaReference"]
    if not fields:
        return None
    return {
        str(reference.get("mediaItemVersionId"))
        for row in rows
        for field in fields
        if isinstance(reference := row.get(field), Mapping) and isinstance(reference.get("mediaItemVersionId"), str)
    }


def _artifact(
    node: PipelineV2RuntimeNode,
    source: PipelineV2RuntimeArtifact,
    inputs: RuntimeInputs,
    rows: Sequence[Mapping[str, object]],
    runtime_evidence: Mapping[str, object],
    definition: TrainedModelDefinition,
    run_id: str,
) -> PipelineV2RuntimeArtifact:
    model_pin = {
        "modelRef": definition.model_ref,
        "branch": definition.branch,
        "resolvedVersion": definition.version,
        "revision": definition.revision,
        "executableReference": definition.executable_reference,
    }
    return PipelineV2RuntimeArtifact(
        node_id=node.node_id,
        descriptor_id=node.descriptor_id,
        spec_version=node.spec_version,
        port_id="dataset",
        artifact_kind="dataset_version",
        plane="dataset",
        items=tuple(dict(row) for row in rows),
        artifact_ref={"type": "pipeline_intermediate", "runId": run_id, "nodeId": node.node_id},
        manifest={
            "inputArtifacts": artifact_input_refs(inputs),
            "rowCount": len(rows),
            "modelPin": model_pin,
            "runtimeEvidence": dict(runtime_evidence),
            "previewSupported": False,
            "executionMode": "batch",
        },
        security_envelope=inherited_runtime_security([source]),
        status="COMMITTED",
        is_serving=False,
        committed_at=_now(),
    )


def _required_text(config: Mapping[str, object], field: str) -> str:
    value = config.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationFailed("trained model config field is required", details={"field": field})
    return value.strip()
