from __future__ import annotations

from typing import cast

import pytest
from foundry_lite.application.ports import DatasetSchemaReference
from foundry_lite.application.services.dataset.schema_evolution import (
    DatasetSchemaEvolutionChange,
    build_schema_evolution_result,
    plan_schema_evolution,
    resolve_inferred_null_types,
    resume_schema_backfill_progress,
    schema_columns_by_name,
)
from foundry_lite.domain.errors import ValidationFailed


def test_schema_evolution_allows_numeric_widening_with_warning() -> None:
    plan = plan_schema_evolution(
        current_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "amount", "type": "integer", "nullable": True},
        ),
        next_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "amount", "type": "float", "nullable": True},
        ),
        primary_key=("order_id",),
    )

    assert plan.status == "compatible_with_warning"
    assert plan.failure() is None
    assert [change.to_payload()["kind"] for change in plan.warning_changes] == ["type_widening"]


def test_schema_evolution_blocks_rename_and_deleted_source_column() -> None:
    plan = plan_schema_evolution(
        current_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "status", "type": "string", "nullable": True},
        ),
        next_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "state", "type": "string", "nullable": True, "renamedFrom": "status"},
        ),
        primary_key=("order_id",),
    )

    failure = plan.failure()

    assert plan.status == "blocked"
    assert failure is not None
    assert {change["kind"] for change in cast(list[dict[str, object]], failure["blockedChanges"])} == {
        "drop_column",
        "rename_column",
    }


def test_schema_evolution_marks_deprecated_field_as_consumer_warning() -> None:
    plan = plan_schema_evolution(
        current_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "legacy_status", "type": "string", "nullable": True},
        ),
        next_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "legacy_status", "type": "string", "nullable": True, "deprecated": True},
        ),
        primary_key=("order_id",),
    )

    assert plan.status == "compatible_with_warning"
    assert [change.to_payload()["kind"] for change in plan.warning_changes] == ["deprecate_column"]


def test_backfill_resumes_idempotently() -> None:
    change = DatasetSchemaEvolutionChange(
        kind="add_required_column_with_default",
        column="region",
        status="warning",
        reason="existing rows need a deterministic backfill/default proof",
        next_type="string",
        require_backfill=True,
    )
    first = resume_schema_backfill_progress(
        dataset_id="ds_orders",
        source_schema_hash="schema_v1",
        target_schema_hash="schema_v2",
        changes=(change,),
    )
    steps = cast(list[dict[str, object]], first["steps"])
    completed = {**first, "completedStepIds": [steps[0]["id"]]}

    resumed = resume_schema_backfill_progress(
        dataset_id="ds_orders",
        source_schema_hash="schema_v1",
        target_schema_hash="schema_v2",
        changes=(change,),
        existing_progress=completed,
    )

    assert resumed["backfillKey"] == first["backfillKey"]
    assert resumed["completedStepIds"] == [steps[0]["id"]]
    assert resumed["status"] == "COMPLETED"
    assert resumed["nextStep"] == "Schema evolution backfill already complete."


def test_schema_evolution_builds_warning_metadata_with_backfill_progress() -> None:
    current_schema = _schema(
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "int", "nullable": False},
    )
    next_schema = _schema(
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "amount", "type": "long", "nullable": True, "deprecated": True},
        {"name": "region", "type": "string", "nullable": False, "default": "NA"},
        {"name": "note", "type": "string", "nullable": True},
    )

    result = build_schema_evolution_result(
        dataset_id="ds_orders",
        current_schema={
            "id": "schema_v1",
            "dataset_id": "ds_orders",
            "version": 1,
            "schema_json": current_schema,
            "schema_hash": "hash-v1",
            "created_at": "2026-06-19T00:00:00Z",
        },
        next_schema=next_schema,
        primary_key=("order_id",),
        target_schema=DatasetSchemaReference(schema_id="schema_v2", version=2),
    )

    metadata = result.metadata

    assert result.failure is None
    assert metadata is not None
    assert metadata["status"] == "compatible_with_warning"
    assert metadata["consumerCompatibility"] == "review_recommended"
    assert metadata["sourceSchemaVersionId"] == "schema_v1"
    assert metadata["targetSchemaVersionId"] == "schema_v2"
    assert cast(dict[str, object], metadata["backfillProgress"])["status"] == "PENDING"
    assert [change["kind"] for change in cast(list[dict[str, object]], metadata["changes"])] == [
        "add_nullable_column",
        "add_required_column_with_default",
        "type_widening",
        "relax_nullability",
        "deprecate_column",
    ]


def test_schema_evolution_blocks_required_addition_and_primary_key_change() -> None:
    plan = plan_schema_evolution(
        current_schema=_schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "amount", "type": "float", "nullable": True},
        ),
        next_schema={
            "columns": [
                {"name": "amount", "type": "integer", "nullable": True},
                {"name": "region", "type": "string", "nullable": False},
            ],
            "primary_key": ["amount"],
        },
        primary_key=("order_id",),
    )

    failure = plan.failure()

    assert failure is not None
    assert {change["kind"] for change in cast(list[dict[str, object]], failure["blockedChanges"])} == {
        "add_required_column_without_default",
        "drop_column",
        "primary_key_change",
        "type_change",
    }


def test_schema_evolution_rejects_invalid_schema_shapes() -> None:
    invalid_schemas = [
        {"columns": "bad"},
        {"columns": [42]},
        {"columns": [{1: "bad"}]},
        {"columns": [{"type": "string"}]},
    ]

    for schema in invalid_schemas:
        with pytest.raises(ValidationFailed):
            schema_columns_by_name(schema)


def test_schema_evolution_resolves_null_only_column_without_false_type_conflict() -> None:
    current = _schema(
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "note", "type": "null", "nullable": True},
    )
    concrete = _schema(
        {"name": "order_id", "type": "string", "nullable": False},
        {"name": "note", "type": "string", "nullable": True},
    )

    plan = plan_schema_evolution(current_schema=current, next_schema=concrete, primary_key=("order_id",))
    preserved = resolve_inferred_null_types(
        concrete,
        _schema(
            {"name": "order_id", "type": "string", "nullable": False},
            {"name": "note", "type": "null", "nullable": True},
        ),
    )

    assert plan.failure() is None
    assert [change.kind for change in plan.changes] == ["resolve_null_type"]
    assert schema_columns_by_name(preserved)["note"]["type"] == "string"


def _schema(*columns: dict[str, object]) -> dict[str, object]:
    return {"columns": list(columns), "primary_key": ["order_id"], "cdc": {"enabled": False}}
