from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.source_database_adapter import SourceDatabaseAdapter
from foundry_lite.domain.errors import ValidationFailed
from foundry_lite.infrastructure.adapters.source_database import SqlAlchemySourceDatabaseAdapter
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
    assert {mode.operation for mode in contract.modes} == {"list_tables", "read_table_batch"}
