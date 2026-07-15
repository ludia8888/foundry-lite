"""Infrastructure adapter implementation for source database."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from foundry_lite.application.ports.adapter_failure import AdapterFailureContract, AdapterFailureMode
from foundry_lite.application.ports.connector_adapter import ConnectorNetworkRoute
from foundry_lite.application.ports.source_database_adapter import (
    SourceDatabaseAdapter,
    SourceDatabaseConnectionProbe,
    SourceTableBatch,
)
from foundry_lite.application.primitives import SQL_IDENTIFIER_PATTERN, _json_ready
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.source_database_tunnel import SourceDatabaseTunnel


class SqlAlchemySourceDatabaseAdapter:
    """SQLAlchemy-backed adapter for Postgres-compatible source exploration."""

    profile_name = "sqlalchemy-source-database"

    def failure_contract(self) -> AdapterFailureContract:
        return AdapterFailureContract(
            adapter_profile=self.profile_name,
            modes=(
                AdapterFailureMode("list_tables", "validation", True, "The database source could not be explored."),
                AdapterFailureMode(
                    "test_connection", "validation", True, "The database source connection could not be established."
                ),
                AdapterFailureMode(
                    "read_table_batch", "validation", True, "The database table batch could not be read."
                ),
            ),
        )

    def test_connection(
        self,
        database_url: str,
        *,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> SourceDatabaseConnectionProbe:
        tunnel: SourceDatabaseTunnel | None = None
        try:
            with _database_engine(database_url, network_route) as (engine, tunnel):
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                    visible_resource_count = len(inspect(conn).get_table_names())
                return SourceDatabaseConnectionProbe(
                    adapter_profile=self.profile_name,
                    database_kind=engine.dialect.name,
                    driver=engine.dialect.driver,
                    visible_resource_count=visible_resource_count,
                    network_evidence=_success_evidence(tunnel, connection_id),
                )
        except ValidationFailed:
            raise
        except SQLAlchemyError as exc:
            raise _database_error("source database connection test failed", self.profile_name, tunnel, exc) from exc

    def list_tables(
        self,
        database_url: str,
        *,
        sample_limit: int,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> Sequence[Mapping[str, object]]:
        _require_positive_limit(sample_limit)
        tunnel: SourceDatabaseTunnel | None = None
        try:
            with _database_engine(database_url, network_route) as (engine, tunnel), engine.connect() as conn:
                inspector = inspect(conn)
                return tuple(
                    _table_summary(name, inspector.get_columns(name))
                    for name in inspector.get_table_names()[:sample_limit]
                )
        except ValidationFailed:
            raise
        except SQLAlchemyError as exc:
            raise _database_error("source database tables could not be listed", self.profile_name, tunnel, exc) from exc

    def read_table_batch(
        self,
        database_url: str,
        *,
        table_name: str,
        batch_limit: int,
        checkpoint_column: str | None = None,
        after_value: object | None = None,
        network_route: ConnectorNetworkRoute | None = None,
        connection_id: str | None = None,
    ) -> SourceTableBatch:
        _require_safe_table_name(table_name)
        _require_positive_limit(batch_limit)
        if checkpoint_column is not None:
            _require_safe_identifier(checkpoint_column, "checkpointColumn")
        try:
            rows, network_evidence = _read_batch_rows(
                database_url,
                table_name,
                checkpoint_column,
                batch_limit,
                after_value,
                network_route,
                connection_id,
            )
        except ValidationFailed:
            raise
        except SQLAlchemyError as exc:
            raise ValidationFailed(
                "source database table batch could not be read",
                details={"tableName": table_name, "adapterProfile": self.profile_name},
            ) from exc
        normalized = tuple(_json_ready(dict(row)) for row in rows)
        row_maps = tuple(row for row in normalized if isinstance(row, Mapping))
        return SourceTableBatch(
            rows=row_maps,
            schema=_schema(row_maps),
            checkpoint=_checkpoint(row_maps, checkpoint_column),
            network_evidence=network_evidence,
        )


def _read_batch_rows(
    database_url: str,
    table_name: str,
    checkpoint_column: str | None,
    batch_limit: int,
    after_value: object | None,
    network_route: ConnectorNetworkRoute | None,
    connection_id: str | None,
) -> tuple[Sequence[Mapping[str, object]], Mapping[str, object]]:
    tunnel: SourceDatabaseTunnel | None = None
    try:
        with _database_engine(database_url, network_route) as (engine, tunnel), engine.connect() as conn:
            rows = conn.execute(
                _select_statement(table_name, checkpoint_column, batch_limit), {"after": after_value}
            ).mappings()
            values = tuple({str(key): value for key, value in row.items()} for row in rows)
            return values, _success_evidence(tunnel, connection_id)
    except SQLAlchemyError as exc:
        raise _database_error(
            "source database table batch could not be read", "sqlalchemy-source-database", tunnel, exc
        ) from exc


@contextmanager
def _database_engine(
    database_url: str,
    route: ConnectorNetworkRoute | None,
) -> Iterator[tuple[Engine, SourceDatabaseTunnel | None]]:
    url = make_url(database_url)
    if route is None or not url.drivername.startswith("postgresql"):
        if route is not None and route.mode == "agent_proxy":
            raise ValidationFailed("Source Agent database tunnel currently requires a PostgreSQL URL")
        engine = create_engine(url, future=True)
        try:
            yield engine, None
        finally:
            engine.dispose()
        return
    target_host = _required_database_host(url.host)
    target_port = url.port or 5432
    with SourceDatabaseTunnel(route, target_host, target_port) as tunnel:
        tunneled_url = url.set(port=tunnel.port).update_query_dict({"hostaddr": tunnel.hostaddr})
        engine = create_engine(tunneled_url, future=True)
        try:
            yield engine, tunnel
        finally:
            engine.dispose()


def _success_evidence(tunnel: SourceDatabaseTunnel | None, connection_id: str | None) -> Mapping[str, object]:
    if tunnel is None:
        return {}
    return tunnel.evidence(connection_id or "source-database", response_flags="NONE")


def _database_error(
    message: str,
    adapter_profile: str,
    tunnel: SourceDatabaseTunnel | None,
    error: SQLAlchemyError,
) -> ValidationFailed:
    if tunnel is not None and tunnel.route_error() is not None:
        return tunnel.route_error() or ValidationFailed(message)
    details: dict[str, object] = {"adapterProfile": adapter_profile}
    if tunnel is not None:
        details["networkEvidence"] = tunnel.evidence(
            "source-database",
            response_flags=_database_response_flag(error),
        )
    return ValidationFailed(message, details=details)


def _database_response_flag(error: SQLAlchemyError) -> str:
    message = str(error).lower()
    if "ssl" in message or "certificate" in message:
        return "TLS_ERROR"
    if "password authentication failed" in message or "authentication" in message:
        return "AUTH_ERROR"
    return "DATABASE_PROTOCOL_ERROR"


def _required_database_host(value: str | None) -> str:
    if value:
        return value
    raise ValidationFailed("PostgreSQL Source Agent route requires a database host")


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
