from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.media_reference_binding_repository import MediaReferenceBindingRecord
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaReferenceBindingRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def binding_repo(
    request: pytest.FixtureRequest, tmp_path: Path
) -> tuple[SqlAlchemyMediaReferenceBindingRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'binding.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyMediaReferenceBindingRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyMediaReferenceBindingRepository(postgres_fixture.engine), postgres_fixture.engine


def _binding(*, version_id: str = "miv-1", key: str = "idem-1") -> MediaReferenceBindingRecord:
    return MediaReferenceBindingRecord(
        media_reference_binding_id="mrb-1",
        tenant_id="tenant-demo",
        holder_type="Order",
        holder_id="order-1",
        property_name="sourceDocument",
        media_set_id="ms-1",
        media_item_id="mi-1",
        media_item_version_id=version_id,
        logical_path="/contracts/acme.pdf",
        content_hash="h1",
        security_envelope={"classification": "confidential"},
        idempotency_key=key,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def test_create_and_read_binding_by_holder(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    with engine.begin() as conn:
        repo.create_binding(transaction=conn, record=_binding())
    with engine.begin() as conn:
        found = repo.binding_by_holder(
            transaction=conn,
            tenant_id="tenant-demo",
            holder_type="Order",
            holder_id="order-1",
            property_name="sourceDocument",
        )
        missing = repo.binding_by_holder(
            transaction=conn,
            tenant_id="tenant-other",
            holder_type="Order",
            holder_id="order-1",
            property_name="sourceDocument",
        )
    assert found is not None and found.media_item_version_id == "miv-1"
    assert missing is None


def _binding_at(
    *, binding_id: str, holder_id: str, version_id: str, property_name: str = "sourceDocument"
) -> MediaReferenceBindingRecord:
    return MediaReferenceBindingRecord(
        media_reference_binding_id=binding_id,
        tenant_id="tenant-demo",
        holder_type="Order",
        holder_id=holder_id,
        property_name=property_name,
        media_set_id="ms-1",
        media_item_id="mi-1",
        media_item_version_id=version_id,
        logical_path=f"/contracts/{binding_id}.pdf",
        content_hash="h1",
        security_envelope={"classification": "confidential"},
        idempotency_key=binding_id,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def test_bindings_for_media_versions_reverse_lookup_is_tenant_scoped_and_batched(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    with engine.begin() as conn:
        repo.create_binding(
            transaction=conn, record=_binding_at(binding_id="mrb-a", holder_id="order-a", version_id="miv-1")
        )
        repo.create_binding(
            transaction=conn, record=_binding_at(binding_id="mrb-b", holder_id="order-b", version_id="miv-1")
        )
        repo.create_binding(
            transaction=conn, record=_binding_at(binding_id="mrb-c", holder_id="order-c", version_id="miv-2")
        )
    with engine.begin() as conn:
        found = repo.bindings_for_media_versions(
            transaction=conn, tenant_id="tenant-demo", media_item_version_ids=["miv-1"]
        )
        other_tenant = repo.bindings_for_media_versions(
            transaction=conn, tenant_id="tenant-other", media_item_version_ids=["miv-1"]
        )
        empty = repo.bindings_for_media_versions(transaction=conn, tenant_id="tenant-demo", media_item_version_ids=[])
    assert {record.holder_id for record in found} == {"order-a", "order-b"}
    assert all(record.media_item_version_id == "miv-1" for record in found)
    assert other_tenant == []
    assert empty == []


def test_update_binding_repoints_to_new_version(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    with engine.begin() as conn:
        repo.create_binding(transaction=conn, record=_binding(version_id="miv-1", key="idem-1"))
        repo.update_binding(
            transaction=conn,
            record=_binding(version_id="miv-2", key="idem-2"),
        )
    with engine.begin() as conn:
        found = repo.binding_by_holder(
            transaction=conn,
            tenant_id="tenant-demo",
            holder_type="Order",
            holder_id="order-1",
            property_name="sourceDocument",
        )
    assert found is not None and found.media_item_version_id == "miv-2" and found.idempotency_key == "idem-2"
