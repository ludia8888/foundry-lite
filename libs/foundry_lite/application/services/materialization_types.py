from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, NoReturn

from foundry_lite.application.ports import DatasetRow
from foundry_lite.application.ports.materialization_repository import (
    MaterializationSourceRef,
    MaterializationTargetRef,
)
from foundry_lite.domain.errors import ValidationFailed

SUPPORTED_MATERIALIZATION_TYPES: Final = frozenset({"action_log", "object_snapshot"})


@dataclass(frozen=True)
class MaterializationRunPlan:
    api_name: str
    materialization_id: str
    materialization_type: str
    target_dataset: DatasetRow
    transaction_id: str
    run_id: str
    watermark: Mapping[str, object]
    rows: Sequence[Mapping[str, object]]
    fieldnames: list[str]


@dataclass(frozen=True)
class MaterializationSpec:
    materialization_type: str
    source: MaterializationSourceRef
    target: MaterializationTargetRef


MATERIALIZATION_SPECS: dict[str, MaterializationSpec] = {
    "action_log": MaterializationSpec(
        materialization_type="action_log",
        source={"type": "action_runs"},
        target={"dataset": "ops.action_log"},
    ),
    "order_current": MaterializationSpec(
        materialization_type="object_snapshot",
        source={"objectType": "Order"},
        target={"dataset": "ops.order_current"},
    ),
}


def supported_materialization_type(materialization_type: str) -> str:
    if materialization_type in SUPPORTED_MATERIALIZATION_TYPES:
        return materialization_type
    unsupported_materialization_type(materialization_type)


def unsupported_materialization_type(materialization_type: str) -> NoReturn:
    raise ValidationFailed(
        "unsupported materialization type",
        details={
            "materialization_type": materialization_type,
            "supported_types": sorted(SUPPORTED_MATERIALIZATION_TYPES),
        },
    )
