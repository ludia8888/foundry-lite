"""Application service helpers for materialization types workflows."""

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

# The only per-object-type materialization mode that ontology YAML may declare
# today. Ontology validation and run-time spec resolution share this constant
# so a YAML-accepted mode can never be unrunnable.
OBJECT_SNAPSHOT_MODE: Final = "object_snapshot"
MATERIALIZATION_MODES: Final = frozenset({OBJECT_SNAPSHOT_MODE})


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


def object_snapshot_spec(object_type_api_name: str, dataset_ref: str) -> MaterializationSpec:
    """Build the runnable spec for one ontology-declared object-snapshot materialization."""
    return MaterializationSpec(
        materialization_type=OBJECT_SNAPSHOT_MODE,
        source={"objectType": object_type_api_name},
        target={"dataset": dataset_ref},
    )


def materialization_spec_name(dataset_ref: str) -> str:
    """Derive a spec api name from its target dataset ref (``ops.order_current`` -> ``order_current``).

    Deriving the name from the dataset keeps YAML declarations to a single
    source of truth; ontology validation rejects colliding derived names.
    """
    _, _, name = dataset_ref.partition(".")
    return name or dataset_ref


# action_log is a built-in platform spec: it reads action runs, not one object
# type, so it cannot be declared per object type in ontology YAML.
BUILTIN_MATERIALIZATION_SPECS: dict[str, MaterializationSpec] = {
    "action_log": MaterializationSpec(
        materialization_type="action_log",
        source={"type": "action_runs"},
        target={"dataset": "ops.action_log"},
    ),
}

# order_current predates declarative materializations. Ontologies that do not
# declare ``materialization:`` on Order (older fixtures and deployments) still
# resolve this name; an ontology-declared spec with the same name always wins.
LEGACY_MATERIALIZATION_SPECS: dict[str, MaterializationSpec] = {
    "order_current": object_snapshot_spec("Order", "ops.order_current"),
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
