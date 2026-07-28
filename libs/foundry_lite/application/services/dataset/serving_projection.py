"""Dataset serving projection derived from durable transaction evidence."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from foundry_lite.application.ports import TabularRow


def project_dataset_serving_rows(
    rows: Sequence[Mapping[str, object]],
    transaction_metadata: Mapping[str, object],
    schema_contract: Mapping[str, object],
) -> list[TabularRow]:
    """Hide storage-derived columns when a Pipeline output contract is present."""

    columns = transaction_metadata.get("servingColumns")
    fieldnames = _serving_fieldnames(columns)
    if not fieldnames:
        fieldnames = _schema_fieldnames(schema_contract.get("columns"))
    if not fieldnames:
        return [dict(row) for row in rows]
    return [{field: row.get(field) for field in fieldnames} for row in rows]


def _serving_fieldnames(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(column) for column in value if isinstance(column, str) and column)


def _schema_fieldnames(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(
        str(column["name"]) for column in value if isinstance(column, Mapping) and isinstance(column.get("name"), str)
    )
