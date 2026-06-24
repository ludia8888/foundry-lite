"""L7 media retention/purge engine invariants (mark -> grace -> sweep, fail-safe protection).

Assembles the media stack over SQLite + local storage with a fake runtime to prove the
purge engine: a version reachable by a MediaReference or under legal hold is NEVER purged
(invariant ``retention_never_purges_reachable_or_legal_hold_version``), an unreachable
un-held version past its grace IS purged with its whole derived footprint and its blob, a
version not yet past grace is left alone, and every purge is audited.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from foundry_lite.application.ports import TransactionContext
from foundry_lite.application.ports.media_derivative_repository import ContentUnitRecord, MediaDerivativeRecord
from foundry_lite.application.primitives import _new_id, _now
from foundry_lite.application.services.media.binding import MediaReferenceBindingService
from foundry_lite.application.services.media.catalog import MediaCatalogService, MediaSetSpec
from foundry_lite.application.services.media.retention import MediaRetentionService
from foundry_lite.application.services.media.transactions import MediaTransactionService
from foundry_lite.application.services.media.uploads import MediaUploadInput, MediaUploadService
from foundry_lite.domain.context import RequestContext
from foundry_lite.infrastructure import schema as db
from foundry_lite.infrastructure.adapters.local_media_storage import LocalMediaStorageAdapter
from foundry_lite.infrastructure.repositories import (
    SqlAlchemyMediaDerivativeRepository,
    SqlAlchemyMediaReferenceBindingRepository,
    SqlAlchemyMediaRepository,
)
from sqlalchemy import create_engine, insert, select


class _FakeRuntime:
    def __init__(self) -> None:
        self.audits: list[str] = []

    def _audit(self, conn: TransactionContext, ctx: RequestContext, *, event_type: str, **_: object) -> None:
        self.audits.append(event_type)

    def _outbox(self, conn: TransactionContext, ctx: RequestContext, event_type: str, *_: object, **__: object) -> str:
        return event_type


@dataclass(frozen=True)
class _Env:
    ctx: RequestContext
    engine: object
    repo: SqlAlchemyMediaRepository
    derivative_repo: SqlAlchemyMediaDerivativeRepository
    storage: LocalMediaStorageAdapter
    retention: MediaRetentionService
    binding: MediaReferenceBindingService
    upload: MediaUploadService
    transaction: MediaTransactionService
    media_set_id: str
    runtime: _FakeRuntime = field(default_factory=_FakeRuntime)

    def commit_version(self, *, logical_path: str, body: bytes, idem: str) -> str:
        tx = self.transaction.open(self.ctx, media_set_id=self.media_set_id, idempotency_key=idem)
        session = self.upload.initiate(
            self.ctx, media_set_id=self.media_set_id, logical_path=logical_path, supplied_mime_type="application/pdf"
        )
        staged = self.upload.complete(
            self.ctx,
            inputs=MediaUploadInput(
                media_set_id=self.media_set_id,
                media_transaction_id=tx,
                logical_path=logical_path,
                supplied_mime_type="application/pdf",
                schema_type="document",
                format="pdf",
                security_envelope={"tenantId": self.ctx.tenant_id, "classification": "confidential"},
            ),
            upload=session,
            source=io.BytesIO(body),
        )
        self.transaction.commit(self.ctx, media_transaction_id=tx)
        return staged.media_item_version_id

    def attach_derived(self, version_id: str) -> tuple[str, str]:
        """Attach one COMMITTED derivative + content unit to a version (purge footprint)."""
        envelope = {"tenantId": self.ctx.tenant_id, "classification": "confidential"}
        derivative_id = _new_id("mder")
        unit_id = _new_id("cu")
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            self.derivative_repo.create_derivative_or_get_existing(
                transaction=conn,
                record=MediaDerivativeRecord(
                    media_derivative_id=derivative_id,
                    tenant_id=self.ctx.tenant_id,
                    source_media_item_version_id=version_id,
                    derivative_kind="pdf_text",
                    processor_spec_hash="spec",
                    processor_name="pdf",
                    processor_version="1",
                    model_name=None,
                    model_version="",
                    params_hash="spec",
                    security_envelope=envelope,
                    status="COMMITTED",
                    created_at=_now(),
                ),
            )
            self.derivative_repo.insert_content_units(
                transaction=conn,
                records=[
                    ContentUnitRecord(
                        content_unit_id=unit_id,
                        tenant_id=self.ctx.tenant_id,
                        source_media_item_version_id=version_id,
                        derivative_id=derivative_id,
                        unit_kind="page",
                        ordinal=0,
                        text="x",
                        text_hash="h",
                        chunk_spec_hash="spec",
                        security_envelope=envelope,
                        created_at=_now(),
                    )
                ],
            )
            conn.execute(
                insert(db.media_access_caches).values(
                    id=_new_id("mac"),
                    tenant_id=self.ctx.tenant_id,
                    source_media_item_version_id=version_id,
                    access_pattern="thumbnail",
                    access_pattern_spec_hash="spec",
                    persistence_policy="persist",
                    source_content_hash="h",
                    blob_key=f"cache/{version_id}",
                    content_hash="h",
                    byte_size=1,
                    mime_type="image/png",
                    created_at=_now(),
                )
            )
        return derivative_id, unit_id

    def blob_key(self, version_id: str) -> str:
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            version = self.repo.media_item_version_by_id(
                transaction=conn, tenant_id=self.ctx.tenant_id, media_item_version_id=version_id
            )
        assert version is not None
        return version.blob_key

    def version_present(self, version_id: str) -> bool:
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            version = self.repo.media_item_version_by_id(
                transaction=conn, tenant_id=self.ctx.tenant_id, media_item_version_id=version_id
            )
        return version is not None

    def derived_count(self, version_id: str) -> int:
        with self.engine.begin() as conn:  # type: ignore[attr-defined]
            total = 0
            for table in (db.media_derivatives, db.content_units, db.media_access_caches):
                rows = conn.execute(select(table.c.id).where(table.c.source_media_item_version_id == version_id)).all()
                total += len(rows)
        return total


@pytest.fixture
def env(tmp_path: Path) -> _Env:
    engine = create_engine(f"sqlite:///{tmp_path / 'l7.db'}", future=True)
    db.create_database(engine)
    repo = SqlAlchemyMediaRepository(engine)
    derivative_repo = SqlAlchemyMediaDerivativeRepository(engine)
    binding_repo = SqlAlchemyMediaReferenceBindingRepository(engine)
    storage = LocalMediaStorageAdapter(tmp_path / "media")
    runtime = _FakeRuntime()
    catalog = MediaCatalogService(engine=engine, media_repository=repo)
    catalog.bind_collaborators({"runtime_service": runtime})
    upload = MediaUploadService(engine=engine, media_repository=repo, media_storage=storage)
    transaction = MediaTransactionService(engine=engine, media_repository=repo, media_storage=storage)
    transaction.bind_collaborators({"runtime_service": runtime})
    binding = MediaReferenceBindingService(
        engine=engine, media_repository=repo, media_reference_binding_repository=binding_repo
    )
    binding.bind_collaborators({"runtime_service": runtime})
    retention = MediaRetentionService(engine=engine, media_repository=repo, media_storage=storage)
    retention.bind_collaborators({"runtime_service": runtime})
    ctx = RequestContext()
    media_set = catalog.create_media_set(
        ctx,
        MediaSetSpec(
            namespace="legal",
            name="contracts",
            schema_type="document",
            primary_format="pdf",
            allowed_input_formats=("pdf",),
            classification="confidential",
        ),
    )
    return _Env(
        ctx=ctx,
        engine=engine,
        repo=repo,
        derivative_repo=derivative_repo,
        storage=storage,
        retention=retention,
        binding=binding,
        upload=upload,
        transaction=transaction,
        media_set_id=media_set.media_set_id,
        runtime=runtime,
    )


def test_media_retention_skips_reachable_or_legal_hold(env: _Env) -> None:
    # A reachable (bound), B legal hold, C unreachable + un-held.
    a = env.commit_version(logical_path="/a.pdf", body=b"%PDF-1.4 a", idem="ta")
    b = env.commit_version(logical_path="/b.pdf", body=b"%PDF-1.4 b", idem="tb")
    c = env.commit_version(logical_path="/c.pdf", body=b"%PDF-1.4 c", idem="tc")
    env.attach_derived(c)
    c_blob = env.blob_key(c)

    env.binding.bind(
        env.ctx,
        holder_type="Order",
        holder_id="order-1",
        property_name="doc",
        media_item_version_id=a,
        idempotency_key="bind-a",
    )
    env.retention.place_legal_hold(env.ctx, media_item_version_id=b)

    marked_at = datetime(2026, 6, 1, tzinfo=datetime.now().astimezone().tzinfo)
    env.retention.mark_media_versions_for_retention(env.ctx, media_item_version_ids=[a, b, c], now=marked_at)

    summary = env.retention.purge_marked_media_versions(
        env.ctx, now=marked_at + timedelta(days=30), grace=timedelta(days=7)
    )

    assert summary.purged_version_ids == (c,)
    assert summary.skipped_reachable_version_ids == (a,)
    assert summary.skipped_legal_hold_version_ids == (b,)
    # A (reachable) and B (held) survive untouched.
    assert env.version_present(a)
    assert env.version_present(b)
    # C is physically gone: version row + derived footprint + blob.
    assert not env.version_present(c)
    assert env.derived_count(c) == 0
    assert not env.storage.stat(c_blob).is_present
    # Purging C did not touch A/B's blobs.
    assert env.storage.stat(env.blob_key(a)).is_present
    assert env.storage.stat(env.blob_key(b)).is_present
    assert "media.retention.purged" in env.runtime.audits


def test_retention_does_not_purge_before_grace_elapsed(env: _Env) -> None:
    c = env.commit_version(logical_path="/c.pdf", body=b"%PDF-1.4 c", idem="tc")
    marked_at = datetime(2026, 6, 1, tzinfo=datetime.now().astimezone().tzinfo)
    env.retention.mark_media_versions_for_retention(env.ctx, media_item_version_ids=[c], now=marked_at)

    # Only 3 days elapsed against a 7-day grace: not yet sweepable.
    summary = env.retention.purge_marked_media_versions(
        env.ctx, now=marked_at + timedelta(days=3), grace=timedelta(days=7)
    )
    assert summary.purged_version_ids == ()
    assert env.version_present(c)


def test_unmarked_version_is_never_swept(env: _Env) -> None:
    c = env.commit_version(logical_path="/c.pdf", body=b"%PDF-1.4 c", idem="tc")
    now = datetime(2026, 6, 30, tzinfo=datetime.now().astimezone().tzinfo)
    summary = env.retention.purge_marked_media_versions(env.ctx, now=now, grace=timedelta(days=7))
    assert summary.purged_version_ids == ()
    assert env.version_present(c)


def test_purge_audits_the_sweep(env: _Env) -> None:
    c = env.commit_version(logical_path="/c.pdf", body=b"%PDF-1.4 c", idem="tc")
    marked_at = datetime(2026, 6, 1, tzinfo=datetime.now().astimezone().tzinfo)
    env.runtime.audits.clear()
    env.retention.mark_media_versions_for_retention(env.ctx, media_item_version_ids=[c], now=marked_at)
    assert "media.retention.marked" in env.runtime.audits
    env.retention.purge_marked_media_versions(env.ctx, now=marked_at + timedelta(days=30), grace=timedelta(days=7))
    assert env.runtime.audits.count("media.retention.purged") == 1
