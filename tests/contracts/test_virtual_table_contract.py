"""Virtual table registry contract: pointers only, tenant-scoped, name-unique per folder.

The registry stores what a virtual table *is*, never what it contains. Two properties carry
weight here. Registration is unique per (tenant, parent, name) because a downstream object type
or pipeline node refers to a pointer by name, and a second pointer with the same name in the
same folder makes that reference ambiguous — so it is a conflict, not an overwrite. And the
pinned schema survives the round trip intact, because drift detection compares against it: a
pin that degraded in storage would report drift that never happened, or miss drift that did.
"""

from __future__ import annotations

import pytest
from foundry_lite.application.ports.virtual_table import (
    VirtualTableAlreadyExistsError,
    VirtualTableColumn,
    VirtualTableRecord,
    VirtualTableRepository,
    VirtualTableSchema,
    schema_drift,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyVirtualTableRepository
from sqlalchemy import create_engine


@pytest.fixture
def repository(tmp_path) -> VirtualTableRepository:
    engine = create_engine(f"sqlite:///{tmp_path / 'virtual-tables.db'}", future=True)
    db.create_database(engine)
    return SqlAlchemyVirtualTableRepository(engine)


def _record(
    rid: str = "vt-1",
    *,
    tenant_id: str = "tenant-demo",
    name: str = "youtube_videos",
    parent_rid: str = "folder-1",
    connection_rid: str = "conn-1",
) -> VirtualTableRecord:
    return VirtualTableRecord(
        rid=rid,
        tenant_id=tenant_id,
        name=name,
        parent_rid=parent_rid,
        connection_rid=connection_rid,
        config={"schema": "public", "table": "youtube_videos"},
        schema=VirtualTableSchema(
            columns=(
                VirtualTableColumn("video_id", "text", is_nullable=False),
                VirtualTableColumn("view_count", "integer"),
            )
        ),
        markings=("pii",),
        created_at="2026-08-05T00:00:00Z",
    )


def test_registered_pointer_round_trips_with_its_pinned_schema(repository: VirtualTableRepository) -> None:
    repository.register(_record())

    found = repository.get(tenant_id="tenant-demo", rid="vt-1")

    assert found is not None
    assert found.name == "youtube_videos"
    assert found.config == {"schema": "public", "table": "youtube_videos"}
    assert found.markings == ("pii",)
    assert found.schema.column_names() == ("video_id", "view_count")
    by_name = {column.name: column for column in found.schema.columns}
    assert by_name["video_id"].data_type == "text"
    assert by_name["video_id"].is_nullable is False


def test_a_round_tripped_pin_still_detects_drift(repository: VirtualTableRepository) -> None:
    """Drift compares against the stored pin, so the pin must survive storage exactly."""
    repository.register(_record())
    stored = repository.get(tenant_id="tenant-demo", rid="vt-1")
    assert stored is not None

    observed = VirtualTableSchema(
        columns=(VirtualTableColumn("video_id", "text", is_nullable=False), VirtualTableColumn("view_count", "text"))
    )

    assert schema_drift(stored.schema, stored.schema) == ()
    assert schema_drift(stored.schema, observed) == ("column type changed at source: view_count integer -> text",)


def test_a_second_pointer_with_the_same_name_in_the_folder_is_a_conflict(
    repository: VirtualTableRepository,
) -> None:
    repository.register(_record("vt-1"))

    with pytest.raises(VirtualTableAlreadyExistsError, match="already registered"):
        repository.register(_record("vt-2"))


def test_the_same_name_is_allowed_in_a_different_folder(repository: VirtualTableRepository) -> None:
    repository.register(_record("vt-1", parent_rid="folder-1"))
    repository.register(_record("vt-2", parent_rid="folder-2"))

    assert repository.get(tenant_id="tenant-demo", rid="vt-2") is not None


def test_a_pointer_is_invisible_to_another_tenant(repository: VirtualTableRepository) -> None:
    repository.register(_record())

    assert repository.get(tenant_id="tenant-other", rid="vt-1") is None


def test_listing_returns_only_this_tenant_and_connection(repository: VirtualTableRepository) -> None:
    repository.register(_record("vt-1", name="a", connection_rid="conn-1"))
    repository.register(_record("vt-2", name="b", connection_rid="conn-1"))
    repository.register(_record("vt-3", name="c", connection_rid="conn-2"))
    repository.register(_record("vt-4", name="d", tenant_id="tenant-other", connection_rid="conn-1"))

    listed = repository.list_for_connection(tenant_id="tenant-demo", connection_rid="conn-1")

    assert [record.name for record in listed] == ["a", "b"]


def test_deleting_a_pointer_leaves_other_tenants_untouched(repository: VirtualTableRepository) -> None:
    repository.register(_record("vt-1"))
    repository.register(_record("vt-2", tenant_id="tenant-other"))

    repository.delete(tenant_id="tenant-demo", rid="vt-1")

    assert repository.get(tenant_id="tenant-demo", rid="vt-1") is None
    assert repository.get(tenant_id="tenant-other", rid="vt-2") is not None


def test_deleting_across_tenants_is_a_no_op(repository: VirtualTableRepository) -> None:
    repository.register(_record())

    repository.delete(tenant_id="tenant-other", rid="vt-1")

    assert repository.get(tenant_id="tenant-demo", rid="vt-1") is not None
