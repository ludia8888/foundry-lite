"""Immutable trained-model definition snapshots embedded in deployment plans."""

from __future__ import annotations

from collections.abc import Mapping

from foundry_lite.application.ports.trained_model_inference import (
    TrainedModelDefinition,
    TrainedModelField,
)
from foundry_lite.domain.errors import ValidationFailed


def trained_model_definition_from_snapshot(snapshot: Mapping[str, object]) -> TrainedModelDefinition:
    return TrainedModelDefinition(
        model_ref=_text(snapshot, "modelRef"),
        display_name=_text(snapshot, "displayName"),
        branch=_text(snapshot, "branch"),
        version=_text(snapshot, "version"),
        revision=_text(snapshot, "revision"),
        executable_reference=_text(snapshot, "executableReference"),
        input_fields=_fields(snapshot, "inputFields"),
        output_fields=_fields(snapshot, "outputFields"),
        cpu_cores=_positive_float(snapshot, "cpuCores"),
        memory_mib=_positive_int(snapshot, "memoryMiB"),
        gpu_type=_text(snapshot, "gpuType"),
        startup_timeout_seconds=_positive_int(snapshot, "startupTimeoutSeconds"),
        is_preview_supported=_boolean(snapshot, "previewSupported"),
        execution_modes=_execution_modes(snapshot),
    )


def _fields(snapshot: Mapping[str, object], key: str) -> tuple[TrainedModelField, ...]:
    raw_fields = snapshot.get(key)
    if not isinstance(raw_fields, list | tuple) or not raw_fields:
        raise _invalid_snapshot(key)
    return tuple(_field(value, key, index) for index, value in enumerate(raw_fields))


def _field(value: object, key: str, index: int) -> TrainedModelField:
    if not isinstance(value, Mapping):
        raise _invalid_snapshot(key, index=index)
    required = value.get("required")
    if not isinstance(required, bool):
        raise _invalid_snapshot(key, index=index, childField="required")
    return TrainedModelField(
        name=_text(value, "name"),
        data_type=_text(value, "dataType"),
        is_required=required,
    )


def _execution_modes(snapshot: Mapping[str, object]) -> tuple[str, ...]:
    raw_modes = snapshot.get("executionModes")
    if not isinstance(raw_modes, list | tuple) or not raw_modes:
        raise _invalid_snapshot("executionModes")
    modes = tuple(str(mode).strip() for mode in raw_modes if isinstance(mode, str) and mode.strip())
    if len(modes) != len(raw_modes):
        raise _invalid_snapshot("executionModes")
    return modes


def _text(snapshot: Mapping[str, object], key: str) -> str:
    value = snapshot.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid_snapshot(key)
    return value.strip()


def _positive_int(snapshot: Mapping[str, object], key: str) -> int:
    value = snapshot.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise _invalid_snapshot(key)
    return value


def _positive_float(snapshot: Mapping[str, object], key: str) -> float:
    value = snapshot.get(key)
    if not isinstance(value, int | float) or isinstance(value, bool) or float(value) <= 0:
        raise _invalid_snapshot(key)
    return float(value)


def _boolean(snapshot: Mapping[str, object], key: str) -> bool:
    value = snapshot.get(key)
    if not isinstance(value, bool):
        raise _invalid_snapshot(key)
    return value


def _invalid_snapshot(field: str, **details: object) -> ValidationFailed:
    return ValidationFailed(
        "deployed trained-model definition snapshot is invalid",
        details={"field": field, **details},
    )
