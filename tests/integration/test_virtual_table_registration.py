"""Registering a virtual table against a live source, and what registration is allowed to hold.

A pointer is only worth anything if the shape it pins is the shape the source actually has, so
registration asks the source rather than trusting the caller. These tests run against a real
Postgres because the interesting failures -- a column that was renamed, a table that does not
exist, a credential that leaked into the registry -- are not visible against a fake reader.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from foundry_lite.application.ports.adapter_failure import AdapterError
from foundry_lite.application.services.virtual_table_service import VirtualTableService
from foundry_lite.domain.context import RequestContext
from foundry_lite.domain.errors import ConflictDetected, NotFound, ValidationFailed
from foundry_lite.infrastructure.adapters.postgres_virtual_table import PostgresVirtualTableReader
from foundry_lite.infrastructure.repositories.virtual_table_repository import (
    SqlAlchemyVirtualTableRepository,
)
from foundry_lite.infrastructure.schema.sources import metadata as sources_metadata
from sqlalchemy import create_engine, text
from testcontainers.postgres import PostgresContainer

_CTX = RequestContext(roles=("admin",), actor_user_id="u-1", tenant_id="tenant-a")
_VAULT_PATH = "src/ongleam/url"
_CONFIG: dict[str, Any] = {"schema": "public", "table": "youtube_videos", "databaseUrlSecretRef": _VAULT_PATH}


class _StubVault:
    """Holds the URL so the registry never has to."""

    def __init__(self, url: str) -> None:
        self._url = url

    def get_secret(self, name: str, *, version: str | None = None) -> Any:
        del version
        assert name == _VAULT_PATH
        return type("Secret", (), {"value": self._url})()


class _StubPolicy:
    def require(self, ctx: RequestContext, permission: str) -> None:
        del ctx, permission


class _RecordingAudit:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def _audit(self, conn: object, ctx: RequestContext, **fields: Any) -> None:
        del conn, ctx
        self.events.append(fields)


@pytest.fixture(scope="module")
def source_url() -> Iterator[str]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as postgres:
        url = postgres.get_connection_url()
        engine = create_engine(url, future=True)
        with engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE youtube_videos ("
                    "  id serial PRIMARY KEY,"
                    "  video_id text NOT NULL,"
                    "  view_count integer)"
                )
            )
        engine.dispose()
        yield url


@pytest.fixture(scope="module")
def registry_engine() -> Iterator[Any]:
    """One registry container for the file. A container per test is minutes of wall clock for
    isolation that emptying a table buys just as well."""
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as registry:
        engine = create_engine(registry.get_connection_url(), future=True)
        sources_metadata.create_all(engine)
        yield engine
        engine.dispose()


@pytest.fixture
def service(registry_engine: Any, source_url: str) -> Iterator[tuple[VirtualTableService, _RecordingAudit]]:
    """The registry lives in our own Postgres; the pointed-at table lives in the source's."""
    with registry_engine.begin() as connection:
        connection.execute(text("TRUNCATE TABLE virtual_tables"))
    audit = _RecordingAudit()
    built = VirtualTableService.__new__(VirtualTableService)
    built.engine = registry_engine
    built.policy = _StubPolicy()  # type: ignore[assignment]
    built.virtual_table_repository = SqlAlchemyVirtualTableRepository(registry_engine)  # type: ignore[assignment]
    built.virtual_table_reader = PostgresVirtualTableReader()  # type: ignore[assignment]
    built.secret_vault = _StubVault(source_url)  # type: ignore[assignment]
    built.runtime_service = audit  # type: ignore[assignment]
    yield built, audit


def test_registration_pins_the_shape_the_source_actually_has(service: Any) -> None:
    """The caller does not supply a schema. A shape nobody verified is a promise nobody checked."""
    built, _ = service

    record = built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )

    assert record.schema.column_names() == ("id", "video_id", "view_count")
    assert record.rid.startswith("vt")
    assert record.tenant_id == "tenant-a"


def test_registering_a_table_the_source_does_not_have_fails(service: Any) -> None:
    """A pointer to nothing is worse than no pointer: it fails later, in someone else's pipeline."""
    built, _ = service

    with pytest.raises(AdapterError, match="external table not found: public.no_such_table"):
        built.register_virtual_table(
            name="absent",
            parent_rid="folder-1",
            connection_rid="conn-1",
            config={**_CONFIG, "table": "no_such_table"},
            ctx=_CTX,
        )


def test_the_same_name_cannot_be_registered_twice_in_one_folder(service: Any) -> None:
    built, _ = service
    built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )

    with pytest.raises(ConflictDetected, match="already registered"):
        built.register_virtual_table(
            name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
        )


@pytest.mark.parametrize(
    "reference",
    [
        # The whole point of the pointer design: a URL here would put a password in the registry,
        # in every audit payload, and in every API response that returns the record.
        "postgresql://user:hunter2@db.internal:5432/app",
        "user@host",
        "",
        None,
    ],
)
def test_a_config_that_carries_a_credential_is_refused(service: Any, reference: object) -> None:
    built, _ = service
    config = {"schema": "public", "table": "youtube_videos"}
    if reference is not None:
        config["databaseUrlSecretRef"] = str(reference)

    with pytest.raises(ValidationFailed, match="secret"):
        built.register_virtual_table(
            name="leaky", parent_rid="folder-1", connection_rid="conn-1", config=config, ctx=_CTX
        )


def test_a_registered_pointer_is_listed_and_fetched_by_rid(service: Any) -> None:
    built, _ = service
    record = built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )

    assert [item.rid for item in built.list_virtual_tables(connection_rid="conn-1", ctx=_CTX)] == [record.rid]
    assert built.get_virtual_table(record.rid, ctx=_CTX).name == "youtube_videos"


def test_a_pointer_from_another_tenant_is_not_visible(service: Any) -> None:
    """Tenant scoping is the registry's job, and a pointer names a table someone else may not see."""
    built, _ = service
    record = built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )
    other = RequestContext(roles=("admin",), actor_user_id="u-2", tenant_id="tenant-b")

    assert built.list_virtual_tables(connection_rid="conn-1", ctx=other) == ()
    with pytest.raises(NotFound):
        built.get_virtual_table(record.rid, ctx=other)


def test_schema_drift_is_reported_and_never_absorbed(service: Any, source_url: str) -> None:
    """A pipeline node was built against the pinned columns, so adopting the change silently
    would move what those consumers resolve to without anyone approving it.

    Drifts its own table rather than the shared one: an ALTER on a module-scoped fixture is
    visible to every other test in the file, and under a shuffled order that is a failure whose
    cause is somewhere else entirely.
    """
    built, _ = service
    engine = create_engine(source_url, future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE drifting (id integer, video_id text)"))
    record = built.register_virtual_table(
        name="drifting",
        parent_rid="folder-1",
        connection_rid="conn-1",
        config={**_CONFIG, "table": "drifting"},
        ctx=_CTX,
    )
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE drifting ADD COLUMN sentiment text"))
    engine.dispose()

    drift = built.inspect_schema_drift(record.rid, ctx=_CTX)

    assert drift["hasDrifted"] is True
    assert drift["addedColumns"] == ["sentiment"]
    assert drift["removedColumns"] == []
    # Pinned, not refreshed: the stored record still describes what consumers were built against.
    assert built.get_virtual_table(record.rid, ctx=_CTX).schema.column_names() == ("id", "video_id")


def test_deleting_a_pointer_leaves_the_external_table_alone(service: Any, source_url: str) -> None:
    built, _ = service
    record = built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )

    built.delete_virtual_table(record.rid, ctx=_CTX)

    with pytest.raises(NotFound):
        built.get_virtual_table(record.rid, ctx=_CTX)
    engine = create_engine(source_url, future=True)
    with engine.begin() as connection:
        assert connection.execute(text("SELECT count(*) FROM youtube_videos")).scalar() == 0
    engine.dispose()


def test_the_audit_trail_records_the_pointer_and_not_the_way_behind_it(service: Any) -> None:
    """An audit payload is read by people who are not allowed the credential."""
    built, audit = service
    built.register_virtual_table(
        name="youtube_videos", parent_rid="folder-1", connection_rid="conn-1", config=_CONFIG, ctx=_CTX
    )

    event = audit.events[0]
    assert event["event_type"] == "virtual_table.register"
    assert event["after_ref"]["name"] == "youtube_videos"
    assert "hunter2" not in str(event) and "databaseUrlSecretRef" not in str(event["after_ref"])
