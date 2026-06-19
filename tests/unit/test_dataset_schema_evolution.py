from __future__ import annotations

from typing import cast

from foundry_lite.application.services.dataset.schema_evolution import (
    DatasetSchemaEvolutionChange,
    plan_schema_evolution,
    resume_schema_backfill_progress,
)


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


def _schema(*columns: dict[str, object]) -> dict[str, object]:
    return {"columns": list(columns), "primary_key": ["order_id"], "cdc": {"enabled": False}}
