"""Infrastructure adapter implementation for source database."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import create_engine, inspect, text

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter, SourceTableBatch
from foundry_lite.application.primitives import SQL_IDENTIFIER_PATTERN, _json_ready
from foundry_lite.domain.errors import ValidationFailed


class SqlAlchemySourceDatabaseAdapter:
    """SQLAlchemy-backed adapter for Postgres-compatible source exploration."""

    profile_name = "sqlalchemy-source-database"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("list_tables", "validation", True, "The database source could not be explored."),
                AdapterFailureMode(
                    "read_table_batch", "validation", True, "The database table batch could not be read."
                ),
            ),
        )

    def list_tables(self, database_url: str, *, sample_limit: int) -> Sequence[Mapping[str, object]]:
        _require_positive_limit(sample_limit)
        engine = create_engine(database_url, future=True)
        with engine.connect() as conn:
            inspector = inspect(conn)
            return tuple(
                _table_summary(name, inspector.get_columns(name)) for name in inspector.get_table_names()[:sample_limit]
            )

    def read_table_batch(
        self,
        database_url: str,
        *,
        table_name: str,
        batch_limit: int,
        checkpoint_column: str | None = None,
        after_value: object | None = None,
    ) -> SourceTableBatch:
        _require_safe_table_name(table_name)
        _require_positive_limit(batch_limit)
        if checkpoint_column is not None:
            _require_safe_identifier(checkpoint_column, "checkpointColumn")
        engine = create_engine(database_url, future=True)
        with engine.connect() as conn:
            rows = (
                conn.execute(_select_statement(table_name, checkpoint_column, batch_limit), {"after": after_value})
                .mappings()
                .all()
            )
        normalized = tuple(_json_ready(dict(row)) for row in rows)
        row_maps = tuple(row for row in normalized if isinstance(row, Mapping))
        return SourceTableBatch(
            rows=row_maps, schema=_schema(row_maps), checkpoint=_checkpoint(row_maps, checkpoint_column)
        )


def _table_summary(table_name: str, columns: Sequence[Mapping[str, object]]) -> dict[str, object]:
    return {
        "tableName": table_name,
        "columns": [{"name": str(column["name"]), "type": str(column["type"])} for column in columns],
    }


def _select_statement(table_name: str, checkpoint_column: str | None, batch_limit: int) -> Any:
    quoted_table = _quoted_table_name(table_name)
    if checkpoint_column is None:
        return text(f"SELECT * FROM {quoted_table} LIMIT {batch_limit}")  # nosec B608 - identifier and limit validated.
    quoted_column = _quoted_identifier(checkpoint_column)
    return text(  # nosec B608 - identifiers and limit are validated before SQL text construction.
        f"SELECT * FROM {quoted_table} "  # nosec B608 - validated quoted identifier only.
        f"WHERE (:after IS NULL OR {quoted_column} > :after) "
        f"ORDER BY {quoted_column} ASC LIMIT {batch_limit}"
    )


def _schema(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    if not rows:
        return {"columns": []}
    return {"columns": [{"name": name, "type": type(value).__name__} for name, value in rows[0].items()]}


def _checkpoint(rows: Sequence[Mapping[str, object]], checkpoint_column: str | None) -> dict[str, object]:
    if checkpoint_column is None or not rows:
        return {}
    return {"checkpointColumn": checkpoint_column, "lastValue": rows[-1].get(checkpoint_column)}


def _quoted_table_name(table_name: str) -> str:
    return ".".join(_quoted_identifier(part) for part in table_name.split("."))


def _quoted_identifier(value: str) -> str:
    _require_safe_identifier(value, "identifier")
    return f'"{value}"'


def _require_safe_table_name(table_name: str) -> None:
    for part in table_name.split("."):
        _require_safe_identifier(part, "tableName")


def _require_safe_identifier(value: str, field: str) -> None:
    if SQL_IDENTIFIER_PATTERN.fullmatch(value):
        return
    raise ValidationFailed("database source identifier is unsafe", details={field: value})


def _require_positive_limit(value: int) -> None:
    if 1 <= value <= 10_000:
        return
    raise ValidationFailed("source batch limit must be between 1 and 10000", details={"batch_limit": value})


_: SourceDatabaseAdapter = SqlAlchemySourceDatabaseAdapter()
