"""Typed Dataset-output execution helpers for Pipeline Builder runs."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from foundry_lite.application.ports.pipeline_repository import PipelineRunRow
from foundry_lite.application.primitives import _now
from foundry_lite.application.services.pipeline_node_execution_evidence_types import (
    PipelineNodeAttemptContext,
)
from foundry_lite.application.services.pipeline_output_committers import (
    PipelineNodeCommitterRegistry,
)
from foundry_lite.application.services.runtime_evidence_boundary import RuntimeEvidenceBoundary
from foundry_lite.application.state_transitions import (
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_PARTIAL,
    StatusTransition,
)
from foundry_lite.domain.context import RequestContext

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PipelineRunExecution:
    """Ordered output results plus the exact compiled item that stopped execution."""

    outputs: tuple[JsonObject, ...]
    failed_item: JsonObject | None = None
    failed_attempt: PipelineNodeAttemptContext | None = None
    skipped_items: tuple[JsonObject, ...] = ()
    error: Exception | None = None


@dataclass(frozen=True, slots=True)
class PipelineUnsuccessfulCompletion:
    transition: StatusTransition
    status: str
    output_dataset_ref: str | None
    output_version_id: str | None
    outputs: tuple[JsonObject, ...]
    timeline: tuple[JsonObject, ...]
    error: JsonObject


def run_compiled_transforms(
    committers: PipelineNodeCommitterRegistry,
    compiled: Mapping[str, object],
    timeline: list[JsonObject],
) -> PipelineRunExecution:
    outputs: list[JsonObject] = []
    items = _compiled_items(compiled)
    for index, item in enumerate(items):
        outcome = committers.commit(item)
        if outcome.error is not None:
            return PipelineRunExecution(
                tuple(outputs),
                failed_item=item,
                failed_attempt=outcome.failed_attempt,
                skipped_items=tuple(items[index + 1 :]),
                error=outcome.error,
            )
        if outcome.timeline_event is None:
            raise RuntimeError("pipeline node succeeded without a timeline event")
        timeline.append(outcome.timeline_event)
        if outcome.output is not None:
            outputs.append(outcome.output)
    return PipelineRunExecution(tuple(outputs))


def outputs_with_failure(
    execution: PipelineRunExecution,
    error: Mapping[str, object],
) -> list[JsonObject]:
    outputs = [dict(output) for output in execution.outputs]
    failed_item = execution.failed_item
    pending = ([failed_item] if failed_item is not None else []) + list(execution.skipped_items)
    for item in pending:
        if item.get("isOutput") is True:
            outputs.append(_failed_output(item, _output_error(execution, item, error)))
    return outputs


def failure_timeline_events(
    execution: PipelineRunExecution,
    error: Mapping[str, object],
) -> list[JsonObject]:
    failed_item = execution.failed_item
    if failed_item is None:
        return []
    events = [_node_failed_event(failed_item, error)]
    events.extend(_node_skipped_event(item, failed_item) for item in execution.skipped_items)
    return events


def legacy_output_fields(
    compiled: Mapping[str, object],
    outputs: list[JsonObject],
) -> tuple[str | None, str | None]:
    selected_outputs = [item for item in _compiled_items(compiled) if item.get("isOutput") is True]
    if len(selected_outputs) != 1:
        return None, None
    selected = selected_outputs[0]
    if selected.get("artifactKind") != "dataset_version":
        return None, None
    dataset_ref = str(selected["datasetRef"])
    output = next((item for item in outputs if item.get("nodeId") == selected["nodeId"]), None)
    return dataset_ref, _committed_version_id(output)


def has_committed_output(outputs: list[JsonObject]) -> bool:
    return any(output.get("status") == "COMMITTED" for output in outputs)


def unsuccessful_run_completion(
    runtime_service: RuntimeEvidenceBoundary,
    ctx: RequestContext,
    row: PipelineRunRow,
    compiled: Mapping[str, object],
    timeline: list[JsonObject],
    execution: PipelineRunExecution,
) -> PipelineUnsuccessfulCompletion:
    if execution.error is None:
        raise RuntimeError("unsuccessful pipeline execution is missing its error")
    error = runtime_service._error_payload(execution.error, ctx, run_id=str(row["id"]))
    outputs = outputs_with_failure(execution, error)
    transition = PIPELINE_RUN_PARTIAL if has_committed_output(outputs) else PIPELINE_RUN_FAILED
    output_dataset_ref, output_version_id = legacy_output_fields(compiled, outputs)
    event: JsonObject = {
        "event": f"pipeline.run.{transition.to_status}",
        "at": _now(),
        "error": dict(error),
    }
    return PipelineUnsuccessfulCompletion(
        transition=transition,
        status=transition.to_status,
        output_dataset_ref=output_dataset_ref,
        output_version_id=output_version_id,
        outputs=tuple(outputs),
        timeline=tuple([*timeline, *failure_timeline_events(execution, error), event]),
        error=dict(error),
    )


def _compiled_items(compiled: Mapping[str, object]) -> list[JsonObject]:
    items = compiled.get("transforms")
    if not isinstance(items, list):
        return []
    return [dict(item) for item in items if isinstance(item, dict) and "nodeId" in item]


def _failed_output(item: Mapping[str, object], error: Mapping[str, object]) -> JsonObject:
    is_candidate = item.get("executionKind") == "governed_output_candidate"
    return {
        "nodeId": item["nodeId"],
        "artifactKind": item.get("artifactKind", "dataset_version"),
        "plane": item.get("plane", "dataset"),
        "status": "FAILED",
        "commitKind": "GOVERNED_CANDIDATE" if is_candidate else "SERVING_ASSET",
        "isServing": False,
        "ref": _failed_output_ref(item),
        "error": dict(error),
    }


def _output_error(
    execution: PipelineRunExecution,
    item: Mapping[str, object],
    error: Mapping[str, object],
) -> JsonObject:
    payload = dict(error)
    failed_item = execution.failed_item
    payload["executionDisposition"] = "FAILED" if item is failed_item else "SKIPPED"
    if failed_item is not None:
        payload["failedNodeId"] = failed_item["nodeId"]
    return payload


def _node_failed_event(item: Mapping[str, object], error: Mapping[str, object]) -> JsonObject:
    event: JsonObject = {
        "event": "pipeline.node.failed",
        "at": _now(),
        "nodeId": item["nodeId"],
        "operationRunType": _operation_run_type(item),
        "error": dict(error),
    }
    if "apiName" in item:
        event["transformApiName"] = item["apiName"]
    return event


def _node_skipped_event(item: Mapping[str, object], failed_item: Mapping[str, object]) -> JsonObject:
    event: JsonObject = {
        "event": "pipeline.node.skipped",
        "at": _now(),
        "nodeId": item["nodeId"],
        "operationRunType": _operation_run_type(item),
        "reason": "earlier_node_failed",
        "failedNodeId": failed_item["nodeId"],
    }
    if "apiName" in item:
        event["transformApiName"] = item["apiName"]
    return event


def _failed_output_ref(item: Mapping[str, object]) -> JsonObject:
    if item.get("executionKind") == "governed_output_candidate":
        return {
            "requestedResourceRef": item.get("resourceRef"),
            "servingState": "CANDIDATE_NOT_COMMITTED",
            "servingAssetCreated": False,
        }
    return {"datasetRef": item["datasetRef"]}


def _operation_run_type(item: Mapping[str, object]) -> str:
    if item.get("executionKind") == "governed_output_candidate":
        return "pipeline_output_candidate"
    return "transform"


def _committed_version_id(output: Mapping[str, object] | None) -> str | None:
    if output is None or output.get("status") != "COMMITTED":
        return None
    ref = output.get("ref")
    if not isinstance(ref, Mapping):
        return None
    version_id = ref.get("versionId")
    return str(version_id) if isinstance(version_id, str) else None
