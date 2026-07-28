"""Terminal state rules for production Pipeline Graph v2 runs."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from foundry_lite.application.services.pipeline_v2_runtime_contracts import (
    PipelineV2RunResult,
)
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_PARTIAL,
    PIPELINE_RUN_SUCCEEDED,
    StatusTransition,
)

JsonObject = dict[str, object]
_LEGACY_RUNTIME_CAPABILITIES = frozenset(
    {
        "tabular_v1_compiler",
        "semantic_index_candidate_runtime",
        "ontology_mapping_candidate_runtime",
    }
)


@dataclass(frozen=True, slots=True)
class PipelineGraphV2TerminalState:
    transition: StatusTransition
    status: str
    event: str
    output_dataset_ref: str | None
    output_version_id: str | None
    outputs: tuple[JsonObject, ...]
    timeline: tuple[JsonObject, ...]
    error: JsonObject | None


def is_graph_v2_execution_plan(plan: Mapping[str, object] | None) -> bool:
    """Identify a plan that must not be sent back through the legacy compiler."""

    if plan is None:
        return False
    nodes = plan.get("nodes")
    if not isinstance(nodes, list):
        return False
    return any(_is_graph_v2_node(node) for node in nodes)


def graph_v2_terminal_state(
    result: PipelineV2RunResult,
) -> PipelineGraphV2TerminalState:
    """Choose success, partial, or failed without losing immutable outputs."""

    outputs = tuple(dict(item) for item in result.outputs)
    dataset_ref, version_id = _serving_dataset_fields(outputs)
    transition, status, event = _terminal_identity(result, outputs)
    return PipelineGraphV2TerminalState(
        transition=transition,
        status=status,
        event=event,
        output_dataset_ref=dataset_ref,
        output_version_id=version_id,
        outputs=outputs,
        timeline=tuple(dict(item) for item in result.timeline),
        error=dict(result.error) if result.error is not None else None,
    )


def _is_graph_v2_node(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    capability = str(value.get("runtimeCapability") or "")
    return bool(capability and capability not in _LEGACY_RUNTIME_CAPABILITIES)


def _terminal_identity(
    result: PipelineV2RunResult,
    outputs: Sequence[Mapping[str, object]],
) -> tuple[StatusTransition, str, str]:
    if result.error is None:
        return PIPELINE_RUN_SUCCEEDED, "succeeded", "pipeline.run.succeeded"
    if any(output.get("status") in {"COMMITTED", "COMMIT_OUTCOME_UNKNOWN"} for output in outputs):
        return PIPELINE_RUN_PARTIAL, "partial", "pipeline.run.partial"
    return PIPELINE_RUN_FAILED, "failed", "pipeline.run.failed"


def _serving_dataset_fields(
    outputs: Sequence[Mapping[str, object]],
) -> tuple[str | None, str | None]:
    matches = [
        output
        for output in outputs
        if output.get("artifactKind") == "dataset_version" and output.get("isServing") is True
    ]
    if len(matches) != 1:
        return None, None
    ref = matches[0].get("ref")
    if not isinstance(ref, Mapping):
        return None, None
    return _optional_text(ref.get("datasetRef")), _optional_text(ref.get("versionId"))


def _optional_text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None
