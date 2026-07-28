"""Typed coordinates shared by Pipeline node execution evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass

from foundry_lite.application.services.transform_runs import TransformRunPlan

JsonObject = dict[str, object]


@dataclass(frozen=True, slots=True)
class PipelineNodeAttemptContext:
    """Exact Pipeline node attempt coordinates resolved before compute."""

    node_id: str
    node_run_id: str
    attempt_id: str
    attempt_number: int
    input_artifacts: tuple[JsonObject, ...]
    transform_plan: TransformRunPlan
