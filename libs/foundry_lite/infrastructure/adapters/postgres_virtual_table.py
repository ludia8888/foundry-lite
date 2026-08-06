"""Postgres virtual-table reader (profile ``postgres-virtual-table``).

Executes the read against the external database rather than pulling the table in and filtering
here. Palantir's phrasing for this is compute pushdown — translate what the source can express
into its own query language so rows are removed before they cross the network.

What the source cannot express is still applied, locally, and *reported*. An adapter that
quietly widened a read into a full scan would look identical to one that pushed everything
down, and the caller would have no way to notice the difference until the bill arrived.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal

from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from foundry_lite.application.ports.adapter_failure import (
    AdapterError,
    AdapterFailure,
    AdapterFailureContract,
    AdapterFailureMode,
)
from foundry_lite.application.ports.virtual_table import (
    ExternalTableRef,
    VirtualTableColumn,
    VirtualTablePredicate,
    VirtualTableQuery,
    VirtualTableReadResult,
    VirtualTableSchema,
    projected_columns,
)

# Operators Postgres can evaluate itself. Anything outside this set is applied locally and
# reported, rather than being rejected — a caller asking for an unusual predicate should still
# get an answer, just with honest evidence about where the work happened.
_PUSHABLE_OPERATORS: Mapping[str, str] = {
    "eq": "=",
    "ne": "<>",
    "gt": ">",
    "gte": ">=",
    "lt": "<",
    "lte": "<=",
}
_MAX_ROW_LIMIT = 10_000


class PostgresVirtualTableReader:
    """``VirtualTableReader`` that pushes predicates, projection, and limit into Postgres."""

    profile_name = "postgres-virtual-table"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode(
                    "read",
                    "unavailable",
                    True,
                    "The external database refused or dropped the connection; the pointer is intact.",
                ),
                AdapterFailureMode(
                    "read",
                    "not_found",
                    False,
                    "The external table or column named by the pointer no longer resolves.",
                ),
            ),
        )

    def describe(self, *, connection_url: str, config: Mapping[str, object]) -> VirtualTableSchema:
        table = _qualified_table(config)
        statement = text(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :table ORDER BY ordinal_position"
        )
        with self._guard("describe"):
            engine = create_engine(connection_url, future=True)
            with engine.connect() as connection:
                rows = connection.execute(
                    statement, {"schema": _schema_name(config), "table": _table_name(config)}
                ).mappings()
                columns = tuple(
                    VirtualTableColumn(
                        name=str(row["column_name"]),
                        data_type=str(row["data_type"]),
                        is_nullable=str(row["is_nullable"]).upper() == "YES",
                    )
                    for row in rows
                )
        if not columns:
            raise AdapterError(
                AdapterFailure(self.profile_name, "describe", "not_found", False, f"external table not found: {table}")
            )
        return VirtualTableSchema(columns=columns)

    def discover(self, *, connection_url: str, schema_names: tuple[str, ...] = ()) -> tuple[ExternalTableRef, ...]:
        """List reachable tables, excluding the system catalogs nobody registers a pointer to.

        Base tables and views only: a sequence or an index is not something a pointer can read,
        and returning them would put entries in a picker that fail the moment anyone selects one.
        """
        clause = "AND table_schema = ANY(:schemas)" if schema_names else ""
        statement = text(
            "SELECT table_schema, table_name FROM information_schema.tables "
            "WHERE table_type IN ('BASE TABLE', 'VIEW') "
            "AND table_schema NOT IN ('pg_catalog', 'information_schema') "
            f"{clause} ORDER BY table_schema, table_name"
        )
        parameters: dict[str, object] = {"schemas": list(schema_names)} if schema_names else {}
        with self._guard("discover"):
            engine = create_engine(connection_url, future=True)
            with engine.connect() as connection:
                rows = connection.execute(statement, parameters).mappings()
                return tuple(
                    ExternalTableRef(schema_name=str(row["table_schema"]), table_name=str(row["table_name"]))
                    for row in rows
                )

    def read(
        self,
        *,
        connection_url: str,
        config: Mapping[str, object],
        query: VirtualTableQuery,
        schema: VirtualTableSchema | None = None,
    ) -> VirtualTableReadResult:
        pinned = schema if schema is not None else self.describe(connection_url=connection_url, config=config)
        columns = projected_columns(pinned, query.projection)
        pushed, local = _split_predicates(query.predicates, pinned)
        statement, parameters = _select(config, columns, pushed, _bounded_limit(query.limit))
        with self._guard("read"):
            engine = create_engine(connection_url, future=True)
            with engine.connect() as connection:
                rows = tuple(dict(row) for row in connection.execute(statement, parameters).mappings())
        # Local predicates run after the fetch. The row budget was already spent remotely, so
        # this narrows the answer without pretending the read was cheaper than it was.
        filtered = tuple(row for row in rows if all(_matches(row, predicate) for predicate in local))
        return VirtualTableReadResult(
            rows=filtered,
            pushed_down_predicates=pushed,
            local_predicates=local,
            network_evidence={
                "profile": self.profile_name,
                "rowsFetched": len(rows),
                "rowsReturned": len(filtered),
                "table": _qualified_table(config),
            },
        )

    def _guard(self, operation: str) -> _AdapterGuard:
        return _AdapterGuard(self.profile_name, operation)


class _AdapterGuard:
    """Translate driver exceptions into the typed failures this profile promises."""

    def __init__(self, profile: str, operation: str) -> None:
        self._profile = profile
        self._operation = operation

    def __enter__(self) -> _AdapterGuard:
        return self

    def __exit__(self, _kind: type[BaseException] | None, exc: BaseException | None, _tb: object) -> Literal[False]:
        if exc is None or not isinstance(exc, SQLAlchemyError):
            return False
        raise AdapterError(
            AdapterFailure(
                self._profile,
                self._operation,
                "unavailable",
                True,
                f"virtual table {self._operation} failed against the external database",
            )
        ) from exc


def _split_predicates(
    predicates: Sequence[VirtualTablePredicate], schema: VirtualTableSchema
) -> tuple[tuple[VirtualTablePredicate, ...], tuple[VirtualTablePredicate, ...]]:
    """Partition predicates into what Postgres will evaluate and what we must."""
    known = set(schema.column_names())
    pushed: list[VirtualTablePredicate] = []
    local: list[VirtualTablePredicate] = []
    for predicate in predicates:
        if predicate.column in known and predicate.operator in _PUSHABLE_OPERATORS:
            pushed.append(predicate)
        else:
            local.append(predicate)
    return tuple(pushed), tuple(local)


def _select(
    config: Mapping[str, object],
    columns: Sequence[str],
    predicates: Sequence[VirtualTablePredicate],
    limit: int,
) -> tuple[Any, dict[str, object]]:
    projection = ", ".join(_quoted(name) for name in columns)
    table = _quoted_qualified_table(config)
    parameters: dict[str, object] = {}
    clauses: list[str] = []
    for index, predicate in enumerate(predicates):
        key = f"p{index}"
        operator = _PUSHABLE_OPERATORS[predicate.operator]
        clauses.append(f"{_quoted(predicate.column)} {operator} :{key}")
        parameters[key] = predicate.value
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    # nosec B608 - identifiers are validated and quoted; values are bound parameters.
    return text(f"SELECT {projection} FROM {table}{where} LIMIT {limit}"), parameters  # nosec B608


def _matches(row: Mapping[str, object], predicate: VirtualTablePredicate) -> bool:
    value = row.get(predicate.column)
    if predicate.operator == "contains":
        return isinstance(value, str) and str(predicate.value) in value
    if predicate.operator == "in":
        return isinstance(predicate.value, (list, tuple, set)) and value in predicate.value
    if predicate.operator == "isNull":
        return value is None
    return False


def _bounded_limit(limit: int) -> int:
    if limit < 1:
        raise ValueError("virtual table read limit must be positive")
    return min(limit, _MAX_ROW_LIMIT)


def _schema_name(config: Mapping[str, object]) -> str:
    return str(config.get("schema") or "public")


def _table_name(config: Mapping[str, object]) -> str:
    name = config.get("table")
    if not isinstance(name, str) or not name:
        raise ValueError("virtual table config requires a table name")
    return name


def _qualified_table(config: Mapping[str, object]) -> str:
    return f"{_schema_name(config)}.{_table_name(config)}"


def _quoted_qualified_table(config: Mapping[str, object]) -> str:
    return f"{_quoted(_schema_name(config))}.{_quoted(_table_name(config))}"


def _quoted(identifier: str) -> str:
    if not identifier or not identifier.replace("_", "").isalnum():
        raise ValueError(f"unsafe SQL identifier: {identifier!r}")
    return f'"{identifier}"'
