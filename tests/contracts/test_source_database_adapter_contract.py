from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.source_database import (
    SqlAlchemySourceDatabaseAdapter,
    _select_statement,
)
from sqlalchemy import create_engine, text


@pytest.fixture
def database_url(tmp_path: Path) -> str:
    url = f"sqlite:///{tmp_path / 'source.db'}"
    engine = create_engine(url, future=True)
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE orders (id INTEGER PRIMARY KEY, amount INTEGER, status TEXT)"))
        conn.execute(text("INSERT INTO orders (id, amount, status) VALUES (1, 20, 'open'), (2, 30, 'closed')"))
    return url


@pytest.fixture
def adapter() -> SourceDatabaseAdapter:
    return SqlAlchemySourceDatabaseAdapter()


def test_source_database_adapter_lists_tables_with_schema(adapter: SourceDatabaseAdapter, database_url: str) -> None:
    tables = list(adapter.list_tables(database_url, sample_limit=10))

    assert [table["tableName"] for table in tables] == ["orders"]
    assert {column["name"] for column in tables[0]["columns"]} == {"id", "amount", "status"}


def test_source_database_adapter_tests_live_connection_without_exposing_url(
    adapter: SourceDatabaseAdapter, database_url: str
) -> None:
    probe = adapter.test_connection(database_url)

    assert probe.adapter_profile == adapter.profile_name
    assert probe.database_kind == "sqlite"
    assert probe.driver == "pysqlite"
    assert probe.visible_resource_count == 1


def test_source_database_adapter_reads_checkpointed_batches(adapter: SourceDatabaseAdapter, database_url: str) -> None:
    batch = adapter.read_table_batch(
        database_url,
        table_name="orders",
        batch_limit=10,
        checkpoint_column="id",
        after_value=1,
    )

    assert batch.rows == ({"id": 2, "amount": 30, "status": "closed"},)
    assert batch.schema == {
        "columns": [
            {"name": "id", "type": "int"},
            {"name": "amount", "type": "int"},
            {"name": "status", "type": "str"},
        ]
    }
    assert batch.checkpoint == {"checkpointColumn": "id", "lastValue": 2}


def test_source_database_adapter_rejects_unsafe_identifiers(adapter: SourceDatabaseAdapter, database_url: str) -> None:
    with pytest.raises(ValidationFailed):
        adapter.read_table_batch(database_url, table_name="orders;drop", batch_limit=10)


def test_source_database_adapter_failure_contract_names_operations(adapter: SourceDatabaseAdapter) -> None:
    contract = adapter.failure_contract()

    assert contract.adapter_profile == adapter.profile_name
    assert {mode.operation for mode in contract.modes} == {"test_connection", "list_tables", "read_table_batch"}


def test_source_database_first_batch_omits_the_checkpoint_parameter() -> None:
    """The opening batch carries no checkpoint parameter at all.

    Neutralising the predicate with ``:after IS NULL OR ...`` leaves the parameter's type
    underdetermined, which PostgreSQL refuses to prepare — so the first batch of every
    ``postgres_jdbc`` sync would fail before reading a row.
    """
    statement = _select_statement("orders", "id", 10, has_checkpoint=False)
    sql = str(statement)

    assert ":after" not in sql
    assert "IS NULL" not in sql
    assert 'ORDER BY "id" ASC' in sql


def test_source_database_resumed_batch_bounds_the_scan_by_the_checkpoint() -> None:
    statement = _select_statement("orders", "id", 10, has_checkpoint=True)
    sql = str(statement)

    assert '"id" > :after' in sql
    assert "IS NULL" not in sql
