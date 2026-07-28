"""Deterministic schema-evolution backfill progress."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from foundry_lite.application.primitives import _json_hash


class SchemaBackfillChange(Protocol):
    @property
    def kind(self) -> str: ...

    @property
    def column(self) -> str: ...

    @property
    def reason(self) -> str: ...


def resume_schema_backfill_progress(
    *,
    dataset_id: str,
    source_schema_hash: str,
    target_schema_hash: str,
    changes: Sequence[SchemaBackfillChange],
    existing_progress: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build deterministic backfill progress while preserving completed steps."""
    steps = [_backfill_step(change) for change in changes]
    backfill_key = _backfill_key(dataset_id, source_schema_hash, target_schema_hash, steps)
    completed = _completed_step_ids(existing_progress, backfill_key)
    pending = [step for step in steps if step["id"] not in completed]
    return {
        "backfillKey": backfill_key,
        "status": "COMPLETED" if not pending else "PENDING",
        "steps": steps,
        "completedStepIds": sorted(completed),
        "nextStep": _next_step_message(pending),
    }


def _backfill_step(change: SchemaBackfillChange) -> dict[str, object]:
    return {
        "id": f"{change.kind}:{change.column}",
        "kind": change.kind,
        "column": change.column,
        "status": "PENDING",
        "description": change.reason,
    }


def _backfill_key(
    dataset_id: str,
    source_schema_hash: str,
    target_schema_hash: str,
    steps: Sequence[Mapping[str, object]],
) -> str:
    return "schema_backfill:" + _json_hash(
        {
            "datasetId": dataset_id,
            "sourceSchemaHash": source_schema_hash,
            "targetSchemaHash": target_schema_hash,
            "steps": list(steps),
        }
    )


def _completed_step_ids(existing_progress: Mapping[str, object] | None, backfill_key: str) -> set[str]:
    if existing_progress is None or existing_progress.get("backfillKey") != backfill_key:
        return set()
    value = existing_progress.get("completedStepIds")
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        return set()
    return {str(item) for item in value}


def _next_step_message(pending: Sequence[Mapping[str, object]]) -> str:
    if not pending:
        return "Schema evolution backfill already complete."
    return str(pending[0]["description"])
