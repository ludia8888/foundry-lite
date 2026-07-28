"""Batch-only reusable trained-model runtime for Pipeline Graph v2."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelInferencePort,
    TrainedModelInvocation,
)
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_trained_model_contracts import (
    map_trained_model_inputs,
    merge_trained_model_outputs,
    trained_model_branch_config,
    validate_trained_model_config,
)
from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RuntimeArtifact,
    PipelineV2RuntimeNode,
    RuntimeInputs,
    artifact_input_refs,
    single_input_artifact,
)
from foundry_lite.application.services.pipeline_v2_runtime_security import inherited_runtime_security
from foundry_lite.domain.errors import ValidationFailed


class PipelineV2TrainedModelRuntime:
    """Resolve latest branch model and execute one tabular input/output batch."""

    def __init__(
        self,
        *,
        adapter: TrainedModelInferencePort,
        run_id: str,
    ) -> None:
        self._adapter = adapter
        self._run_id = run_id

    def execute(
        self,
        node: PipelineV2RuntimeNode,
        inputs: RuntimeInputs,
    ) -> PipelineV2RuntimeArtifact:
        source = single_input_artifact(node, inputs)
        model_ref = _required_text(node.config, "modelRef")
        branch, fallbacks = trained_model_branch_config(node.config)
        definition = self._adapter.resolve(model_ref, branch=branch, fallback_branches=fallbacks)
        validate_trained_model_config(node.config, definition)
        mapped = map_trained_model_inputs(source.items, node.config, definition)
        result = self._adapter.infer(TrainedModelInvocation(model_ref, branch, fallbacks, mapped))
        rows = merge_trained_model_outputs(source.items, result.rows, node.config, result.definition)
        return _artifact(node, source, inputs, rows, result.runtime_evidence, result.definition, self._run_id)


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
