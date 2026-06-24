from __future__ import annotations

from pathlib import Path

import pytest
from foundry_lite.application.ports.media_repository import (
    MediaItemRecord,
    MediaItemVersionRecord,
    MediaSetRecord,
    MediaTransactionRecord,
)
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.repositories import SqlAlchemyMediaRepository
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine


@pytest.fixture(params=["sqlalchemy", "postgres"])
def media_repo(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[SqlAlchemyMediaRepository, Engine]:
    if request.param == "sqlalchemy":
        engine = create_engine(f"sqlite:///{tmp_path / 'media.db'}", future=True)
        db.create_database(engine)
        return SqlAlchemyMediaRepository(engine), engine
    postgres_fixture = request.getfixturevalue("postgres_fixture")
    return SqlAlchemyMediaRepository(postgres_fixture.engine), postgres_fixture.engine


def _media_set(media_set_id: str = "ms-1", *, name: str = "contracts") -> MediaSetRecord:
    return MediaSetRecord(
        media_set_id=media_set_id,
        tenant_id="tenant-demo",
        namespace="legal",
        name=name,
        schema_type="document",
        primary_format="pdf",
        allowed_input_formats=("pdf",),
        transaction_policy="transactional",
        storage_profile="local",
        processing_profile="local",
        classification="confidential",
        retention_policy_id=None,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def _transaction(
    transaction_id: str = "mtx-1", *, status: str = "OPEN", idempotency_key: str = "idem-1"
) -> MediaTransactionRecord:
    return MediaTransactionRecord(
        media_transaction_id=transaction_id,
        tenant_id="tenant-demo",
        media_set_id="ms-1",
        status=status,
        mode="APPEND",
        idempotency_key=idempotency_key,
        request_fingerprint="fp-1",
        opened_at="2026-06-23T00:00:00Z",
    )


def _item(item_id: str = "mi-1", *, head_version_id: str | None = None) -> MediaItemRecord:
    return MediaItemRecord(
        media_item_id=item_id,
        tenant_id="tenant-demo",
        media_set_id="ms-1",
        logical_path="/contracts/acme.pdf",
        head_version_id=head_version_id,
        is_deleted=False,
        created_at="2026-06-23T00:00:00Z",
        updated_at="2026-06-23T00:00:00Z",
    )


def _version(
    version_id: str,
    *,
    version_number: int,
    blob_key: str,
    content_hash: str,
    transaction_id: str = "mtx-1",
    status: str = "STAGED",
    created_at: str = "2026-06-23T00:00:00Z",
) -> MediaItemVersionRecord:
    return MediaItemVersionRecord(
        media_item_version_id=version_id,
        tenant_id="tenant-demo",
        media_item_id="mi-1",
        media_transaction_id=transaction_id,
        version_number=version_number,
        blob_key=blob_key,
        content_hash=content_hash,
        byte_size=1024,
        supplied_mime_type="application/pdf",
        sniffed_mime_type="application/pdf",
        schema_type="document",
        format="pdf",
        probe_metadata={"pages": 3},
        security_envelope={"tenantId": "tenant-demo", "classification": "confidential"},
        source_ref=None,
        status=status,
        created_at=created_at,
    )


def test_media_set_create_is_idempotent_and_tenant_scoped(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        fresh = repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
        replay = repo.create_media_set_or_get_existing(transaction=conn, record=_media_set(media_set_id="ms-2"))

    assert fresh is None
    assert replay is not None and replay.media_set_id == "ms-1"
    with engine.begin() as conn:
        assert (
            repo.media_set_by_ref(transaction=conn, tenant_id="tenant-demo", namespace="legal", name="contracts")
            is not None
        )
        assert (
            repo.media_set_by_ref(transaction=conn, tenant_id="tenant-other", namespace="legal", name="contracts")
            is None
        )
        assert {record.media_set_id for record in repo.get_media_sets(transaction=conn, ids=["ms-1"])} == {"ms-1"}


def test_media_transaction_commit_is_cas_and_idempotent(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
        fresh = repo.create_open_transaction(transaction=conn, record=_transaction())
        replay = repo.create_open_transaction(transaction=conn, record=_transaction(transaction_id="mtx-2"))
        committed = repo.commit_transaction(
            transaction=conn, tenant_id="tenant-demo", media_transaction_id="mtx-1", committed_at="2026-06-23T01:00:00Z"
        )
        recommit = repo.commit_transaction(
            transaction=conn, tenant_id="tenant-demo", media_transaction_id="mtx-1", committed_at="2026-06-23T02:00:00Z"
        )

    assert fresh is None
    assert replay is not None and replay.media_transaction_id == "mtx-1"
    assert (
        committed is not None and committed.status == "COMMITTED" and committed.committed_at == "2026-06-23T01:00:00Z"
    )
    # Re-commit is a CAS no-op: the committed_at does not move to the second call.
    assert recommit is not None and recommit.committed_at == "2026-06-23T01:00:00Z"


def test_media_item_head_cas_yields_single_winner(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
        repo.upsert_media_item(transaction=conn, record=_item())
        # Two commits race from the same expected head (None): exactly one wins.
        first = repo.cas_item_head_version(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_id="mi-1",
            expected_head_version_id=None,
            new_head_version_id="miv-1",
            updated_at="2026-06-23T01:00:00Z",
        )
        lost = repo.cas_item_head_version(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_id="mi-1",
            expected_head_version_id=None,
            new_head_version_id="miv-2",
            updated_at="2026-06-23T01:00:01Z",
        )
        # A correct compare-and-set from the new head succeeds.
        advanced = repo.cas_item_head_version(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_id="mi-1",
            expected_head_version_id="miv-1",
            new_head_version_id="miv-2",
            updated_at="2026-06-23T01:00:02Z",
        )

    assert first is not None and first.head_version_id == "miv-1"
    assert lost is None
    assert advanced is not None and advanced.head_version_id == "miv-2"


def test_media_version_insert_dedups_and_commits_with_monotonic_numbers(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
        repo.create_open_transaction(transaction=conn, record=_transaction())
        repo.upsert_media_item(transaction=conn, record=_item())
        assert repo.next_version_number(transaction=conn, media_item_id="mi-1") == 1
        fresh = repo.insert_version(
            transaction=conn, record=_version("miv-1", version_number=1, blob_key="blob-1", content_hash="h1")
        )
        # Same blob (content_hash, byte_size, blob_key) resolves to the existing version.
        replay = repo.insert_version(
            transaction=conn, record=_version("miv-9", version_number=1, blob_key="blob-1", content_hash="h1")
        )
        assert repo.next_version_number(transaction=conn, media_item_id="mi-1") == 2
        committed = repo.commit_staged_versions(
            transaction=conn, tenant_id="tenant-demo", media_transaction_id="mtx-1", committed_at="2026-06-23T01:00:00Z"
        )

    assert fresh is None
    assert replay is not None and replay.media_item_version_id == "miv-1"
    assert [version.media_item_version_id for version in committed] == ["miv-1"]
    assert committed[0].status == "COMMITTED" and committed[0].committed_at == "2026-06-23T01:00:00Z"


def test_fetch_unreachable_staged_versions_finds_aborted_transaction_versions(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
        repo.create_open_transaction(transaction=conn, record=_transaction())
        repo.upsert_media_item(transaction=conn, record=_item())
        repo.insert_version(
            transaction=conn,
            record=_version(
                "miv-1", version_number=1, blob_key="blob-1", content_hash="h1", created_at="2026-06-20T00:00:00Z"
            ),
        )
        repo.abort_transaction(
            transaction=conn,
            tenant_id="tenant-demo",
            media_transaction_id="mtx-1",
            aborted_at="2026-06-20T01:00:00Z",
            error={"reason": "operator_abort"},
        )
        orphans = repo.fetch_unreachable_staged_versions(
            transaction=conn, tenant_id="tenant-demo", older_than="2026-06-21T00:00:00Z"
        )

    assert [version.media_item_version_id for version in orphans] == ["miv-1"]


def _binding_values(version_id: str, *, binding_id: str = "mrb-1") -> dict[str, object]:
    return {
        "id": binding_id,
        "tenant_id": "tenant-demo",
        "holder_type": "Order",
        "holder_id": "order-1",
        "property_name": "doc",
        "media_set_id": "ms-1",
        "media_item_id": "mi-1",
        "media_item_version_id": version_id,
        "logical_path": "/contracts/acme.pdf",
        "content_hash": "h1",
        "security_envelope": {"tenantId": "tenant-demo", "classification": "confidential"},
        "idempotency_key": "bind-1",
        "created_at": "2026-06-23T00:00:00Z",
        "updated_at": "2026-06-23T00:00:00Z",
    }


def _seed_version(repo: SqlAlchemyMediaRepository, conn: object, version_id: str, *, version_number: int) -> None:
    repo.create_media_set_or_get_existing(transaction=conn, record=_media_set())
    repo.create_open_transaction(transaction=conn, record=_transaction())
    repo.upsert_media_item(transaction=conn, record=_item())
    repo.insert_version(
        transaction=conn,
        record=_version(version_id, version_number=version_number, blob_key=f"blob-{version_id}", content_hash="h1"),
    )


def test_mark_and_fetch_retention_marked_versions_is_grace_bounded(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        _seed_version(repo, conn, "miv-1", version_number=1)
        marked = repo.mark_versions_for_retention(
            transaction=conn,
            tenant_id="tenant-demo",
            media_item_version_ids=["miv-1"],
            marked_at="2026-06-01T00:00:00Z",
        )
        before_grace = repo.fetch_retention_marked_versions(
            transaction=conn, tenant_id="tenant-demo", marked_before="2026-05-01T00:00:00Z"
        )
        past_grace = repo.fetch_retention_marked_versions(
            transaction=conn, tenant_id="tenant-demo", marked_before="2026-06-10T00:00:00Z"
        )

    assert marked == 1
    assert before_grace == []
    assert [version.media_item_version_id for version in past_grace] == ["miv-1"]


def test_legal_hold_toggle_and_reference_reachability(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        _seed_version(repo, conn, "miv-1", version_number=1)
        held = repo.set_version_legal_hold(
            transaction=conn, tenant_id="tenant-demo", media_item_version_id="miv-1", has_legal_hold=True
        )
        unreachable = repo.has_reference_binding(
            transaction=conn, tenant_id="tenant-demo", media_item_version_id="miv-1"
        )
        conn.execute(db.media_reference_bindings.insert().values(**_binding_values("miv-1")))
        reachable = repo.has_reference_binding(transaction=conn, tenant_id="tenant-demo", media_item_version_id="miv-1")

    assert held is not None and held.has_legal_hold is True
    assert unreachable is False
    assert reachable is True


def test_purge_version_removes_version_and_derived_footprint(
    media_repo: tuple[SqlAlchemyMediaRepository, Engine],
) -> None:
    repo, engine = media_repo
    with engine.begin() as conn:
        _seed_version(repo, conn, "miv-1", version_number=1)
        conn.execute(
            db.media_access_caches.insert().values(
                id="mac-1",
                tenant_id="tenant-demo",
                source_media_item_version_id="miv-1",
                access_pattern="thumbnail",
                access_pattern_spec_hash="spec",
                persistence_policy="persist",
                source_content_hash="h1",
                blob_key="cache/miv-1",
                content_hash="h1",
                byte_size=1,
                mime_type="image/png",
                created_at="2026-06-23T00:00:00Z",
            )
        )
        repo.purge_version(transaction=conn, tenant_id="tenant-demo", media_item_version_id="miv-1")
        gone = repo.media_item_version_by_id(transaction=conn, tenant_id="tenant-demo", media_item_version_id="miv-1")
        caches = conn.execute(
            db.media_access_caches.select().where(db.media_access_caches.c.source_media_item_version_id == "miv-1")
        ).all()

    assert gone is None
    assert caches == []
