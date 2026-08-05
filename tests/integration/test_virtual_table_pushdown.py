"""Virtual table reads execute at the source, against a real Postgres.

The claim a virtual table makes is that rows are filtered before they cross the network. That
claim cannot be checked by looking at the returned rows — a local filter produces the same
answer. So these tests ask the database what it actually did: `EXPLAIN ANALYZE` reports
`Rows Removed by Filter`, which is only non-zero when the predicate ran inside Postgres.

They also pin the honesty requirement. A predicate Postgres cannot express is still applied,
but it is reported as local, because an adapter that silently widened a read into a full scan
would be indistinguishable from one that pushed everything down.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.ports.virtual_table import (
    VirtualTablePredicate,
    VirtualTableQuery,
    VirtualTableSchema,
    projected_columns,
    schema_drift,
)
from foundry_lite.infrastructure.adapters.postgres_virtual_table import PostgresVirtualTableReader
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

_CONFIG = {"schema": "public", "table": "youtube_videos"}
_ROW_COUNT = 500


@pytest.fixture(scope="module")
def connection_url() -> Iterator[str]:
    """A Postgres shaped like the collected-content table this feature exists to point at."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE youtube_videos ("
                    "  id serial PRIMARY KEY,"
                    "  video_id text NOT NULL,"
                    "  title text,"
                    "  view_count integer,"
                    "  sentiment text,"
                    "  updated_at timestamptz DEFAULT now())"
                )
            )
            for index in range(_ROW_COUNT):
                connection.execute(
                    text(
                        "INSERT INTO youtube_videos (video_id, title, view_count, sentiment) "
                        "VALUES (:video_id, :title, :view_count, :sentiment)"
                    ),
                    {
                        "video_id": f"vid-{index}",
                        "title": f"올리지오 리프팅 후기 {index}",
                        "view_count": index * 10,
                        "sentiment": "negative" if index % 7 == 0 else "positive",
                    },
                )
        yield url


@pytest.fixture
def reader() -> PostgresVirtualTableReader:
    return PostgresVirtualTableReader()


def _explain_rows_removed(connection_url: str, sql: str, parameters: dict[str, object]) -> int:
    engine = create_engine(connection_url, future=True)
    with engine.connect() as connection:
        plan = connection.execute(text(f"EXPLAIN (ANALYZE, FORMAT TEXT) {sql}"), parameters).fetchall()
    for row in plan:
        line = str(row[0])
        if "Rows Removed by Filter:" in line:
            return int(line.split("Rows Removed by Filter:")[1].strip())
    return 0


def test_registration_pins_the_external_column_shape(reader: PostgresVirtualTableReader, connection_url: str) -> None:
    schema = reader.describe(connection_url=connection_url, config=_CONFIG)

    assert schema.column_names() == ("id", "video_id", "title", "view_count", "sentiment", "updated_at")
    by_name = {column.name: column for column in schema.columns}
    assert by_name["view_count"].data_type == "integer"
    assert by_name["video_id"].is_nullable is False


def test_describe_on_a_missing_table_fails_as_an_invalid_pointer(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    with pytest.raises(AdapterError) as error:
        reader.describe(connection_url=connection_url, config={"schema": "public", "table": "ghost_table"})

    assert error.value.failure.kind == "not_found"
    assert error.value.failure.is_retryable is False


def test_predicates_run_inside_postgres_not_in_this_process(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    """`Rows Removed by Filter` is non-zero only when the source did the filtering."""
    removed = _explain_rows_removed(
        connection_url,
        'SELECT "video_id" FROM "public"."youtube_videos" WHERE "sentiment" = :p0 AND "view_count" > :p1 LIMIT 5',
        {"p0": "negative", "p1": 2000},
    )

    assert removed > 0, "the predicate did not reach the database"

    result = reader.read(
        connection_url=connection_url,
        config=_CONFIG,
        query=VirtualTableQuery(
            predicates=(
                VirtualTablePredicate("sentiment", "eq", "negative"),
                VirtualTablePredicate("view_count", "gt", 2000),
            ),
            projection=("video_id", "view_count"),
            limit=5,
        ),
    )

    assert result.has_full_push_down()
    assert len(result.rows) == 5
    assert all(row["sentiment" if "sentiment" in row else "video_id"] is not None for row in result.rows)
    assert result.network_evidence["rowsFetched"] == 5, "only the bounded page crossed the network"


def test_projection_limits_the_columns_that_leave_the_source(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    result = reader.read(
        connection_url=connection_url,
        config=_CONFIG,
        query=VirtualTableQuery(projection=("video_id",), limit=3),
    )

    assert [sorted(row) for row in result.rows] == [["video_id"]] * 3


def test_limit_is_enforced_at_the_source_not_after_the_fetch(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    """A pointer at a large table must never pull the table to honour a small limit."""
    result = reader.read(connection_url=connection_url, config=_CONFIG, query=VirtualTableQuery(limit=10))

    assert len(result.rows) == 10
    assert result.network_evidence["rowsFetched"] == 10
    assert result.network_evidence["rowsFetched"] < _ROW_COUNT


def test_an_unpushable_predicate_is_applied_locally_and_reported(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    """Silently widening a read into a scan must look different from pushing everything down."""
    result = reader.read(
        connection_url=connection_url,
        config=_CONFIG,
        query=VirtualTableQuery(
            predicates=(
                VirtualTablePredicate("sentiment", "eq", "negative"),
                VirtualTablePredicate("title", "contains", "리프팅"),
            ),
            limit=20,
        ),
    )

    assert result.has_full_push_down() is False
    assert [predicate.column for predicate in result.pushed_down_predicates] == ["sentiment"]
    assert [predicate.column for predicate in result.local_predicates] == ["title"]
    assert result.rows, "the local predicate still had to be applied, not dropped"
    assert all("리프팅" in str(row["title"]) for row in result.rows)


def test_a_predicate_on_an_unpinned_column_is_not_pushed(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    schema = VirtualTableSchema(columns=reader.describe(connection_url=connection_url, config=_CONFIG).columns[:2])

    result = reader.read(
        connection_url=connection_url,
        config=_CONFIG,
        query=VirtualTableQuery(predicates=(VirtualTablePredicate("sentiment", "eq", "negative"),), limit=5),
        schema=schema,
    )

    assert [predicate.column for predicate in result.local_predicates] == ["sentiment"]


def test_projection_outside_the_pinned_schema_is_refused(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    """A column that appeared at the source after registration is not part of the contract."""
    pinned = reader.describe(connection_url=connection_url, config=_CONFIG)

    with pytest.raises(ValueError, match="absent from the pinned schema"):
        projected_columns(pinned, ["video_id", "column_added_later"])


def test_schema_drift_names_the_column_that_moved(reader: PostgresVirtualTableReader, connection_url: str) -> None:
    pinned = reader.describe(connection_url=connection_url, config=_CONFIG)
    observed = VirtualTableSchema(
        columns=tuple(
            column if column.name != "view_count" else type(column)(name="view_count", data_type="text")
            for column in pinned.columns
            if column.name != "sentiment"
        )
    )

    findings = schema_drift(pinned, observed)

    assert "column removed at source: sentiment" in findings
    assert "column type changed at source: view_count integer -> text" in findings


def test_an_identical_schema_reports_no_drift(reader: PostgresVirtualTableReader, connection_url: str) -> None:
    pinned = reader.describe(connection_url=connection_url, config=_CONFIG)

    assert schema_drift(pinned, pinned) == ()


def test_a_zero_or_negative_limit_is_refused(reader: PostgresVirtualTableReader, connection_url: str) -> None:
    with pytest.raises(ValueError, match="limit must be positive"):
        reader.read(connection_url=connection_url, config=_CONFIG, query=VirtualTableQuery(limit=0))


def test_an_unsafe_table_identifier_is_refused_before_any_sql_runs(
    reader: PostgresVirtualTableReader, connection_url: str
) -> None:
    with pytest.raises(ValueError, match="unsafe SQL identifier"):
        reader.read(
            connection_url=connection_url,
            config={"schema": "public", "table": 'youtube_videos"; DROP TABLE youtube_videos; --'},
            query=VirtualTableQuery(limit=1),
            schema=VirtualTableSchema(columns=()),
        )
