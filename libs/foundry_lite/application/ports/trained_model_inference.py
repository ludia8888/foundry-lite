"""Typed boundary for reusable trained-model inference in Pipeline Builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TrainedModelField:
    name: str
    data_type: str
    is_required: bool = True


@dataclass(frozen=True, slots=True)
class TrainedModelDefinition:
    model_ref: str
    display_name: str
    branch: str
    version: str
    revision: str
    input_fields: tuple[TrainedModelField, ...]
    output_fields: tuple[TrainedModelField, ...]
    cpu_cores: float = 1.0
    memory_mib: int = 8192
    gpu_type: str = "none"
    startup_timeout_seconds: int = 600
    is_preview_supported: bool = False
    execution_modes: tuple[str, ...] = ("batch",)


@dataclass(frozen=True, slots=True)
class TrainedModelInvocation:
    model_ref: str
    branch: str
    fallback_branches: tuple[str, ...]
    rows: tuple[Mapping[str, object], ...]
    expected_model_version: str | None = None
    expected_revision: str | None = None


@dataclass(frozen=True, slots=True)
class TrainedModelInferenceResult:
    definition: TrainedModelDefinition
    rows: tuple[Mapping[str, object], ...]
    runtime_evidence: Mapping[str, object]


class TrainedModelInferencePort(Protocol):
    """Resolve a reusable model and execute its single-table batch API."""

    def list_models(self) -> Sequence[TrainedModelDefinition]: ...

    def resolve(
        self,
        model_ref: str,
        *,
        branch: str,
        fallback_branches: Sequence[str] = (),
    ) -> TrainedModelDefinition: ...

    def infer(self, invocation: TrainedModelInvocation) -> TrainedModelInferenceResult: ...
