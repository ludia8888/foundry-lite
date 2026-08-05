from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from foundry_lite.application.ports.media_reference_binding_repository import (
    AttachmentHolderAssociationRecord,
    MediaReferenceBindingRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaReferenceBindingRepository
from sqlalchemy import create_engine, insert
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


def test_delete_bindings_replaces_direct_array_and_struct_property_only(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    with engine.begin() as conn:
        for binding_id, property_name in (
            ("mrb-direct", "attachments"),
            ("mrb-array", "attachments[0]"),
            ("mrb-struct", "attachments.receipt"),
            ("mrb-other", "otherAttachment"),
        ):
            repo.create_binding(
                transaction=conn,
                record=_binding_at(
                    binding_id=binding_id,
                    holder_id="order-1",
                    version_id=f"miv-{binding_id}",
                    property_name=property_name,
                ),
            )
        removed = repo.delete_bindings_by_holder_property(
            transaction=conn,
            tenant_id="tenant-demo",
            holder_type="Order",
            holder_id="order-1",
            property_name="attachments",
        )

    with engine.begin() as conn:
        remaining = repo.binding_by_holder(
            transaction=conn,
            tenant_id="tenant-demo",
            holder_type="Order",
            holder_id="order-1",
            property_name="otherAttachment",
        )
    assert removed == 3
    assert remaining is not None


def test_delete_bindings_by_holder_removes_all_properties_for_only_that_object(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    with engine.begin() as conn:
        for binding_id, holder_id, property_name in (
            ("mrb-a", "order-1", "receipt"),
            ("mrb-b", "order-1", "photos[0]"),
            ("mrb-c", "order-2", "receipt"),
        ):
            repo.create_binding(
                transaction=conn,
                record=_binding_at(
                    binding_id=binding_id,
                    holder_id=holder_id,
                    version_id=f"miv-{binding_id}",
                    property_name=property_name,
                ),
            )
        removed = repo.delete_bindings_by_holder(
            transaction=conn,
            tenant_id="tenant-demo",
            holder_type="Order",
            holder_id="order-1",
        )
        remaining = repo.bindings_for_media_versions(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_version_ids=["miv-mrb-a", "miv-mrb-b", "miv-mrb-c"],
        )
    assert removed == 2
    assert [(row.holder_id, row.property_name) for row in remaining] == [("order-2", "receipt")]


def test_attachment_holder_limit_counts_lifetime_associations_after_live_bindings_are_removed(
    binding_repo: tuple[SqlAlchemyMediaReferenceBindingRepository, Engine],
) -> None:
    repo, engine = binding_repo
    version_id = "miv-lifetime-limit"
    _seed_media_version(engine, version_id)
    with engine.begin() as conn:
        results = [
            repo.reserve_attachment_holder(
                transaction=conn,
                record=_association(version_id, index),
                max_holders=10,
            )
            for index in range(10)
        ]
        replay = repo.reserve_attachment_holder(
            transaction=conn,
            record=_association(version_id, 0),
            max_holders=10,
        )
        denied = repo.reserve_attachment_holder(
            transaction=conn,
            record=_association(version_id, 10),
            max_holders=10,
        )
        associations = repo.attachment_associations_for_media_version(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_version_id=version_id,
        )
    assert [result.holder_count for result in results] == list(range(1, 11))
    assert replay.is_reserved and replay.is_existing and replay.holder_count == 10
    assert not denied.is_reserved and denied.holder_count == 10
    assert len(associations) == 10


def test_postgres_attachment_holder_reservation_has_one_tenth_slot_winner(postgres_fixture) -> None:
    repo = SqlAlchemyMediaReferenceBindingRepository(postgres_fixture.engine)
    version_id = "miv-concurrent-lifetime-limit"
    _seed_media_version(postgres_fixture.engine, version_id)
    with postgres_fixture.engine.begin() as conn:
        for index in range(9):
            result = repo.reserve_attachment_holder(
                transaction=conn,
                record=_association(version_id, index),
                max_holders=10,
            )
            assert result.is_reserved

    def reserve(index: int) -> bool:
        with postgres_fixture.engine.begin() as conn:
            return repo.reserve_attachment_holder(
                transaction=conn,
                record=_association(version_id, index),
                max_holders=10,
            ).is_reserved

    with ThreadPoolExecutor(max_workers=2) as executor:
        winners = list(executor.map(reserve, (9, 10)))
    assert sorted(winners) == [False, True]


def _association(version_id: str, index: int) -> AttachmentHolderAssociationRecord:
    return AttachmentHolderAssociationRecord(
        association_id=f"mattachassoc-{version_id}-{index}",
        tenant_id="tenant-demo",
        media_item_version_id=version_id,
        holder_type="Order",
        holder_id=f"order-{index}",
        first_action_run_id=f"action-run-{index}",
        created_at=f"2026-08-04T00:00:{index:02d}Z",
    )


def _seed_media_version(engine: Engine, version_id: str) -> None:
    with engine.begin() as conn:
        conn.execute(
            insert(db.media_item_versions).values(
                id=version_id,
                tenant_id="tenant-demo",
                media_item_id=f"mi-{version_id}",
                media_transaction_id=f"mtx-{version_id}",
                version_number=1,
                blob_key=f"blob/{version_id}",
                content_hash=f"hash-{version_id}",
                byte_size=1,
                supplied_mime_type="application/pdf",
                sniffed_mime_type="application/pdf",
                schema_type="document",
                format="pdf",
                probe_metadata={},
                security_envelope={"tenantId": "tenant-demo", "classification": "internal"},
                source_ref=None,
                status="COMMITTED",
                retention_marked_at=None,
                legal_hold=False,
                created_at="2026-08-04T00:00:00Z",
                committed_at="2026-08-04T00:00:00Z",
            )
        )
